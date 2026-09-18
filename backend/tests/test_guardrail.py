"""护栏拦截统计的测试。

这些 case 不是假想的：都是 2026-09-18 对 glm-4-flash 实测复现过的幻觉输出。
护栏本身早就有了，这里测的是「拦截了什么」能不能被准确地数出来并交给前端展示——
数错了比不展示更糟，那等于对店长撒谎。
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm, serializers  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    os.environ.pop("GLM_API_KEY", None)
    return TestClient(app)


def test_counts_fabricated_pin():
    """实测 case：「E05 周六请假，周末早班多留一个收银」→ 模型顺带编一条 E06 固定排班。"""
    raw = {
        "action": "generate",
        "temp_leaves": [{"employee_id": "E05", "days": ["六"]}],
        "pins": [{"employee_id": "E06", "day": "六", "shift": "早班"}],
        "min_staff_override": {"六|早班": 1},
    }
    out = llm._sanitize(raw, "下周正常排班，E05 周六请假，周末早班多留一个收银", "llm", model="glm-4-flash")
    g = out.guardrail
    assert out.pins == [] and out.min_staff_override == {}
    assert [t.employee_id for t in out.temp_leaves] == ["E05"]     # 真提到的人必须留下
    assert g.triggered is True
    assert (g.dropped_pins, g.dropped_min_staff) == (1, 1)
    assert g.summary == "已丢弃 2 条模型自造约束：1 条固定排班、1 条无效人数下限"
    assert out.action == "generate"                                # 不能因为护栏触发就退化成澄清


def test_counts_illegal_employee_id_without_asking_the_manager():
    """实测 case：「帮我排下周的班」→ 模型编出 E001/E002 的 pins。

    店长根本没提过这两个工号，反问「E001 是谁」只会让人以为系统坏了，
    所以这类必须静默丢弃 + 记账，而不是转成澄清问题。
    """
    raw = {
        "action": "generate",
        "pins": [
            {"employee_id": "E001", "day": "一", "shift": "早班"},
            {"employee_id": "E002", "day": "一", "shift": "晚班"},
        ],
    }
    out = llm._sanitize(raw, "帮我排下周的班", "llm", model="glm-4-flash")
    assert out.action == "generate" and out.clarification_needed == []
    assert out.pins == []
    assert out.guardrail.dropped_pins == 2
    assert out.guardrail.invalid_employee_ids == ["E001", "E002"]


def test_user_written_illegal_id_still_asks():
    """与上一条的分界线：店长自己写了 E99，那就该反问，而不是当幻觉丢掉。"""
    out = llm._sanitize({"action": "adjust", "temp_leaves": [{"employee_id": "E99", "days": ["一"]}]}, "E99 周一请假", "llm")
    assert out.action == "unknown"
    assert any("E99" in c for c in out.clarification_needed)
    assert out.guardrail.invalid_employee_ids == ["E99"]


def test_counts_exclude_and_forbid():
    raw = {
        "action": "generate",
        "exclude_employees": ["E03"],
        "forbids": [{"employee_id": "E09", "day": "一", "shift": "早班"}],
    }
    out = llm._sanitize(raw, "下周正常排班", "llm", model="glm-4-flash")
    g = out.guardrail
    assert (g.dropped_excludes, g.dropped_forbids) == (1, 1)
    assert "整周排除" in g.summary and "禁止排班" in g.summary


def test_silent_when_model_behaves():
    out = llm._sanitize({"action": "generate", "pins": [{"employee_id": "E01", "day": "六", "shift": "早班"}]},
                        "周六早班把 E01 排上", "llm", model="glm-4.5-flash")
    g = out.guardrail
    assert g.triggered is False and g.summary == ""
    assert (g.dropped_pins, g.dropped_forbids, g.dropped_excludes, g.dropped_min_staff) == (0, 0, 0, 0)
    assert out.model_used == "glm-4.5-flash"


def test_degraded_parse_reports_no_model():
    out = llm.fallback_parse("E06 周四家里有事来不了")
    assert out.model_used is None and out.guardrail.triggered is False


def test_serializer_exposes_contract_shape():
    out = llm._sanitize({"action": "generate", "pins": [{"employee_id": "E06", "day": "六", "shift": "早班"}]},
                        "下周正常排班", "llm", model="glm-4-flash")
    payload = serializers.intent_out(out)
    assert payload["model_used"] == "glm-4-flash"
    assert set(payload["guardrail"]) == {
        "triggered", "dropped_pins", "dropped_forbids", "dropped_excludes",
        "dropped_min_staff", "invalid_employee_ids", "summary",
    }
    assert payload["guardrail"]["dropped_pins"] == 1


def test_generate_response_always_carries_guardrail(client):
    """哪条链路都要带 guardrail，前端不必对降级/正常两种响应做特判。"""
    g = client.post("/api/generate", json={"instruction": ""}).json()["intent"]["guardrail"]
    assert g["triggered"] is False and g["summary"] == ""
    clarify = client.post("/api/generate", json={"instruction": "小王明天来不了"}).json()["intent"]
    assert "guardrail" in clarify and clarify["model_used"] is None
