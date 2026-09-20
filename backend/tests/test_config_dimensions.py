"""非默认维度与求解预算。

配置化真正的验收点不是「默认配置还能跑」，而是「换成 5 天 3 班、1 天 1 班、
一天多班之后，求解与校验仍然一致」。所以这里每个用例都跑完整的 solve → validate：
求解器自己说的「找到了」不算，必须由校验器独立点头。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config_check, serializers, solver  # noqa: E402
from app import validator as V  # noqa: E402
from app.config import (  # noqa: E402
    MAX_SLOTS,
    RULE_MAX_SHIFTS,
    RULE_MIN_REST,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    UnavailableSlot,
    default_config,
    index_of,
)
from app.models import ScheduleRequest  # noqa: E402
from config_factory import config, days, emp, emps, rule, shifts  # noqa: E402

THREE_SHIFTS = shifts(("早", "08:00", "12:00"), ("中", "12:00", "16:00"), ("晚", "16:00", "20:00"))


def solve(cfg, intent=None, budget=None):
    return solver.solve(intent or ScheduleRequest(), None, config=cfg, budget=budget)


def solved(cfg):
    """求解并让校验器独立验收，返回排班表。"""
    assert config_check.check(cfg)["ok"], config_check.check(cfg)["errors"]
    sch, _, diag = solve(cfg)
    assert sch is not None, f"配置应可解：{diag.evidence}"
    rep = V.validate(sch, None, config=cfg)
    assert rep.passed, [v.detail for v in rep.violations]
    return sch


# ---------- 三班制 / 五天周期 ----------


def three_shift_config():
    return config(
        day_defs=days(5, peak=["d5"]),
        shift_defs=THREE_SHIFTS,
        employees=[
            *emps(4, skills=["咖啡"]),
            *emps(5, start=5),
        ],
        skill_pool=["咖啡"],
        rules=[
            rule("MS", RULE_MIN_STAFF, default=2, peak=3),
            rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1),
            rule("MX", RULE_MAX_SHIFTS, max=6),
            rule("OS", RULE_ONE_SHIFT_PER_DAY),
        ],
    )


def test_five_days_three_shifts_end_to_end():
    cfg = three_shift_config()
    sch = solved(cfg)
    idx = index_of(cfg)
    assert len(sch.slots) == 15 == idx.slot_count()
    assert {(s.day, s.shift) for s in sch.slots} == set(idx.slots)
    for s in sch.slots:
        assert len(s.employee_ids) >= idx.min_required(s.day, s.shift)
    # peak 日的下限来自配置，不是「周末」这个概念
    assert idx.min_required("d5", "早") == 3 and idx.min_required("d1", "早") == 2


def test_soft_metrics_do_not_assume_14_slots():
    cfg = three_shift_config()
    for e in cfg.employees:
        e.preferred_shifts = ["早"]
    sch = solved(cfg)
    rep = V.validate(sch, None, config=cfg)
    out = serializers.soft_metrics_out(rep, sch, cfg)
    assert set(out) == {"preference_rate", "balance_score", "skill_redundancy"}
    # 分母写死 14 格时，15 格场景会算出 >1 的指标，前端进度条直接溢出
    assert all(0.0 <= out[k] <= 1.0 for k in out), out


def test_single_shift_seven_days():
    cfg = config(
        day_defs=days(7), shift_defs=shifts(("全天", "09:00", "18:00")),
        employees=emps(6),
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("MX", RULE_MAX_SHIFTS, max=4)],
    )
    sch = solved(cfg)
    assert len(sch.slots) == 7
    assert all(len(s.employee_ids) >= 2 for s in sch.slots)


def test_one_day_one_shift_is_a_valid_scenario():
    cfg = config(
        day_defs=days(1), shift_defs=shifts(("全天", "09:00", "18:00")), employees=emps(3),
        rules=[rule("MS", RULE_MIN_STAFF, default=2)],
    )
    sch = solved(cfg)
    assert len(sch.slots) == 1 and len(sch.slots[0].employee_ids) >= 2


# ---------- 一天多班（one_shift_per_day 关闭） ----------


def test_multiple_shifts_per_day_when_rule_disabled():
    """4 人撑 3 个班 × 每班 2 人：不允许一天多班就必然无解，允许则有解。

    这条用例锁住的是「one_shift_per_day 真的可关」——旧 solver 把它写成隐式假设，
    三班制小店会永远拿到「无解」。
    """
    base = dict(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=emps(4),
        rules=[rule("MS", RULE_MIN_STAFF, default=2)],
    )
    loose = config(**base)
    sch = solved(loose)
    per_day = {}
    for s in sch.slots:
        for e in s.employee_ids:
            per_day.setdefault((e, s.day), 0)
            per_day[(e, s.day)] += 1
    # 6 人次 / 4 人：鸽笼原理保证至少有人一天上两个班
    assert max(per_day.values()) >= 2

    strict = config(**{**base, "rules": [rule("MS", RULE_MIN_STAFF, default=2),
                                         rule("OS", RULE_ONE_SHIFT_PER_DAY)]})
    sch2, _, diag = solve(strict)
    assert sch2 is None and diag.infeasible and diag.kind == "proven_infeasible"
    # 证据要落在「一天最多上几个班」这个下界上：它是 one_shift_per_day 打开后的直接后果
    assert any("当天各班共需 6 人次" in e for e in diag.evidence), diag.evidence
    assert index_of(strict).max_shifts_per_day() == 1


def test_min_rest_hours_limits_same_day_stacking():
    """一天多班允许，但仍受最小休息间隔约束：8 小时间隔下 3 个连排班不可能同日堆叠。"""
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=emps(4),
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("MR", RULE_MIN_REST, hours=8)],
    )
    assert index_of(cfg).max_shifts_per_day() == 1
    sch, _, diag = solve(cfg)
    # 与上一个用例同样的员工池，因为休息间隔把「一天两班」堵死，所以必然无解
    assert sch is None and diag.kind == "proven_infeasible"


# ---------- 求解预算 ----------


def test_budget_scales_with_slots_but_has_a_hard_cap():
    small = solver.SearchBudget.for_index(index_of(default_config()))
    assert (small.time_s, small.nodes) == (solver.TIME_BUDGET_S, solver.NODE_BUDGET)

    big = config(day_defs=days(14), shift_defs=[*THREE_SHIFTS, *shifts(("夜", "20:00", "00:00"))],
                 employees=emps(20), rules=[rule("MS", RULE_MIN_STAFF, default=1)])
    idx = index_of(big)
    assert idx.slot_count() == MAX_SLOTS
    budget = solver.SearchBudget.for_index(idx)
    assert budget.time_s > small.time_s and budget.nodes > small.nodes
    # 硬顶存在的理由：用户宁可拿到「场景太大」，也不愿等一分钟拿到一句无解
    assert budget.time_s <= solver.TIME_BUDGET_CAP_S and budget.nodes <= solver.NODE_BUDGET_CAP


def test_exhausted_budget_reports_timeout_not_infeasible():
    """超预算 ≠ 无解。说成无解会把店长引向砍规则，而正确动作是缩小场景。"""
    sch, _, diag = solve(default_config(), budget=solver.SearchBudget(time_s=0.0, nodes=1))
    assert sch is None
    assert diag.infeasible and diag.kind == "search_timeout"
    assert any("未能在预算内找到可行解，也未证明无解" in e for e in diag.evidence), diag.evidence
    assert any("预算" in e for e in diag.evidence)


def test_structural_infeasibility_beats_budget_exhaustion():
    """结构性无解要给出证据，即使预算也用尽了：两种结论对应完全不同的下一步动作。"""
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS,
        employees=[emp("E01"), emp("E02", unavailable=[UnavailableSlot(day="d1")])],
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("OS", RULE_ONE_SHIFT_PER_DAY)],
    )
    sch, _, diag = solve(cfg, budget=solver.SearchBudget(time_s=0.0, nodes=1))
    assert sch is None and diag.kind == "proven_infeasible"
    assert diag.bottleneck_rule == "MS" and diag.bottleneck_slots


def test_default_scenario_still_solves_within_default_budget():
    """回归保护：预算改动不能让默认场景变成偶发超时。"""
    sch, _, diag = solve(default_config())
    assert sch is not None, diag.evidence
    assert len(sch.slots) == 14


# ---------- 意图在非默认维度上仍然生效 ----------


def test_intent_leave_and_pin_on_custom_dimensions():
    from app.models import Pin, TempLeave

    cfg = three_shift_config()
    intent = ScheduleRequest(
        action="adjust",
        temp_leaves=[TempLeave(employee_id="E01", days=["d1", "d2"])],
        pins=[Pin(employee_id="E09", day="d3", shift="晚")],
    )
    sch, _, diag = solve(cfg, intent)
    assert sch is not None, diag.evidence
    grid = sch.as_map()
    assert "E01" not in grid["d1|早"] + grid["d1|中"] + grid["d1|晚"]
    assert "E09" in grid["d3|晚"]
    assert V.validate(sch, intent, config=cfg).passed


@pytest.mark.parametrize("day_count", [1, 3, 7, 14])
def test_period_lengths_are_supported(day_count):
    cfg = config(
        day_defs=days(day_count), shift_defs=shifts(("全天", "09:00", "18:00")),
        employees=emps(8), rules=[rule("MS", RULE_MIN_STAFF, default=2)],
    )
    sch = solved(cfg)
    assert len(sch.slots) == day_count
