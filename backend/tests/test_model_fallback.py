"""慢模型超时后的优雅降级测试（离线，不打网络）。

这组测试锁的是一个线上真实踩到的问题：把默认模型换成 glm-4.5-flash 之后，
它的单次耗时波动极大（线上实测 24s / 28s / 34s，也出现过打满 75s 预算）。
打满预算的那一次直接掉到规则解析，于是用户选了「最准」的模型，偶发拿到的却是
**最差**的解析结果，界面上只写一句「语言模型暂不可用」，无从判断发生了什么。

所以现在的行为是三级降级：慢模型 → 最快的模型 → 规则解析。
这里断言的正是这条链路，以及「换过模型必须让用户看见」。
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm, models_registry  # noqa: E402

FAST = models_registry.FAST_FALLBACK_TEXT_MODEL
SLOW = "glm-4.5-flash"


# ---------- fallback_model_for ----------


def test_slow_model_falls_back_to_fastest():
    assert models_registry.fallback_model_for(SLOW, 12.0) == FAST


def test_fast_model_has_no_fallback():
    """最快的模型自己超时，再换一次没有意义，只会让用户多等一轮。"""
    assert models_registry.fallback_model_for(FAST, 12.0) is None


def test_unknown_model_has_no_fallback():
    assert models_registry.fallback_model_for(None, 12.0) is None
    assert models_registry.fallback_model_for("not-a-model", 12.0) is None


# ---------- _chat_text 的兜底行为 ----------


class _Recorder:
    """按调用顺序模拟：第一次超时，第二次成功。"""

    def __init__(self, fail_first: bool, exc: Exception | None = None):
        self.models: list[str] = []
        self.fail_first = fail_first
        self.exc = exc or httpx.ReadTimeout("timeout")

    def install(self, monkeypatch):
        recorder = self

        async def fake_chat(messages, *, temperature, json_mode, max_tokens, model=None, timeout=None):
            recorder.models.append(model)
            if recorder.fail_first and len(recorder.models) == 1:
                raise recorder.exc
            return "{}"

        monkeypatch.setattr(llm, "_chat", fake_chat)
        monkeypatch.setenv("GLM_API_KEY", "test-key")
        return recorder


def _chat_text(model):
    return asyncio.run(
        llm._chat_text(
            [{"role": "user", "content": "x"}],
            temperature=0.0,
            json_mode=True,
            max_tokens=900,
            model=model,
        )
    )


def test_timeout_retries_with_fast_model(monkeypatch):
    rec = _Recorder(fail_first=True).install(monkeypatch)
    content, answered_by = _chat_text(SLOW)
    assert content == "{}"
    assert answered_by == FAST
    assert rec.models == [SLOW, FAST], "超时后应当只换模型重试一次"


def test_success_does_not_trigger_fallback(monkeypatch):
    rec = _Recorder(fail_first=False).install(monkeypatch)
    _, answered_by = _chat_text(SLOW)
    assert answered_by == SLOW
    assert rec.models == [SLOW], "没超时就不该多打一次上游请求"


def test_non_timeout_error_is_not_retried_with_other_model(monkeypatch):
    """4xx 换个模型也一样失败，重试只是白等——必须原样抛出去走规则解析。"""
    err = httpx.HTTPStatusError("bad request", request=None, response=None)  # type: ignore[arg-type]
    rec = _Recorder(fail_first=True, exc=err).install(monkeypatch)
    with pytest.raises(httpx.HTTPStatusError):
        _chat_text(SLOW)
    assert rec.models == [SLOW]


def test_fast_model_timeout_propagates(monkeypatch):
    """最快的模型超时无处可退，异常必须冒上去，由 parse_intent 落到规则解析。"""
    rec = _Recorder(fail_first=True).install(monkeypatch)
    with pytest.raises(httpx.ReadTimeout):
        _chat_text(FAST)
    assert rec.models == [FAST]


# ---------- 对外可见性 ----------


def test_parse_intent_reports_fallback_model(monkeypatch):
    """换过模型必须在 intent 上留痕，否则用户会以为自己选的模型一直在生效。"""
    rec = _Recorder(fail_first=True)

    async def fake_chat(messages, *, temperature, json_mode, max_tokens, model=None, timeout=None):
        rec.models.append(model)
        if len(rec.models) == 1:
            raise httpx.ReadTimeout("timeout")
        return '{"action":"generate","temp_leaves":[],"pins":[],"forbids":[]}'

    monkeypatch.setattr(llm, "_chat", fake_chat)
    monkeypatch.setenv("GLM_API_KEY", "test-key")

    intent = asyncio.run(llm.parse_intent("下周正常排班", model=SLOW))
    assert intent.parse_source == "llm", "退到快模型后仍然是 LLM 解析，不该被记成规则降级"
    assert intent.model_used == FAST
    assert intent.model_fallback_from == SLOW


def test_parse_intent_falls_back_to_rules_when_both_fail(monkeypatch):
    async def always_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(llm, "_chat", always_timeout)
    monkeypatch.setenv("GLM_API_KEY", "test-key")

    intent = asyncio.run(llm.parse_intent("下周正常排班", model=SLOW))
    assert intent.parse_source == "fallback_rule"
    assert intent.model_used is None
    assert intent.model_fallback_from is None


def test_serializer_surfaces_fallback_reason():
    from app.models import ScheduleRequest
    from app.serializers import intent_out

    intent = ScheduleRequest(
        action="generate", raw_text="下周正常排班", parse_source="llm",
        model_used=FAST, model_fallback_from=SLOW,
    )
    out = intent_out(intent)
    # 换模型不是「模型不可用」，所以不该把 degraded 置真去触发那套告警式 UI
    assert out["degraded"] is False
    assert out["model_fallback_from"] == SLOW
    assert SLOW in out["degrade_reason"] and FAST in out["degrade_reason"]
