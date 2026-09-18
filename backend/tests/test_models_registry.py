"""模型白名单与按请求切换模型的测试（离线，不打网络）。

关注点不是「接口有没有返回」，而是三件会真出事的事：
1. 非白名单模型必须 400 且把可用清单告诉用户，不能默默换成默认模型；
2. `glm-4v-flash` 的 max_tokens 必须被夹到 1024——实测超了整个请求会 1210 参数非法；
3. 不传 model 时行为与加入模型选择之前完全一致（向后兼容）。
"""
from __future__ import annotations

import asyncio
import os
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

    def __init__(self, **kwargs):
        pass

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
    assert captured.last_payload["max_tokens"] == 900


def test_chat_passes_requested_model(captured):
    _chat(model="glm-4.5-flash")
    assert captured.last_payload["model"] == "glm-4.5-flash"
    assert captured.last_payload["max_tokens"] == 900      # 文本模型不设上限，原样透传


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
