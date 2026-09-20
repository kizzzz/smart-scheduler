"""校验器与求解器的回归测试。

测试重点不是「代码能跑」，而是「9 条硬规则真的被独立校验器抓得住」。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import solver  # noqa: E402
from app import validator as V  # noqa: E402
from app.data import DAYS, all_slots  # noqa: E402
from app.models import Pin, Schedule, ScheduleRequest, Slot, TempLeave  # noqa: E402


def gen(intent: ScheduleRequest | None = None, base=None):
    return solver.solve(intent or ScheduleRequest(), base)


@pytest.fixture(scope="module")
def baseline():
    sch, _, diag = gen()
    assert sch is not None, f"基础场景不应无解: {diag}"
    return sch


def rule_of(report, rid):
    return next(r for r in report.rule_results if r.rule_id == rid)


# ---------- 主链路 ----------


def test_baseline_zero_violation(baseline):
    """北极星指标：硬约束零违规。"""
    rep = V.validate(baseline)
    assert rep.passed, [v.model_dump() for v in rep.violations]
    assert len(rep.violations) == 0
    assert len(baseline.slots) == 14


def test_all_rules_reported(baseline):
    rep = V.validate(baseline)
    assert [r.rule_id for r in rep.rule_results] == [f"R-{i:02d}" for i in range(1, 11)]
    assert all(r.passed for r in rep.rule_results)


def test_headcount_lower_bound(baseline):
    for s in baseline.slots:
        need = 6 if s.day in ("六", "日") else 4
        assert len(s.employee_ids) >= need, f"周{s.day}{s.shift} 人数不足"


# ---------- 逐条规则必须抓得住 ----------


def test_r01_missing_keeper(baseline):
    bad = baseline.model_copy(deep=True)
    slot = bad.slots[0]
    keepers = [e for e in slot.employee_ids if "店长值守" in V.EMPLOYEES[e].skills]
    slot.employee_ids = [e for e in slot.employee_ids if e not in keepers] + ["E09", "E10"]
    rep = V.validate(bad)
    assert not rep.passed
    assert rule_of(rep, "R-01").violation_count >= 1


def test_r02_drink_below_two(baseline):
    bad = Schedule(slots=[Slot(day=d, shift=s, employee_ids=[]) for d, s in all_slots()])
    bad.slots[0].employee_ids = ["E01", "E11", "E15", "E11"]
    rep = V.validate(bad)
    assert rule_of(rep, "R-02").violation_count >= 1


def test_r03_no_cashier():
    """E04 有值守但无收银，E09/E16 只有饮品，E20 无收银 → 该班 R-03 必违规。"""
    sch = Schedule(slots=[Slot(day="三", shift="早班", employee_ids=["E04", "E09", "E16", "E20"])])
    rep = V.validate(sch)
    hit = [v for v in rep.violations if v.rule_id == "R-03" and v.day == "三" and v.shift == "早班"]
    assert len(hit) == 1
    # 同一班换入 E10（有收银）后，该班 R-03 不再违规
    sch.slots[0].employee_ids = ["E04", "E09", "E16", "E10"]
    rep2 = V.validate(sch)
    assert not [v for v in rep2.violations if v.rule_id == "R-03" and v.day == "三" and v.shift == "早班"]


def test_r04_weekend_needs_six(baseline):
    bad = baseline.model_copy(deep=True)
    sat = next(s for s in bad.slots if s.day == "六" and s.shift == "早班")
    sat.employee_ids = sat.employee_ids[:5]
    rep = V.validate(bad)
    assert rule_of(rep, "R-04").violation_count >= 1


def test_r05_over_five_shifts():
    slots = []
    for d in DAYS[:6]:
        slots.append(Slot(day=d, shift="早班", employee_ids=["E02", "E06", "E07", "E17", "E11", "E10"]))
    rep = V.validate(Schedule(slots=slots))
    assert rule_of(rep, "R-05").violation_count >= 1
    assert rep.per_employee_shifts["E02"] == 6


def test_r05_catches_same_day_double_shift():
    """同日早晚班时间重叠，必须被拦住。"""
    sch = Schedule(slots=[
        Slot(day="一", shift="早班", employee_ids=["E01", "E06", "E07", "E11"]),
        Slot(day="一", shift="晚班", employee_ids=["E01", "E06", "E07", "E11"]),
    ])
    rep = V.validate(sch)
    assert any("早班与晚班" in v.detail for v in rep.violations if v.rule_id == "R-05")


def test_r06_six_consecutive_days():
    slots = [Slot(day=d, shift="早班", employee_ids=["E02", "E06", "E07", "E17", "E11", "E10"]) for d in DAYS[:6]]
    rep = V.validate(Schedule(slots=slots))
    assert rule_of(rep, "R-06").violation_count >= 1


def test_r07_evening_then_morning():
    sch = Schedule(slots=[
        Slot(day="一", shift="晚班", employee_ids=["E02", "E07", "E11", "E18"]),
        Slot(day="二", shift="早班", employee_ids=["E02", "E07", "E12", "E10"]),
    ])
    rep = V.validate(sch)
    assert rule_of(rep, "R-07").violation_count == 2
    v = next(v for v in rep.violations if v.rule_id == "R-07")
    assert v.day == "二" and v.shift == "早班"


def test_r08_fixed_leave_and_unavailable():
    sch = Schedule(slots=[
        Slot(day="三", shift="早班", employee_ids=["E01", "E06", "E09", "E13"]),
    ])
    rep = V.validate(sch)
    details = [v.detail for v in rep.violations if v.rule_id == "R-08"]
    assert any("E01" in d and "请假" in d for d in details)      # E01 周三请假
    assert any("E13" in d for d in details)                      # E13 只能周六日


def test_r08_temp_leave_from_intent(baseline):
    """意图里的临时请假必须和固有请假同等约束力。"""
    slot = next(s for s in baseline.slots if s.employee_ids)
    eid = slot.employee_ids[0]
    intent = ScheduleRequest(action="adjust", temp_leaves=[TempLeave(employee_id=eid, days=[slot.day])])
    rep = V.validate(baseline, intent)
    hit = [v for v in rep.violations if v.rule_id == "R-08" and v.employee_id == eid and "临时请假" in v.detail]
    assert hit, "临时请假未被 R-08 拦住"


def test_r09_unknown_employee():
    sch = Schedule(slots=[Slot(day="一", shift="早班", employee_ids=["E99", "E01", "E06", "E07"])])
    rep = V.validate(sch)
    assert rule_of(rep, "R-09").violation_count == 1


# ---------- 修改类指令 ----------


def test_temp_leave_replan_is_valid_and_minimal(baseline):
    intent = ScheduleRequest(action="adjust", temp_leaves=[TempLeave(employee_id="E06", days=["四"])])
    sch, _, diag = gen(intent, baseline)
    assert sch is not None and not diag.infeasible
    rep = V.validate(sch, intent)
    assert rep.passed, [v.model_dump() for v in rep.violations]
    for s in sch.slots:
        if s.day == "四":
            assert "E06" not in s.employee_ids
    changed = solver.changed_slots(baseline, sch)
    assert len(changed) <= 5, f"扰动过大: {changed}"


def test_pin_is_respected(baseline):
    intent = ScheduleRequest(action="adjust", pins=[Pin(employee_id="E01", day="六", shift="早班")])
    sch, _, _ = gen(intent, baseline)
    assert sch is not None
    assert V.validate(sch, intent).passed
    sat = next(s for s in sch.slots if s.day == "六" and s.shift == "早班")
    assert "E01" in sat.employee_ids


def test_min_staff_override_can_only_raise(baseline):
    intent = ScheduleRequest(min_staff_override={"一|早班": 6})
    sch, _, _ = gen(intent)
    assert sch is not None
    slot = next(s for s in sch.slots if s.day == "一" and s.shift == "早班")
    assert len(slot.employee_ids) >= 6
    # 下调请求不生效：R-04 下限仍然守住
    intent2 = ScheduleRequest(min_staff_override={"六|早班": 2})
    sch2, _, _ = gen(intent2)
    assert sch2 is not None
    sat = next(s for s in sch2.slots if s.day == "六" and s.shift == "早班")
    assert len(sat.employee_ids) >= 6


# ---------- 无解诊断 ----------


def test_proven_infeasible_when_keepers_removed():
    intent = ScheduleRequest(exclude_employees=["E01", "E02"])
    sch, _, diag = gen(intent)
    assert sch is None
    assert diag.infeasible and diag.kind == "proven_infeasible"
    assert diag.bottleneck_rule == "R-01"
    assert diag.evidence and diag.unlock_options
    assert len(diag.unlock_options) >= 2


def test_no_false_infeasible_on_single_leave(baseline):
    """真实数据存在可行解，单人请假不得误报无解。"""
    for eid in ("E07", "E10", "E17", "E20"):
        intent = ScheduleRequest(temp_leaves=[TempLeave(employee_id=eid, days=["五"])])
        sch, _, diag = gen(intent, baseline)
        assert sch is not None, f"{eid} 请假被误判无解: {diag.evidence}"


# ---------- 软指标 ----------


def test_soft_metrics_reasonable(baseline):
    m = V.validate(baseline).soft_metrics
    assert m.total_assignments >= 64
    assert m.preference_hit_rate >= 0.6
    assert m.workload_max <= 5
