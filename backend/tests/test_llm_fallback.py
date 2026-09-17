"""LLM 层的降级与防幻觉测试（全部离线，不依赖网络）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm  # noqa: E402
from app.models import Diagnosis, ScheduleRequest, UnlockOption, ValidationReport  # noqa: E402


# ---------- 兜底解析 ----------


def test_fallback_plain_generate_never_asks_clarification():
    """模型不可用时，「帮我排班」必须照常出方案，不能退化成追问。"""
    r = llm.fallback_parse("帮我排下周一到周日的班")
    assert r.action == "generate"
    assert r.clarification_needed == []
    assert r.notes


def test_fallback_recognizes_leave():
    r = llm.fallback_parse("E06 周四家里有事来不了")
    assert r.action == "adjust"
    assert r.temp_leaves[0].employee_id == "E06"
    assert r.temp_leaves[0].days == ["四"]


def test_fallback_recognizes_pin():
    r = llm.fallback_parse("周六早班让 E01 亲自盯着")
    assert r.action == "adjust"
    assert r.pins[0].model_dump() == {"employee_id": "E01", "day": "六", "shift": "早班"}


def test_fallback_asks_when_person_unknown():
    r = llm.fallback_parse("小王明天来不了")
    assert r.action == "unknown"
    assert r.clarification_needed


# ---------- 模型输出清洗 ----------


def test_sanitize_drops_fake_employee_and_forces_clarification():
    raw = {
        "action": "adjust",
        "temp_leaves": [{"employee_id": "E99", "days": ["三"]}, {"employee_id": "E06", "days": ["四"]}],
    }
    r = llm._sanitize(raw, "…", "llm")
    assert [t.employee_id for t in r.temp_leaves] == ["E06"]
    assert r.action == "unknown"        # 出现无法对应的员工 → 必须澄清
    assert any("E99" in c for c in r.clarification_needed)


def test_sanitize_rejects_illegal_day_and_shift():
    raw = {"action": "adjust", "pins": [{"employee_id": "E01", "day": "八", "shift": "夜班"}]}
    r = llm._sanitize(raw, "…", "llm")
    assert r.pins == []
    assert r.clarification_needed


def test_sanitize_keeps_only_valid_override():
    raw = {"action": "generate", "min_staff_override": {"日|晚班": 7, "下周": 9, "六": 0}}
    r = llm._sanitize(raw, "…", "llm")
    assert r.min_staff_override == {"日|晚班": 7}


# ---------- 解释防幻觉 ----------


def _passed_report() -> ValidationReport:
    return ValidationReport(passed=True, violations=[], rule_results=[])


def test_explanation_drops_lines_contradicting_validator():
    content = "- 9 条硬规则全部通过\n- 周四早班需增加 1 名店长值守员工\n- 偏好满足度较高\n- 工时分布均衡"
    out = llm._clean_explanation(content, _passed_report(), Diagnosis())
    assert out is not None
    assert "需增加" not in out
    assert out.count("\n") == 2


def test_explanation_falls_back_when_all_lines_dropped():
    content = "- 存在违规\n- 人数不足"
    assert llm._clean_explanation(content, _passed_report(), Diagnosis()) is None


def test_explanation_keeps_bottleneck_wording_when_infeasible():
    diag = Diagnosis(infeasible=True, kind="proven_infeasible", bottleneck_rule="R-01")
    content = "- 无可行解，R-01 缺少店长值守资格员工\n- 周一仅 1 名可用\n- 建议临时授权"
    out = llm._clean_explanation(content, None, diag)
    assert out is not None and "R-01" in out and "缺少" in out


def test_template_explanation_covers_infeasible():
    diag = Diagnosis(
        infeasible=True,
        kind="proven_infeasible",
        bottleneck_rule="R-01",
        evidence=["周一：全天仅 1 名具备店长值守资格的员工可排班"],
        unlock_options=[UnlockOption(title="临时授予店长值守资格", detail="…", cost="需店长确认")],
    )
    out = llm.template_explanation(None, ScheduleRequest(), diag)
    assert "R-01" in out and "解锁选项" in out


def test_llm_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    assert llm.llm_enabled() is False
