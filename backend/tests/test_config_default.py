"""默认配置等价性：省略 config 的请求必须与配置化改造前逐字段一致。

这一组是整次改造的回归基线。配置化最容易出的事故不是「新维度不能用」，而是
「默认维度悄悄变了」——比如员工技能顺序变了、R-07 的边界松了一小时。
所以这里逐项对着 data.py 的题面数据核，而不是只跑一遍看有没有异常。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import data  # noqa: E402
from app import rules as R  # noqa: E402
from app import solver  # noqa: E402
from app import validator as V  # noqa: E402
from app.config import (  # noqa: E402
    LOCKED_RULE_TYPES,
    RULE_MAX_CONSECUTIVE,
    RULE_MAX_SHIFTS,
    RULE_MIN_REST,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    RULE_RESPECT_UNAVAILABILITY,
    RULE_SKILL_INTEGRITY,
    default_config,
    default_index,
    index_of,
)
from app.models import Schedule, ScheduleRequest, Slot  # noqa: E402
from config_factory import failed_rules  # noqa: E402


@pytest.fixture(scope="module")
def baseline():
    sch, _, diag = solver.solve(ScheduleRequest(), None)
    assert sch is not None, f"默认配置不应无解：{diag}"
    return sch


# ---------- 维度 ----------


def test_default_scenario_equals_data_module():
    idx = default_index()
    assert idx.day_ids == data.DAYS
    assert idx.shift_ids == data.SHIFTS
    assert [idx.day_label(d) for d in idx.day_ids] == [data.DAY_LABELS[d] for d in data.DAYS]
    assert {d for d in idx.day_ids if idx.is_peak(d)} == data.WEEKEND
    assert idx.slot_count() == 14
    # 天优先顺序是前端网格与 diff 的隐含契约，换成班优先会让看板列错位
    assert idx.slots[:3] == [("一", "早班"), ("一", "晚班"), ("二", "早班")]


def test_default_shift_hours_derived_from_time():
    idx = default_index()
    assert idx.shift_time_label("早班") == data.SHIFT_TIME["早班"]
    assert idx.shifts["早班"].hours == 8.0 and idx.shifts["晚班"].hours == 8.0


def test_default_min_required_matches_old_helper():
    idx = default_index()
    for d in data.DAYS:
        for s in data.SHIFTS:
            assert idx.min_required(d, s) == data.min_required(d)


# ---------- 员工 ----------


def test_default_employees_match_data_records():
    cfg = default_config()
    assert [e.id for e in cfg.employees] == list(data.EMPLOYEES)
    for e in cfg.employees:
        src = data.EMPLOYEES[e.id]
        # 题面没有姓名字段：占位必须是工号本身，编一个中文名就是凭空造数据
        assert e.name == e.id
        assert e.role == src.role
        assert set(e.skills) == set(src.skills)
        assert e.max_shifts is None          # 默认走全局 R-05
        assert e.preferred_shifts == ([src.preference] if src.preference else [])
        assert e.active is True


def test_default_unavailability_covers_days_and_leave():
    idx = default_index()
    for eid, src in data.EMPLOYEES.items():
        for d in data.DAYS:
            expect = d in src.available_days and d not in src.leave_days
            assert idx.can_work(eid, d) is expect, (eid, d)
        # 请假是具名原因，结构性不可用不是——求解器的灵活度启发式依赖这个区分
        assert set(idx.roster_days(eid)) == set(src.available_days)


# ---------- 规则 ----------


def test_default_rules_map_to_templates():
    cfg = default_config()
    # R-10 是配置化后显式补上的：默认两班重叠让「一人一天一个班」一直是隐含假设，
    # 不写成规则的话，用户把班次改成互不重叠之后这条假设会静默消失
    assert [r.id for r in cfg.rules] == [f"R-{i:02d}" for i in range(1, 11)]
    assert [r.name for r in cfg.rules[:9]] == [r["text"] for r in data.RULES]
    assert cfg.rules[9].name == "每人每天最多一个班"
    types = {r.id: r.type for r in cfg.rules}
    assert types["R-01"] == types["R-02"] == types["R-03"] == RULE_REQUIRE_ATTRIBUTE
    assert types["R-04"] == RULE_MIN_STAFF
    assert types["R-05"] == RULE_MAX_SHIFTS
    assert types["R-06"] == RULE_MAX_CONSECUTIVE
    assert types["R-07"] == RULE_MIN_REST
    assert types["R-08"] == RULE_RESPECT_UNAVAILABILITY
    assert types["R-09"] == RULE_SKILL_INTEGRITY
    assert types["R-10"] == RULE_ONE_SHIFT_PER_DAY
    params = {r.id: r.params for r in cfg.rules}
    assert params["R-04"]["default"] == data.MIN_PER_SHIFT_WEEKDAY
    assert params["R-04"]["peak"] == data.MIN_PER_SHIFT_WEEKEND
    assert params["R-05"]["max"] == data.MAX_SHIFTS_PER_WEEK
    assert params["R-06"]["max"] == data.MAX_CONSECUTIVE_DAYS
    assert params["R-07"]["hours"] == 13
    # R-10 非 locked：一人一天一个班是业务选择，用户必须能关掉它
    assert [r.locked for r in cfg.rules] == [False] * 7 + [True, True, False]
    assert all(r.enabled for r in cfg.rules)


def test_locked_rules_are_appended_when_missing():
    cfg = default_config()
    cfg.rules = [r for r in cfg.rules if r.type not in LOCKED_RULE_TYPES]
    # 重新构造走 model_validator：缺 locked 规则不能静默变成「不检查」
    restored = type(cfg).model_validate(cfg.model_dump())
    assert {r.type for r in restored.rules} >= set(LOCKED_RULE_TYPES)
    assert all(r.locked for r in restored.rules if r.type in LOCKED_RULE_TYPES)


def test_default_r10_is_reported_on_same_day_double_shift():
    """R-10 存在的意义就是让「一人一天一个班」在校验面板上有一行。"""
    sch = Schedule(slots=[
        Slot(day="一", shift="早班", employee_ids=["E01", "E06", "E07", "E11"]),
        Slot(day="一", shift="晚班", employee_ids=["E01", "E06", "E07", "E11"]),
    ])
    rep = V.validate(sch)
    hit = [v for v in rep.violations if v.rule_id == "R-10"]
    assert len(hit) == 4 and all(v.day == "一" for v in hit)
    assert "被安排 2 个班" in hit[0].detail


def test_disabling_default_r10_keeps_the_physical_invariant():
    """关掉 R-10 之后，默认两班时间重叠这一物理事实仍必须被拦住（归在重叠规则 R-05 下）。

    这正是「不能只看规则开关」的原因：R-10 关掉了，一个人一天照样上不了两个重叠的班。
    """
    cfg = default_config()
    for r in cfg.rules:
        if r.id == "R-10":
            r.enabled = False
    sch = Schedule(slots=[
        Slot(day="一", shift="早班", employee_ids=["E01", "E06", "E07", "E11"]),
        Slot(day="一", shift="晚班", employee_ids=["E01", "E06", "E07", "E11"]),
    ])
    rep = V.validate(sch, None, config=cfg)
    assert "R-10" not in [r.rule_id for r in rep.rule_results if not r.passed]
    assert any("时间重叠" in v.detail for v in rep.violations if v.rule_id == "R-05")


def test_disabling_default_r10_does_not_change_the_solution(baseline):
    """默认两班重叠，R-10 开或关，求解器给出的排班必须一模一样。"""
    cfg = default_config()
    for r in cfg.rules:
        if r.id == "R-10":
            r.enabled = False
    other, _, _ = solver.solve(ScheduleRequest(), None, config=cfg)
    assert other is not None and other.as_map() == baseline.as_map()


def test_rule_results_order_is_config_order():
    idx = default_index()
    assert [r.id for r in R.rule_ids_in_order(idx)] == [f"R-{i:02d}" for i in range(1, 11)]


# ---------- 端到端等价 ----------


def test_solve_is_identical_with_explicit_default_config(baseline):
    other, _, _ = solver.solve(ScheduleRequest(), None, config=default_config())
    assert other is not None
    assert other.as_map() == baseline.as_map()


def test_validate_is_identical_with_explicit_default_config(baseline):
    implicit = V.validate(baseline)
    explicit = V.validate(baseline, None, config=default_config())
    assert implicit.model_dump() == explicit.model_dump()
    assert implicit.passed and len(implicit.rule_results) == 10


def test_soft_metrics_identical_with_explicit_default_config(baseline):
    assert V.soft_metrics(baseline).model_dump() == \
        V.soft_metrics(baseline, config=default_config()).model_dump()


def test_baseline_passes_and_respects_headcount(baseline):
    rep = V.validate(baseline)
    assert rep.passed, [v.model_dump() for v in rep.violations]
    idx = default_index()
    for s in baseline.slots:
        assert len(s.employee_ids) >= idx.min_required(s.day, s.shift)


# ---------- R-07 → min_rest_hours 等价 ----------


def _pair(prev_day: str, prev_shift: str, day: str, shift: str, eid: str = "E01") -> Schedule:
    return Schedule(slots=[
        Slot(day=prev_day, shift=prev_shift, employee_ids=[eid]),
        Slot(day=day, shift=shift, employee_ids=[eid]),
    ])


def test_min_rest_hours_forbids_night_then_next_morning():
    """旧 R-07 的原话：晚班之后不能接次日早班。21:00 → 09:00 只有 12 小时。

    引擎按「严格小于才违规」判，所以旧行为靠默认值 hours=13 维持，而不是靠比较符。
    """
    rep = V.validate(_pair("一", "晚班", "二", "早班"))
    assert "R-07" in failed_rules(rep)
    assert any(v.employee_id == "E01" and v.day == "二" for v in rep.violations if v.rule_id == "R-07")


def test_min_rest_hours_exactly_at_threshold_is_compliant():
    """参数叫「最少休息 X 小时」，给够 X 就该放行：12 小时间隔在 hours=12 下不违规。

    这条和上一条共同钉住默认值的选择——把默认从 13 调回 12，旧 R-07 就会漏掉晚接早。
    """
    cfg = default_config()
    for r in cfg.rules:
        if r.type == RULE_MIN_REST:
            r.params = {"hours": 12}
    # 一 晚班 21:00 → 二 早班 09:00 = 恰好 12 小时
    rep = V.validate(_pair("一", "晚班", "二", "早班"), None, config=cfg)
    assert "R-07" not in failed_rules(rep)


def test_min_rest_hours_allows_morning_then_next_night():
    # 一 17:00 → 二 13:00 = 20 小时，休息充足
    rep = V.validate(_pair("一", "早班", "二", "晚班"))
    assert "R-07" not in failed_rules(rep)


def test_min_rest_hours_is_a_parameter_not_a_shift_name_comparison():
    """把参数调小，同一张表就不再违规——证明判定走的是实际间隔小时数，不是班次名字符串。"""
    cfg = default_config()
    for r in cfg.rules:
        if r.type == RULE_MIN_REST:
            r.params = {"hours": 11}
    rep = V.validate(_pair("一", "晚班", "二", "早班"), None, config=cfg)
    assert "R-07" not in failed_rules(rep)


def test_default_index_is_cached_but_copies_are_independent():
    """配置是可变对象：改了副本不能命中旧索引，否则会拿一份与配置不一致的规则去校验。"""
    assert index_of(None) is default_index()
    cfg = default_config()
    for r in cfg.rules:
        if r.type == RULE_MIN_STAFF:
            r.params = {**r.params, "default": 1, "peak": 1}
    assert index_of(cfg).min_required("一", "早班") == 1
    assert default_index().min_required("一", "早班") == data.MIN_PER_SHIFT_WEEKDAY
