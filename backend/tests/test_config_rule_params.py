"""规则参数的合法性：非法参数必须在入口被拒绝，而不是由各引擎自行解释。

这一组用例盯的是同一类 bug：`max_consecutive_days.max = 0` 字面意思是「谁都不许上班」，
而用户想说的几乎一定是「不限制」。任何一方替他挑一种解释，都会产生
「solver 判死 / validator 判合规」这种两个答案的局面。所以：

- config_check 一律报 invalid_rule_params（error，拦在生成之前）；
- 两个引擎一律把非法参数当成「这条规则不生效」，口径写在 config.valid_limit 里。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config_check, rules, solver, validator  # noqa: E402
from app.config import (  # noqa: E402
    RULE_MAX_CONSECUTIVE,
    RULE_MAX_SHIFTS,
    RULE_MIN_REST,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    default_config,
    index_of,
    valid_hours,
    valid_limit,
)
from app.models import ScheduleRequest  # noqa: E402
from config_factory import DAY_SHIFT, NIGHT_SHIFT, config, days, emp, emps, rule, sched, shifts  # noqa: E402

TWO_SHIFTS = shifts(DAY_SHIFT, NIGHT_SHIFT)


def codes(result, key="errors"):
    return [i["code"] for i in result[key]]


def solve(cfg):
    return solver.solve(ScheduleRequest(action="generate"), None, config=cfg)


def params_errors(*rule_defs):
    out = config_check.check(config(day_defs=days(3), shift_defs=TWO_SHIFTS,
                                   employees=emps(6, skills=("咖啡",)), skill_pool=("咖啡",),
                                   rules=list(rule_defs)))
    return [e for e in out["errors"] if e["code"] == "invalid_rule_params"]


# ---------- 口径本身 ----------


@pytest.mark.parametrize("raw,expected", [
    (3, 3), ("3", 3), (3.0, 3),
    (1, 1),                     # 下界 1 有效
    (0, None), (-1, None),      # 0 与负数都不是「不限制」
    (None, None), ("", None), ("很多", None),
])
def test_valid_limit_boundaries(raw, expected):
    assert valid_limit(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (12, 12.0), ("13.5", 13.5), (0.5, 0.5),
    (0, None), (-1, None), (None, None), ("半天", None),
])
def test_valid_hours_boundaries(raw, expected):
    assert valid_hours(raw) == expected


# ---------- 每类模板的非法参数 ----------


@pytest.mark.parametrize("bad_rule", [
    rule("MC", RULE_MAX_CONSECUTIVE, max=0),
    rule("MC", RULE_MAX_CONSECUTIVE, max=-2),
    rule("MC", RULE_MAX_CONSECUTIVE, max="两天"),
    rule("MC", RULE_MAX_CONSECUTIVE),                               # 缺参数
    rule("MX", RULE_MAX_SHIFTS, max=0),
    rule("MX", RULE_MAX_SHIFTS, max=-1),
    rule("MX", RULE_MAX_SHIFTS),
    rule("MR", RULE_MIN_REST, hours=0),
    rule("MR", RULE_MIN_REST, hours=-12),
    rule("MR", RULE_MIN_REST, hours="十二"),
    rule("MR", RULE_MIN_REST),
    rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=0),
    rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="", min=1),
    rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="   ", min=1),
    rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="certificate", value="咖啡", min=1),
    rule("MS", RULE_MIN_STAFF, default=-1),
    rule("MS", RULE_MIN_STAFF, default=2, peak="三"),
    rule("MS", RULE_MIN_STAFF, default=2, overrides=[{"day": "d1", "shift": "早", "min": -1}]),
    rule("MS", RULE_MIN_STAFF, default=2, overrides=[{"day": "d1", "shift": "早"}]),
    rule("MS", RULE_MIN_STAFF, default=2, overrides=["d1早班3人"]),
    rule("MS", RULE_MIN_STAFF, default=2, overrides="周六加人"),
])
def test_illegal_params_are_errors(bad_rule):
    hit = params_errors(bad_rule)
    assert hit, f"{bad_rule.type} 的非法参数被放行了"
    # error 必须挂在具体规则上，配置页才能把红字画在这条规则的卡片里
    assert hit[0]["where"] == {"rule_id": bad_rule.id}
    assert hit[0]["fix"]


@pytest.mark.parametrize("good_rule", [
    rule("MC", RULE_MAX_CONSECUTIVE, max=1),
    rule("MC", RULE_MAX_CONSECUTIVE, max=3),
    rule("MX", RULE_MAX_SHIFTS, max=1),
    rule("MR", RULE_MIN_REST, hours=0.5),
    rule("MR", RULE_MIN_REST, hours=13),
    rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1),
    rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="role", value="店员", min=1),
    rule("MS", RULE_MIN_STAFF, default=1),
    rule("MS", RULE_MIN_STAFF, default=0),                          # 0 人下限是合法表达：这一格不设要求
    rule("MS", RULE_MIN_STAFF, default=1, peak=2),
    rule("MS", RULE_MIN_STAFF, default=1, overrides=[{"day": "d1", "shift": "早", "min": 0}]),
    rule("OS", RULE_ONE_SHIFT_PER_DAY),                             # 无参数模板不该被要求参数
])
def test_legal_params_are_not_flagged(good_rule):
    assert params_errors(good_rule) == []


def test_default_config_has_no_param_errors():
    """默认 10 条规则是参数校验的回归基线：它一旦被误判，所有默认链路都会 400。"""
    out = config_check.check(default_config())
    assert out["ok"] is True
    assert "invalid_rule_params" not in codes(out)
    assert "rest_forces_day_gap" not in codes(out, "warnings")


def test_max_consecutive_zero_message_offers_both_ways_out():
    """0 的两种可能解释都不采纳，但要把「怎么写才对」说清楚，否则用户会再填一次 0。"""
    hit = params_errors(rule("MC", RULE_MAX_CONSECUTIVE, max=0))[0]
    assert "最多连续工作天数" in hit["message"] and "≥1" in hit["message"]
    assert "3" in hit["fix"] and "停用" in hit["fix"]        # 3 = 用例里的周期天数


def test_disabled_rule_with_illegal_params_is_not_blocking():
    """停用的规则不参与求解，为它的参数拦住整份配置属于误拦。"""
    cfg = config(day_defs=days(3), shift_defs=TWO_SHIFTS, employees=emps(6),
                 rules=[rule("MC", RULE_MAX_CONSECUTIVE, enabled=False, max=0)])
    out = config_check.check(cfg)
    assert out["ok"] is True and "invalid_rule_params" not in codes(out)


# ---------- 两个引擎同口径 ----------


def test_zero_max_consecutive_means_rule_off_in_both_engines():
    """非法上限下，solver 与 validator 必须给出同一个答案（都当它不生效）。

    改造前这里是分裂的：solver 按字面把 0 当成「连 1 天都不行」→ 谁都排不了 →
    无解；validator 用 `<= 0 → 跳过` 兜底 → 同一张表判合规。
    """
    cfg = config(day_defs=days(2), shift_defs=shifts(DAY_SHIFT),
                 employees=emps(1),
                 rules=[rule("MS", RULE_MIN_STAFF, default=1), rule("MC", RULE_MAX_CONSECUTIVE, max=0)])
    idx = index_of(cfg)
    assert idx.max_consecutive_days is None                  # 编译期就归零成「没配」
    assert idx.max_work_days("E01") == 2                     # 不再被 0 折成不能上班

    schedule, _trace, _diag = solve(cfg)
    assert schedule is not None, "非法参数不该表现为无解"
    report = validator.validate(schedule, config=cfg)
    assert report.passed is True
    # 规则清单里仍然有它（用户看得到这条规则存在），只是没有违规
    assert "MC" in [r.rule_id for r in report.rule_results]


def test_zero_max_shifts_means_rule_off_in_both_engines():
    cfg = config(day_defs=days(2), shift_defs=shifts(DAY_SHIFT), employees=emps(1),
                 rules=[rule("MS", RULE_MIN_STAFF, default=1), rule("MX", RULE_MAX_SHIFTS, max=0)])
    assert index_of(cfg).max_shifts("E01") is None
    schedule, _trace, _diag = solve(cfg)
    assert schedule is not None
    assert validator.validate(schedule, config=cfg).passed is True


def test_personal_max_shifts_zero_is_taken_literally():
    """个人上限 0 不是「没配」：solver 不排这个人，校验也必须把排了他判成违规。

    这里刻意与规则参数的 0 相反——个人上限是「这个人本周期不排」的正当写法，
    两个引擎照字面执行才一致；当成「没配」回落到全局规则才会造成分裂。
    """
    cfg = config(day_defs=days(1), shift_defs=shifts(DAY_SHIFT),
                 employees=[emp("E01", max_shifts=0), emp("E02")],
                 rules=[rule("MX", RULE_MAX_SHIFTS, max=5)])
    assert index_of(cfg).max_shifts("E01") == 0
    report = validator.validate(sched({("d1", "早"): ["E01"]}), config=cfg)
    assert [v.employee_id for v in report.violations if v.rule_id == "MX"] == ["E01"]
    assert validator.validate(sched({("d1", "早"): ["E02"]}), config=cfg).passed is True


def test_dirty_attribute_min_does_not_crash_solver():
    """脏参数最坏也只能让规则不生效，不能变成 500。"""
    cfg = config(day_defs=days(1), shift_defs=shifts(DAY_SHIFT), employees=emps(2, skills=("咖啡",)),
                 skill_pool=("咖啡",),
                 rules=[rule("MS", RULE_MIN_STAFF, default=1),
                        rule("RA", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min="一名")])
    schedule, _trace, _diag = solve(cfg)
    assert schedule is not None
    assert validator.validate(schedule, config=cfg).passed is True
    assert rules.run(rules.build_view(schedule, index_of(cfg))) == []


# ---------- 休息间隔跨天：容量下界 ----------


def test_rest_over_a_day_is_folded_into_capacity():
    """休息间隔跨过一整天时，可排天数会被腰斩——容量估算必须知道，否则放行一份必然无解的配置。

    2 天 × 1 班、每班 1 人、只有 1 名员工、要求休息 30 小时：早班 09:00–13:00，次日早班
    最多只能隔 20 小时，所以这个人两天里只能上 1 个班，demand 2 > supply 1，必然无解。
    折进来之前 supply 会被算成 2，自检放行，用户要等一次求解才看到「无解」。
    """
    cfg = config(day_defs=days(2), shift_defs=shifts(DAY_SHIFT), employees=emps(1),
                 rules=[rule("MS", RULE_MIN_STAFF, default=1), rule("MR", RULE_MIN_REST, hours=30)])
    idx = index_of(cfg)
    assert idx.min_day_gap() == 2 and idx.max_work_days("E01") == 1

    out = config_check.check(cfg)
    assert out["ok"] is False
    assert "capacity_lt_total_demand" in codes(out)
    assert out["capacity"]["supply_person_shifts"] == 1
    # 求解器确实证明无解：自检与它结论一致，而不是抢在它前面猜
    assert solve(cfg)[0] is None


def test_rest_over_a_day_warns_even_when_solvable():
    """够用的时候不能报 error，但要解释清楚供给为什么变少了。"""
    cfg = config(day_defs=days(3), shift_defs=shifts(DAY_SHIFT), employees=emps(3),
                 rules=[rule("MS", RULE_MIN_STAFF, default=1), rule("MR", RULE_MIN_REST, hours=30)])
    out = config_check.check(cfg)
    assert out["ok"] is True
    hit = [w for w in out["warnings"] if w["code"] == "rest_forces_day_gap"]
    assert hit and hit[0]["where"] == {"rule_id": "MR"}
    assert "隔 2 天" in hit[0]["message"]
    assert solve(cfg)[0] is not None


def test_normal_rest_hours_do_not_trigger_day_gap():
    """默认那种 13 小时的休息间隔只约束当天与隔夜，不该被误判成跨天。"""
    cfg = config(day_defs=days(3), shift_defs=TWO_SHIFTS, employees=emps(4),
                 rules=[rule("MR", RULE_MIN_REST, hours=13)])
    assert index_of(cfg).min_day_gap() == 1
    assert "rest_forces_day_gap" not in codes(config_check.check(cfg), "warnings")


# ---------- 悬空的单格覆盖 ----------


def test_dangling_override_reference_is_warning():
    """悬空覆盖不会错排，但会静默失效：用户以为「第 9 天晚班已经加到 3 人」。"""
    cfg = config(day_defs=days(2), shift_defs=TWO_SHIFTS, employees=emps(6),
                 rules=[rule("MS", RULE_MIN_STAFF, default=1,
                             overrides=[{"day": "d9", "shift": "晚", "min": 3}])])
    out = config_check.check(cfg)
    assert out["ok"] is True
    hit = [w for w in out["warnings"] if w["code"] == "unknown_override_ref"]
    assert hit and "d9/晚" in hit[0]["message"]
