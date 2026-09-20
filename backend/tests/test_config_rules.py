"""8 类规则模板逐条验收。

每个用例都成对出现：一张必须报违规的表 + 一张必须通过的表。只测「能报出来」不够——
规则引擎最危险的失败方式是「什么都报」，那会让校验报告失去可信度，店长就开始无视红点。

用例里的班次刻意选不重叠的时间段（09-13 / 17-21）：默认的 09-17 / 13-21 是重叠的，
在它上面测 one_shift_per_day 只会撞到引擎级不变式，测不到规则本身。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import validator as V  # noqa: E402
from app.config import (  # noqa: E402
    RULE_MAX_CONSECUTIVE,
    RULE_MAX_SHIFTS,
    RULE_MIN_REST,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    RULE_RESPECT_UNAVAILABILITY,
    RULE_SKILL_INTEGRITY,
    UnavailableSlot,
    index_of,
)
from config_factory import (  # noqa: E402
    DAY_SHIFT,
    NIGHT_SHIFT,
    config,
    days,
    emp,
    emps,
    failed_rules,
    rule,
    sched,
    shifts,
)

TWO_SHIFTS = shifts(DAY_SHIFT, NIGHT_SHIFT)


def check(cfg, grid):
    return V.validate(sched(grid), None, config=cfg)


# ---------- 1. min_staff_per_shift ----------


def test_min_staff_default_peak_and_override():
    cfg = config(
        day_defs=days(2, peak=["d2"]),
        shift_defs=TWO_SHIFTS,
        employees=emps(4),
        rules=[rule("MS", RULE_MIN_STAFF, default=1, peak=2,
                    overrides=[{"day": "d1", "shift": "晚", "min": 3}])],
    )
    # override 优先于 peak/default，先确认索引把这一格的下限编译对了
    assert index_of(cfg).min_required("d1", "晚") == 3

    bad = check(cfg, {
        ("d1", "早"): ["E01"],                       # 达标（default=1）
        ("d1", "晚"): ["E01", "E02"],                # override 要 3
        ("d2", "早"): ["E03"],                       # peak 要 2
        ("d2", "晚"): ["E03", "E04"],                # 达标
    })
    assert failed_rules(bad) == ["MS"]
    slots = {(v.day, v.shift) for v in bad.violations}
    assert slots == {("d1", "晚"), ("d2", "早")}

    ok = check(cfg, {
        ("d1", "早"): ["E01"],
        ("d1", "晚"): ["E02", "E03", "E04"],
        ("d2", "早"): ["E01", "E02"],
        ("d2", "晚"): ["E03", "E04"],
    })
    assert ok.passed, [v.detail for v in ok.violations]


# ---------- 2. require_attribute ----------


def test_require_attribute_by_skill_and_role():
    cfg = config(
        day_defs=days(1),
        shift_defs=TWO_SHIFTS,
        employees=[emp("E01", skills=["咖啡"]), emp("E02", role="店长"), emp("E03")],
        skill_pool=["咖啡"],
        rules=[
            rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1),
            rule("A2", RULE_REQUIRE_ATTRIBUTE, attr="role", value="店长", min=1, noun="店长值守资格"),
        ],
    )
    bad = check(cfg, {("d1", "早"): ["E01", "E03"], ("d1", "晚"): ["E02", "E03"]})
    # 同类多实例必须各自判定：早班缺店长、晚班缺咖啡
    assert sorted(failed_rules(bad)) == ["A1", "A2"]
    assert {(v.rule_id, v.shift) for v in bad.violations} == {("A1", "晚"), ("A2", "早")}
    assert "店长值守资格" in next(v.detail for v in bad.violations if v.rule_id == "A2")

    ok = check(cfg, {("d1", "早"): ["E01", "E02"], ("d1", "晚"): ["E01", "E02"]})
    assert ok.passed


def test_require_attribute_min_greater_than_one():
    cfg = config(
        day_defs=days(1), shift_defs=shifts(DAY_SHIFT),
        employees=[emp("E01", skills=["咖啡"]), emp("E02", skills=["咖啡"]), emp("E03")],
        skill_pool=["咖啡"],
        rules=[rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=2)],
    )
    bad = check(cfg, {("d1", "早"): ["E01", "E03"]})
    assert failed_rules(bad) == ["A1"] and "仅 1 名" in bad.violations[0].detail
    assert check(cfg, {("d1", "早"): ["E01", "E02"]}).passed


# ---------- 3. max_shifts_per_period ----------


def test_max_shifts_global_and_personal_override():
    cfg = config(
        day_defs=days(2), shift_defs=TWO_SHIFTS,
        employees=[emp("E01"), emp("E02", max_shifts=1), emp("E03")],
        rules=[rule("MX", RULE_MAX_SHIFTS, max=3)],
    )
    bad = check(cfg, {
        ("d1", "早"): ["E01", "E02"],
        ("d1", "晚"): ["E01", "E02"],
        ("d2", "早"): ["E01"],
        ("d2", "晚"): ["E01"],
    })
    assert failed_rules(bad) == ["MX"]
    hits = {v.employee_id for v in bad.violations}
    # E01 超全局上限 3，E02 超个人上限 1；个人上限优先于全局规则
    assert hits == {"E01", "E02"}
    assert "上限 1 个班" in next(v.detail for v in bad.violations if v.employee_id == "E02")

    assert check(cfg, {("d1", "早"): ["E01"], ("d1", "晚"): ["E03"], ("d2", "早"): ["E02"]}).passed


# ---------- 4. max_consecutive_days ----------


def test_max_consecutive_days():
    cfg = config(
        day_defs=days(4), shift_defs=shifts(DAY_SHIFT),
        employees=emps(3),
        rules=[rule("MC", RULE_MAX_CONSECUTIVE, max=2)],
    )
    bad = check(cfg, {("d1", "早"): ["E01"], ("d2", "早"): ["E01"], ("d3", "早"): ["E01"]})
    assert failed_rules(bad) == ["MC"]
    # 一个人一段连班只报一次，否则同一段会刷成 N 条重复违规
    assert len([v for v in bad.violations if v.employee_id == "E01"]) == 1

    ok = check(cfg, {("d1", "早"): ["E01"], ("d2", "早"): ["E01"], ("d3", "早"): ["E02"],
                     ("d4", "早"): ["E01"]})
    assert ok.passed


# ---------- 5. min_rest_hours ----------


def test_min_rest_hours_uses_actual_clock_gap():
    cfg = config(
        day_defs=days(2), shift_defs=TWO_SHIFTS,
        employees=emps(2),
        rules=[rule("MR", RULE_MIN_REST, hours=13)],
    )
    # d1 晚班 21:00 → d2 早班 09:00 = 12 小时 < 13
    bad = check(cfg, {("d1", "晚"): ["E01"], ("d2", "早"): ["E01"]})
    assert failed_rules(bad) == ["MR"]
    assert bad.violations[0].day == "d2"

    loose = config(
        day_defs=days(2), shift_defs=TWO_SHIFTS, employees=emps(2),
        rules=[rule("MR", RULE_MIN_REST, hours=11)],
    )
    assert check(loose, {("d1", "晚"): ["E01"], ("d2", "早"): ["E01"]}).passed


def test_min_rest_hours_boundary_is_inclusive():
    """间隔恰好等于 hours 不违规：参数语义是「至少休息 X 小时」，给满 X 就算合规。

    没有这条，填 12 实际要求 12 以上，配置的人无从察觉。
    """
    exact = config(
        day_defs=days(2), shift_defs=TWO_SHIFTS, employees=emps(2),
        rules=[rule("MR", RULE_MIN_REST, hours=12)],
    )
    # d1 晚班 21:00 → d2 早班 09:00 = 恰好 12 小时
    assert check(exact, {("d1", "晚"): ["E01"], ("d2", "早"): ["E01"]}).passed


def test_min_rest_hours_covers_overnight_shift():
    """跨夜班（22:00–06:00）的间隔必须按绝对时间算，不能按「晚班在早班后面」这类顺序假设。"""
    cfg = config(
        day_defs=days(2), shift_defs=shifts(("夜", "22:00", "06:00"), ("午", "12:00", "20:00")),
        employees=emps(2),
        rules=[rule("MR", RULE_MIN_REST, hours=8)],
    )
    # d1 夜班到 d2 06:00，d2 午班 12:00 开始 = 6 小时 < 8
    bad = check(cfg, {("d1", "夜"): ["E01"], ("d2", "午"): ["E01"]})
    assert failed_rules(bad) == ["MR"]
    # d1 午班 20:00 → d1 夜班 22:00 只隔 2 小时，同样违规；换成隔天则不违规
    assert check(cfg, {("d1", "午"): ["E01"], ("d2", "夜"): ["E01"]}).passed


# ---------- 6. one_shift_per_day ----------


def test_one_shift_per_day_is_optional():
    strict = config(
        day_defs=days(1), shift_defs=TWO_SHIFTS, employees=emps(2),
        rules=[rule("OS", RULE_ONE_SHIFT_PER_DAY)],
    )
    grid = {("d1", "早"): ["E01"], ("d1", "晚"): ["E01"]}
    bad = check(strict, grid)
    assert failed_rules(bad) == ["OS"] and bad.violations[0].day == "d1"

    # 关掉这条规则，同一张表就合规：三班制门店允许一天两班（时间不重叠、休息也够）
    loose = config(day_defs=days(1), shift_defs=TWO_SHIFTS, employees=emps(2), rules=[])
    assert check(loose, grid).passed


def test_overlapping_shifts_are_always_illegal():
    """物理不可行不是可关的业务规则：没有任何规则时，时间重叠仍必须报出来。"""
    cfg = config(
        day_defs=days(1), shift_defs=shifts(("早班", "09:00", "17:00"), ("晚班", "13:00", "21:00")),
        employees=emps(2), rules=[],
    )
    rep = check(cfg, {("d1", "早班"): ["E01"], ("d1", "晚班"): ["E01"]})
    assert not rep.passed
    assert "时间重叠" in rep.violations[0].detail
    # 违规必须挂在一条生效中的规则上，否则前端按 rule_results 分组时会整条消失
    assert rep.violations[0].rule_id in {r.rule_id for r in rep.rule_results}


def test_duplicate_member_in_one_slot_is_reported():
    cfg = config(day_defs=days(1), shift_defs=shifts(DAY_SHIFT), employees=emps(2), rules=[])
    rep = check(cfg, {("d1", "早"): ["E01", "E01"]})
    assert not rep.passed and "重复员工" in rep.violations[0].detail


# ---------- 7. respect_unavailability（locked） ----------


def test_respect_unavailability_covers_day_shift_and_inactive():
    cfg = config(
        day_defs=days(2), shift_defs=TWO_SHIFTS,
        employees=[
            emp("E01", unavailable=[UnavailableSlot(day="d1")]),
            emp("E02", unavailable=[UnavailableSlot(day="d1", shift="晚")]),
            emp("E03", unavailable=[UnavailableSlot(day="d2", reason="请假")]),
            emp("E04", active=False),
        ],
        rules=[],
    )
    rep = check(cfg, {
        ("d1", "早"): ["E01", "E02"],
        ("d1", "晚"): ["E02"],
        ("d2", "早"): ["E03", "E04"],
    })
    hits = {(v.employee_id, v.day, v.shift) for v in rep.violations if v.rule_id == "R-08"}
    assert hits == {("E01", "d1", "早"), ("E02", "d1", "晚"), ("E03", "d2", "早"), ("E04", "d2", "早")}
    details = {v.employee_id: v.detail for v in rep.violations if v.rule_id == "R-08"}
    assert "请假" in details["E03"] and "停用" in details["E04"]
    # 按班次的不可用不能牵连同一天的其他班次
    assert ("E02", "d1", "早") not in hits


def test_locked_rules_run_even_when_disabled():
    cfg = config(
        day_defs=days(1), shift_defs=shifts(DAY_SHIFT),
        employees=[emp("E01", unavailable=[UnavailableSlot(day="d1", reason="请假")])],
        rules=[
            rule("U", RULE_RESPECT_UNAVAILABILITY, enabled=False),
            rule("S", RULE_SKILL_INTEGRITY, enabled=False),
        ],
    )
    rep = check(cfg, {("d1", "早"): ["E01", "E99"]})
    assert sorted(failed_rules(rep)) == ["S", "U"]


# ---------- 8. skill_source_integrity（locked） ----------


def test_skill_source_integrity_rejects_unknown_employee():
    cfg = config(
        day_defs=days(1), shift_defs=shifts(DAY_SHIFT),
        employees=[emp("E01", skills=["咖啡"])], skill_pool=["咖啡"],
        rules=[rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1)],
    )
    rep = check(cfg, {("d1", "早"): ["E01", "X9"]})
    assert "R-09" in failed_rules(rep)
    assert next(v for v in rep.violations if v.rule_id == "R-09").employee_id == "X9"
    # 档案外的人不许为资质要求「充数」：技能只能从档案查（R-09 的可执行形态）
    only_unknown = check(cfg, {("d1", "早"): ["X9"]})
    assert "A1" in failed_rules(only_unknown)


# ---------- 引擎行为 ----------


def test_unknown_rule_type_does_not_break_validation():
    """来自旧版本 localStorage 的未知 type 只由配置自检报错，校验本身不能 500。"""
    cfg = config(day_defs=days(1), shift_defs=shifts(DAY_SHIFT), employees=emps(2),
                 rules=[rule("Z", "forbid_pair_same_shift", a="E01", b="E02")])
    rep = check(cfg, {("d1", "早"): ["E01", "E02"]})
    assert rep.passed
    assert "Z" in [r.rule_id for r in rep.rule_results]   # 规则清单里仍要出现，否则用户以为它没了


def test_rule_results_cover_every_configured_rule():
    cfg = config(
        day_defs=days(1), shift_defs=shifts(DAY_SHIFT), employees=emps(2),
        rules=[rule("MS", RULE_MIN_STAFF, default=1), rule("MX", RULE_MAX_SHIFTS, max=1)],
    )
    rep = check(cfg, {("d1", "早"): ["E01"]})
    assert [r.rule_id for r in rep.rule_results] == ["MS", "MX", "R-08", "R-09"]


def test_unnamed_rule_gets_readable_name():
    """自建规则没填 name 时不能只显示编号：校验面板上一串 "C-1" 等于没有解释。"""
    from app.config import RuleDef

    r = RuleDef(id="C-1", type=RULE_REQUIRE_ATTRIBUTE, params={"attr": "skill", "value": "咖啡", "min": 2})
    assert r.name == "每班至少 2 人具备「咖啡」"
    assert RuleDef(id="C-2", type=RULE_MIN_STAFF, params={"default": 3, "peak": 5}).name == "每班至少 3 人（高峰日 5 人）"
    # 用户填了名字就以用户的为准
    assert RuleDef(id="C-3", type=RULE_MIN_STAFF, name="周末加人", params={"default": 3}).name == "周末加人"
