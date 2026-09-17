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
    # 原文必须真的提到这两个 ID，否则会先被「反幻觉」护栏拦掉，测不到这里的分支
    r = llm._sanitize(raw, "E99 周三请假，E06 周四也来不了", "llm")
    assert [t.employee_id for t in r.temp_leaves] == ["E06"]
    assert r.action == "unknown"        # 出现无法对应的员工 → 必须澄清
    assert any("E99" in c for c in r.clarification_needed)


def test_sanitize_rejects_illegal_day_and_shift():
    raw = {"action": "adjust", "pins": [{"employee_id": "E01", "day": "八", "shift": "夜班"}]}
    r = llm._sanitize(raw, "把 E01 固定到那个班", "llm")
    assert r.pins == []
    assert r.clarification_needed


def test_sanitize_keeps_only_valid_override():
    raw = {"action": "generate", "min_staff_override": {"日|晚班": 7, "下周": 9, "六": 0}}
    r = llm._sanitize(raw, "周日晚班加到 7 个人", "llm")
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


# ---------------------------------------------------------------------------
# 反幻觉护栏：模型不得凭空补出用户没提到的人 / 无效的人数下限
# 线上实测：指令「下周正常排班，E05 周六请假，周末早班多留一个收银」时，
# glm-4-flash 会顺带编出「E01 固定在周六早班」和「周六早班 ≥ 1 人」。
# ---------------------------------------------------------------------------


def test_mentioned_employee_ids_variants():
    assert llm.mentioned_employee_ids("E05 周六请假") == {"E05"}
    assert llm.mentioned_employee_ids("e5 和 E12 都不来") == {"E05", "E12"}
    assert llm.mentioned_employee_ids("下周正常排班") == set()


def test_drops_pin_for_employee_never_mentioned():
    raw = {
        "action": "generate",
        "temp_leaves": [{"employee_id": "E05", "days": ["六"]}],
        "pins": [{"employee_id": "E01", "day": "六", "shift": "早班"}],
    }
    out = llm._sanitize(raw, "下周正常排班，E05 周六请假，周末早班多留一个收银", "llm")
    assert [t.employee_id for t in out.temp_leaves] == ["E05"]
    assert out.pins == []                       # 编造的 pin 必须被丢掉
    assert out.action == "generate"             # 且不能因此退化成澄清态
    assert any("凭空补充" in n for n in out.notes)


def test_keeps_pin_for_employee_actually_mentioned():
    raw = {"action": "generate", "pins": [{"employee_id": "E01", "day": "六", "shift": "早班"}]}
    out = llm._sanitize(raw, "周六早班把 E01 排上", "llm")
    assert [(p.employee_id, p.day, p.shift) for p in out.pins] == [("E01", "六", "早班")]
    assert out.notes == []


def test_drops_hallucinated_exclude_and_forbid():
    raw = {
        "action": "generate",
        "exclude_employees": ["E03"],
        "forbids": [{"employee_id": "E09", "day": "一", "shift": "早班"}],
    }
    out = llm._sanitize(raw, "下周正常排班", "llm")
    assert out.exclude_employees == []
    assert out.forbids == []


def test_drops_noop_min_staff_override():
    # 周六基线是 6 人，override=1 是纯 no-op，留着只会在 UI 上误导
    raw = {"action": "generate", "min_staff_override": {"六|早班": 1, "一": 3}}
    out = llm._sanitize(raw, "周末早班多留一个收银", "llm")
    assert out.min_staff_override == {}


def test_keeps_real_min_staff_override():
    # 周一基线 4 人，抬到 5 人是真约束，必须保留
    raw = {"action": "generate", "min_staff_override": {"一": 5, "六|晚班": 7}}
    out = llm._sanitize(raw, "周一多排一个人，周六晚班加到 7 个", "llm")
    assert out.min_staff_override == {"一": 5, "六|晚班": 7}


def test_invalid_employee_id_still_becomes_clarification():
    # 与「编造」区分开：ID 本身非法 → 仍然要反问，而不是静默丢弃
    raw = {"action": "generate", "temp_leaves": [{"employee_id": "E99", "days": ["一"]}]}
    out = llm._sanitize(raw, "E99 周一请假", "llm")
    assert out.action == "unknown"
    assert any("E99" in c for c in out.clarification_needed)
