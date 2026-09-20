"""按天容量：配置自检必须和 solver.diagnose 报出同一批「必然无解」。

单独一个文件，因为这类 bug 有固定形状：**总量够、每一格单独看也够，但某一天凑不齐**。
自检漏掉这一层的后果不是少一条提示，而是配置页说「没问题」、点生成拿到一条求解器
证明的无解——用户会去砍规则，而真正该做的是给那天补人。所以每个用例都成对断言：
自检报了什么，求解器是不是同一个结论。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config_check  # noqa: E402
from app import solver  # noqa: E402
from app.config import (  # noqa: E402
    RULE_MAX_CONSECUTIVE,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    index_of,
)
from app.models import ScheduleRequest  # noqa: E402
from config_factory import config, days, emp, emps, rule, shifts  # noqa: E402
from config_factory import DAY_SHIFT, MID_SHIFT, NIGHT_SHIFT  # noqa: E402

# 默认那两班（09–17 / 13–21）是重叠的，一个人一天照样只能上一个班；
# 要测「关掉 one_shift_per_day 就该放宽」必须用互不重叠的三班
THREE_SHIFTS = shifts(DAY_SHIFT, MID_SHIFT, NIGHT_SHIFT)
OVERLAPPING = shifts(("早班", "09:00", "17:00"), ("晚班", "13:00", "21:00"))


def codes(result, key="errors"):
    return [i["code"] for i in result[key]]


def repro(one_shift_per_day: bool = True):
    """3 天 × 3 班、8 人，第 3 天高峰 3 人/班 → 当天 9 人次。

    全周期需求 21 人次、供给 24 人次，每一格单独看也有 8 人可排，
    唯一的问题就在「第 3 天需要 9 个不同的人，但只有 8 个人」。
    """
    rules = [rule("MS", RULE_MIN_STAFF, default=2, peak=3)]
    if one_shift_per_day:
        rules.append(rule("R-10", RULE_ONE_SHIFT_PER_DAY))
    return config(day_defs=days(3, peak=("d3",)), shift_defs=THREE_SHIFTS,
                  employees=emps(8), rules=rules)


# ---------- 人数：daily_capacity_lt_demand ----------


def test_daily_capacity_lt_demand_is_error_under_one_shift_per_day():
    out = config_check.check(repro())
    assert out["ok"] is False
    hit = [e for e in out["errors"] if e["code"] == "daily_capacity_lt_demand"]
    assert len(hit) == 1
    assert hit[0]["message"] == (
        "第3天：全天可排班 8 人，当天各班共需 9 人次（规则 R-10 限制每人每天最多 1 个班）"
    )
    # where 指到天：前端要把红点画在这一天上
    assert hit[0]["where"] == {"day": "d3", "rule_id": "MS"}
    # 三条出路都要给，其中「关掉 R-10」只在这条规则真的存在时才提
    assert "降低当天的人数下限" in hit[0]["fix"] and "停用规则 R-10" in hit[0]["fix"]
    assert "补上可排班的员工" in hit[0]["fix"]
    # 总量和单格都不是瓶颈，不该顺带报出来误导用户
    assert "capacity_lt_total_demand" not in codes(out)
    assert "supply_lt_demand" not in codes(out)


def test_self_check_and_solver_agree_on_the_same_day():
    """自检的口径必须和求解器一致：同一份配置，同一个数字，同一天。"""
    cfg = repro()
    schedule, _trace, diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is None and diag is not None and diag.kind == "proven_infeasible"
    evidence = "第3天：全天可排班 8 人，当天各班共需 9 人次"
    assert any(e.startswith(evidence) for e in diag.evidence)
    hit = next(e for e in config_check.check(cfg)["errors"] if e["code"] == "daily_capacity_lt_demand")
    assert hit["message"].startswith(evidence)


def test_disabling_one_shift_per_day_clears_the_error():
    """关掉 R-10 后一人一天可以上 3 个不重叠的班，8 人排 9 人次成立 → 不能再拦。"""
    cfg = repro(one_shift_per_day=False)
    out = config_check.check(cfg)
    assert out["ok"] is True and out["errors"] == []
    assert "no_daily_headroom" not in codes(out, "warnings")
    schedule, _trace, _diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is not None


def test_overlapping_shifts_still_error_without_one_shift_per_day():
    """判定门槛是「可证明」而不是「规则开没开」。

    班次两两重叠时，即使没有 one_shift_per_day，一个人一天也只能上一个班——
    默认的 09–17 / 13–21 正是这种。按「规则是否启用」判会在这里放行一份必然无解的配置。
    """
    cfg = config(day_defs=days(1), shift_defs=OVERLAPPING, employees=emps(8),
                 rules=[rule("MS", RULE_MIN_STAFF, default=5)])
    assert index_of(cfg).one_shift_per_day is False
    out = config_check.check(cfg)
    hit = next(e for e in out["errors"] if e["code"] == "daily_capacity_lt_demand")
    assert "全天可排班 8 人，当天各班共需 10 人次" in hit["message"]
    assert "班次时间重叠或最小休息间隔限制" in hit["message"]
    # 没有 R-10 就不能建议「停用 R-10」，那是一条不存在的规则
    assert "停用规则" not in hit["fix"]
    schedule, _trace, diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is None and diag.kind == "proven_infeasible"


def test_min_rest_hours_can_also_pin_a_day_to_one_shift():
    """休息间隔把三班压回「一天一个班」时，同样要按 1 折算。"""
    cfg = config(
        day_defs=days(1), shift_defs=THREE_SHIFTS, employees=emps(4),
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("RS", "min_rest_hours", hours=10)],
    )
    assert index_of(cfg).max_shifts_per_day() == 1
    out = config_check.check(cfg)
    hit = next(e for e in out["errors"] if e["code"] == "daily_capacity_lt_demand")
    assert "全天可排班 4 人，当天各班共需 6 人次" in hit["message"]


def test_day_with_slot_level_error_is_not_double_reported():
    """同一天既缺人到格子级、又按天不够时只报格子级：那条更可操作。"""
    cfg = config(
        day_defs=days(1), shift_defs=THREE_SHIFTS, employees=emps(2),
        rules=[rule("MS", RULE_MIN_STAFF, default=3), rule("R-10", RULE_ONE_SHIFT_PER_DAY)],
    )
    out = config_check.check(cfg)
    assert "supply_lt_demand" in codes(out)
    assert "daily_capacity_lt_demand" not in codes(out)


def test_daily_capacity_exactly_enough_is_warning_not_error():
    """刚好够仍然可解，只能是 warning——报 error 会拦住一份能用的配置。"""
    cfg = config(
        day_defs=days(3), shift_defs=THREE_SHIFTS, employees=emps(6),
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("R-10", RULE_ONE_SHIFT_PER_DAY)],
    )
    out = config_check.check(cfg)
    assert out["ok"] is True
    assert codes(out, "warnings").count("no_daily_headroom") == 3
    warn = next(w for w in out["warnings"] if w["code"] == "no_daily_headroom")
    assert warn["where"] == {"day": "d1"} and "一个人都不能请假" in warn["message"]
    schedule, _trace, _diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is not None


def test_daily_errors_are_truncated():
    cfg = config(
        day_defs=days(7), shift_defs=THREE_SHIFTS, employees=emps(4),
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("R-10", RULE_ONE_SHIFT_PER_DAY)],
    )
    hit = [e for e in config_check.check(cfg)["errors"] if e["code"] == "daily_capacity_lt_demand"]
    assert len(hit) == config_check.MAX_PER_CODE + 1 and "另有" in hit[-1]["message"]


# ---------- 资质：daily_attribute_capacity_lt_demand ----------


def test_daily_attribute_capacity_lt_demand():
    """每班要 1 名持证员工、一天 2 个班、一人一天只能上一个班 → 当天要 2 名持证员工。

    单格看永远够（那 1 个人每格都能排），只有按天看才发现同一个人分不成两半。
    """
    cfg = config(
        day_defs=days(2), shift_defs=shifts(DAY_SHIFT, NIGHT_SHIFT),
        employees=[emp("E01", skills=["收银"]), *emps(3, start=2)],
        rules=[
            rule("MS", RULE_MIN_STAFF, default=1),
            rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="收银", min=1),
            rule("R-10", RULE_ONE_SHIFT_PER_DAY),
        ],
        skill_pool=["收银"],
    )
    out = config_check.check(cfg)
    assert "attribute_supply_lt_demand" not in codes(out)      # 单格永远够，这里不该命中
    hit = [e for e in out["errors"] if e["code"] == "daily_attribute_capacity_lt_demand"]
    assert [e["where"] for e in hit] == [{"day": "d1", "rule_id": "A1"}, {"day": "d2", "rule_id": "A1"}]
    assert hit[0]["message"] == (
        "第1天：全天仅 1 名具备收银技能的员工可排班，但当天 2 个班共需 2 名不同员工"
        "（规则 R-10 限制每人每天最多 1 个班）"
    )
    assert "停用规则 A1" in hit[0]["fix"]
    schedule, _trace, diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is None and diag.kind == "proven_infeasible"


def test_daily_attribute_ok_when_one_person_can_cover_both_shifts():
    """同一份员工池，关掉 R-10 后一个人就能覆盖两个班 → 不能再报。"""
    cfg = config(
        day_defs=days(2), shift_defs=shifts(DAY_SHIFT, NIGHT_SHIFT),
        employees=[emp("E01", skills=["收银"]), *emps(3, start=2)],
        rules=[
            rule("MS", RULE_MIN_STAFF, default=1),
            rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="收银", min=1),
        ],
        skill_pool=["收银"],
    )
    out = config_check.check(cfg)
    assert out["ok"] is True and out["errors"] == []
    schedule, _trace, _diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is not None


# ---------- 连班上限也是容量上界 ----------


def test_max_consecutive_days_caps_the_supply():
    """7 天里最多连上 5 天 → 一个人最多上 6 天，凑不满 7 天的需求。

    不把连班上限折进供给时，「全员上限之和」会算成 7 人次、刚好等于需求而放行，
    但求解器一定排不出来。
    """
    cfg = config(
        day_defs=days(7), shift_defs=shifts(DAY_SHIFT), employees=emps(1),
        rules=[
            rule("MS", RULE_MIN_STAFF, default=1),
            rule("MC", RULE_MAX_CONSECUTIVE, max=5),
        ],
    )
    out = config_check.check(cfg)
    assert out["capacity"]["supply_person_shifts"] == 6        # 不是 7：第 6 天必须休息
    assert "capacity_lt_total_demand" in codes(out)
    schedule, _trace, _diag = solver.solve(ScheduleRequest(), None, config=cfg)
    assert schedule is None


def test_max_consecutive_days_does_not_shrink_supply_when_it_cannot_bite():
    """连班上限 ≥ 周期长度时不该影响容量估算——那条规则在这个周期里根本不可能被触发。"""
    cfg = config(
        day_defs=days(5), shift_defs=shifts(DAY_SHIFT), employees=emps(2),
        rules=[
            rule("MS", RULE_MIN_STAFF, default=1),
            rule("MC", RULE_MAX_CONSECUTIVE, max=5),
        ],
    )
    out = config_check.check(cfg)
    assert out["capacity"]["supply_person_shifts"] == 10 and out["ok"] is True


# ---------- 两个方向都不许错：自检 ⊇ 求解器的结构性证明，且不得误拦 ----------

STRUCTURAL_EVIDENCE = ("低于人数下限", "全天可排班", "全天仅", "全周期供给上限", "不可能满足")
CAPACITY_CODES = {
    "supply_lt_demand", "attribute_supply_lt_demand", "attribute_absent",
    "daily_capacity_lt_demand", "daily_attribute_capacity_lt_demand", "capacity_lt_total_demand",
}


def test_sweep_agrees_with_solver_in_both_directions():
    """沿人数扫一遍边界（1～6 人 × R-10 开/关），逐个和求解器对拍。

    盯的是两类反向失败，任何一类都是真 bug：
    - 自检放行，求解器却用结构性下界证明无解（配置页说没问题、生成给一条本可提前给出的证据）；
    - 自检报容量 error，求解器却排得出来（误拦一份能用的配置，比漏报更糟）。
    组合式无解（求解器穷尽候选后才知道）不在此列：那需要真的跑一遍搜索，不是自检的职责。
    """
    for headcount in range(1, 7):
        for one_shift in (True, False):
            rules = [rule("MS", RULE_MIN_STAFF, default=2, peak=3)]
            if one_shift:
                rules.append(rule("R-10", RULE_ONE_SHIFT_PER_DAY))
            cfg = config(day_defs=days(2, peak=("d2",)), shift_defs=THREE_SHIFTS,
                         employees=emps(headcount), rules=rules)
            out = config_check.check(cfg)
            schedule, _trace, diag = solver.solve(ScheduleRequest(), None, config=cfg)
            case = f"{headcount} 人 / one_shift_per_day={one_shift}"
            if out["ok"]:
                proofs = [] if diag is None else [
                    e for e in diag.evidence if any(k in e for k in STRUCTURAL_EVIDENCE)
                ]
                assert not proofs, f"{case}：自检放行但求解器能证明无解 {proofs}"
            elif CAPACITY_CODES & {e["code"] for e in out["errors"]}:
                assert schedule is None, f"{case}：自检误拦了一份可解的配置"
