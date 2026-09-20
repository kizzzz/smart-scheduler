"""模型白名单与按请求切换模型的测试（离线，不打网络）。

关注点不是「接口有没有返回」，而是三件会真出事的事：
1. 非白名单模型必须 400 且把可用清单告诉用户，不能默默换成默认模型；
2. `glm-4v-flash` 的 max_tokens 必须被夹到 1024——实测超了整个请求会 1210 参数非法；
3. 不传 model 时行为与加入模型选择之前完全一致（向后兼容）。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm, models_registry  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    os.environ.pop("GLM_API_KEY", None)
    return TestClient(app)


# ---------- GET /api/models ----------


def test_models_payload_structure(client):
    m = client.get("/api/models").json()
    assert set(m) == {
        "default_text", "default_vision", "text", "vision", "locked", "boundary_note", "unlock_note"
    }
    assert m["default_text"] in [x["id"] for x in m["text"]]
    assert m["default_vision"] in [x["id"] for x in m["vision"]]
    # 默认模型必须唯一，否则前端选择器不知道该预选哪个
    assert [x["id"] for x in m["text"] if x["is_default"]] == [m["default_text"]]
    assert [x["id"] for x in m["vision"] if x["is_default"]] == [m["default_vision"]]
    for entry in m["text"] + m["vision"]:
        assert {"id", "label", "tier", "is_default", "latency_hint", "accuracy_hint", "note"} <= set(entry)
        assert entry["tier"] in {"free", "paid"}
    assert m["boundary_note"] and "不影响排班正确性" in m["boundary_note"]


def test_locked_models_explain_why(client):
    """灰显的模型必须带原因：用户要知道「存在但为何不能选」，否则只会反复试。"""
    locked = client.get("/api/models").json()["locked"]
    assert locked and all(set(x) == {"id", "reason"} and x["reason"] for x in locked)
    ids = [x["id"] for x in locked]
    assert "glm-z1-flash" in ids          # 实测 429 频发，可用性不达标
    assert "glm-4-plus" in ids            # 余额不足 1113


def test_vision_entry_carries_measured_token_cap(client):
    v = next(x for x in client.get("/api/models").json()["vision"] if x["id"] == "glm-4v-flash")
    assert v["max_output_tokens"] == 1024
    assert v["measured"]["cell_accuracy"] == "14/14"


def test_extra_models_env_unlocks_without_code_change(monkeypatch):
    monkeypatch.setenv("GLM_EXTRA_MODELS", "glm-4-plus,glm-4.5v")
    payload = models_registry.payload()
    assert "glm-4-plus" in [x["id"] for x in payload["text"]]
    assert "glm-4.5v" in [x["id"] for x in payload["vision"]]
    assert "glm-4-plus" not in [x["id"] for x in payload["locked"]]
    # 没实测过就不许给 measured，免得前端把猜测当数据展示
    extra = next(x for x in payload["text"] if x["id"] == "glm-4-plus")
    assert "measured" not in extra and extra["tier"] == "paid"


# ---------- POST /api/generate 的 model 入参 ----------


def test_generate_accepts_whitelisted_model(client):
    r = client.post("/api/generate", json={"instruction": "", "model": "glm-4.5-flash"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_generate_rejects_unknown_model(client):
    r = client.post("/api/generate", json={"instruction": "", "model": "gpt-4o"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "gpt-4o" in detail
    for mid in models_registry.text_model_ids():
        assert mid in detail          # 必须把可用清单一起给出


def test_generate_without_model_is_unchanged(client):
    r = client.post("/api/generate", json={"instruction": ""}).json()
    assert r["ok"] is True
    # 无 Key 时走降级，model_used 必须是 null 而不是默认模型名
    assert r["intent"]["model_used"] is None


# ---------- _chat 的 model 透传与 max_tokens 夹取 ----------


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "{}"}}]}


class _FakeClient:
    """记录最后一次请求体，用来断言我们到底给上游发了什么。"""

    last_payload: dict = {}
    last_timeout: float | None = None

    def __init__(self, **kwargs):
        _FakeClient.last_timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.last_payload = json or {}
        return _FakeResponse()


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(llm.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    return _FakeClient


def _chat(model=None, max_tokens=900):
    return asyncio.run(
        llm._chat(
            [{"role": "user", "content": "x"}],
            temperature=0.0,
            json_mode=False,
            max_tokens=max_tokens,
            model=model,
        )
    )


def test_chat_defaults_to_module_model(captured):
    _chat()
    assert captured.last_payload["model"] == llm.GLM_MODEL


def test_chat_passes_requested_model(captured):
    _chat(model="glm-4-flash")
    assert captured.last_payload["model"] == "glm-4-flash"
    assert captured.last_payload["max_tokens"] == 900      # 非推理模型不抬高、也无上限，原样透传


def test_chat_raises_output_budget_for_reasoning_model(captured):
    """推理模型必须拿到更大的输出预算，否则 JSON 会被写到一半截断。

    这条来自线上实测：glm-4.5-flash 按 900 tokens 调用时偶发 finish_reason=length，
    `_extract_json` 拿不到合法 JSON，于是静默掉到规则解析——耗时正常、模型也返回了，
    但解析质量突然变成规则级，日志里当时什么都看不到。
    """
    _chat(model="glm-4.5-flash", max_tokens=900)
    assert captured.last_payload["max_tokens"] == 2048


def test_output_floor_never_breaks_hard_cap():
    """抬高不能突破模型硬上限：glm-4v-flash 超过 1024 会整体参数非法。"""
    assert models_registry.resolve_max_tokens("glm-4v-flash", 2048) == 1024
    assert models_registry.resolve_max_tokens("glm-4.5-flash", 600) == 2048
    assert models_registry.resolve_max_tokens("glm-4.5-flash", 4096) == 4096
    assert models_registry.resolve_max_tokens("glm-4-flash", 900) == 900


def test_chat_clamps_vision_max_tokens(captured):
    """实测：glm-4v-flash 传 >1024 会返回「1210 max_tokens参数非法」，一格都读不出来。"""
    _chat(model="glm-4v-flash", max_tokens=2048)
    assert captured.last_payload["model"] == "glm-4v-flash"
    assert captured.last_payload["max_tokens"] == 1024


def test_vision_call_uses_default_vision_model(captured, monkeypatch):
    monkeypatch.setattr(llm, "_extract_json", lambda _: {"rows": []})
    rows, used = asyncio.run(llm.read_schedule_image("data:image/png;base64,AA=="))
    assert rows == [] and used == models_registry.DEFAULT_VISION_MODEL
    assert captured.last_payload["model"] == models_registry.DEFAULT_VISION_MODEL
    assert captured.last_payload["max_tokens"] == 1024
    # 图片必须以 image_url 形式传，且不能带 response_format（视觉模型不保证支持）
    assert captured.last_payload["messages"][0]["content"][1]["type"] == "image_url"
    assert "response_format" not in captured.last_payload


# ---------- 慢模型的超时预算 ----------
#
# 这组测试来自一次线上实测事故：glm-4.5-flash 实测 ~30s，而 GLM_TIMEOUT 默认 12s，
# 结果用户在界面上选了「最准」的模型，后端每次都超时并静默降级成规则解析——
# 他拿到的解析质量比默认模型更差，而界面上没有任何提示。


def test_timeout_scales_with_measured_latency():
    """慢模型必须拿到更宽的超时预算，否则这个选项永远无法生效。"""
    assert models_registry.timeout_for("glm-4.5-flash", 12.0) == pytest.approx(75.25)
    # 快模型不该被无故放宽：3.7 * 2.5 = 9.25 < 12，取 base
    assert models_registry.timeout_for("glm-4-flash-250414", 12.0) == 12.0
    # 未登记的模型（GLM_EXTRA_MODELS 放开的付费模型）没有实测数据，保持 base
    assert models_registry.timeout_for("glm-4-plus", 12.0) == 12.0
    assert models_registry.timeout_for(None, 12.0) == 12.0


def test_default_text_model_timeout_is_sufficient():
    """默认模型的超时预算必须覆盖它的实测耗时——这是换默认模型时最容易踩的坑。

    默认模型现在是 glm-4.5-flash（实测单次 ~30s）。如果哪天有人把 DEFAULT_TEXT_MODEL
    改成一个慢模型却没有登记 measured.avg_latency_s，timeout_for 会退回 12s 的 base，
    于是**每一次默认请求都超时降级成规则解析**，而界面上看不出任何异常。
    """
    default = models_registry.DEFAULT_TEXT_MODEL
    entry = next(m for m in models_registry.text_models() if m["id"] == default)
    measured = (entry.get("measured") or {}).get("avg_latency_s")
    assert measured, f"默认模型 {default} 必须有实测耗时，否则超时预算无从推导"
    assert models_registry.timeout_for(default, 12.0) >= measured * 2, (
        f"默认模型 {default} 的超时预算不足，会每次超时并静默降级"
    )


def test_edge_timeout_covers_worst_case_generate():
    """边缘反代的超时必须覆盖一整次生成的最坏耗时，否则会「后端还在算、边缘先 504」。

    这条测试是踩过的坑：默认模型换成 glm-4.5-flash 后，Caddy 的 read_timeout 还是 60s，
    线上实测一次生成能到 85s，于是页面拿到 504、后端日志却一切正常，极难排查。
    一次生成跑两趟 LLM，所以预算要按 2× 单次算。
    """
    import re

    caddyfile = (
        pathlib.Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"
    )
    m = re.search(r"read_timeout\s+(\d+)s", caddyfile.read_text(encoding="utf-8"))
    assert m, "Caddyfile 未显式设置 read_timeout，慢模型会被边缘截断"
    edge_budget = int(m.group(1))
    worst_case = models_registry.timeout_for(models_registry.DEFAULT_TEXT_MODEL, 12.0) * 2
    assert edge_budget >= worst_case, (
        f"边缘超时 {edge_budget}s 覆盖不了最坏 {worst_case:.0f}s，慢模型会返回 504"
    )


def test_module_default_follows_registry(monkeypatch):
    """llm.GLM_MODEL 未显式配置时必须跟随注册表默认值，不能各处硬写一份。"""
    monkeypatch.delenv("GLM_MODEL", raising=False)
    assert llm.GLM_MODEL == models_registry.DEFAULT_TEXT_MODEL


def test_chat_uses_model_specific_timeout(captured):
    _chat(model="glm-4.5-flash")
    assert captured.last_timeout == pytest.approx(75.25)
    _chat(model="glm-4-flash-250414")
    assert captured.last_timeout == pytest.approx(llm.GLM_TIMEOUT)


def test_chat_does_not_retry_on_timeout(monkeypatch):
    """超时已经等满整个预算，重试只会把等待翻三倍：75s 的模型会拖成 225s。"""
    calls = {"n": 0}

    class _TimeoutClient(_FakeClient):
        async def post(self, url, json=None, headers=None):
            calls["n"] += 1
            raise llm.httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(llm.httpx, "AsyncClient", _TimeoutClient)
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    with pytest.raises(llm.httpx.TimeoutException):
        _chat(model="glm-4.5-flash")
    assert calls["n"] == 1, "超时不应重试"


def test_chat_still_retries_on_connection_error(monkeypatch):
    """网络抖动（非超时）仍然要重试，否则一次瞬时抖动就整体降级。"""
    calls = {"n": 0}

    class _FlakyClient(_FakeClient):
        async def post(self, url, json=None, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise llm.httpx.ConnectError("reset")
            _FakeClient.last_payload = json or {}
            return _FakeResponse()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _FlakyClient)
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    monkeypatch.setattr(llm.asyncio, "sleep", _noop_sleep)
    _chat()
    assert calls["n"] == 2


async def _noop_sleep(_seconds):
    return None
