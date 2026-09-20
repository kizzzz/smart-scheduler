"""配置自检：契约 5.2 要求的 8 类 error 必须都能检出，且不能误报。

误报比漏报更糟：一条假 error 会把用户挡在「生成」之外，而他手里的配置其实是可解的。
所以每个用例都同时断言「该报的报了」和「不该报的没报」。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config_check  # noqa: E402
from app import data  # noqa: E402
from app.config import (  # noqa: E402
    MAX_DAYS,
    RULE_MAX_SHIFTS,
    RULE_MIN_STAFF,
    RULE_REQUIRE_ATTRIBUTE,
    UnavailableSlot,
    default_config,
)
from config_factory import config, days, emp, emps, rule, shifts  # noqa: E402
from config_factory import DAY_SHIFT, NIGHT_SHIFT  # noqa: E402

TWO_SHIFTS = shifts(DAY_SHIFT, NIGHT_SHIFT)


def codes(result, key="errors"):
    return [i["code"] for i in result[key]]


# ---------- 默认配置必须干净 ----------


def test_default_config_passes_self_check():
    out = config_check.check(default_config())
    assert out["ok"] is True and out["errors"] == []
    cap = out["capacity"]
    assert cap["demand_person_shifts"] == 4 * 10 + 6 * 4        # 5 个工作日 × 2 班 × 4 人 + 周末 × 6 人
    assert cap["supply_person_shifts"] > cap["demand_person_shifts"]
    assert len(cap["per_slot"]) == 14
    monday_pool = [
        e for e in data.EMPLOYEES.values() if "一" in e.available_days and "一" not in e.leave_days
    ]
    assert cap["per_slot"][0] == {
        "day": "一", "shift": "早班", "min_required": 4, "eligible": len(monday_pool),
    }
    # 默认配置有 34% 冗余，不该出现「没有余量」的告警
    assert "no_headroom" not in codes(out, "warnings")
    # 也不该出现按天的告警：最紧的周一是 10 人对 8 人次，仍有余量
    assert "no_daily_headroom" not in codes(out, "warnings")


def test_check_accepts_none_as_default():
    assert config_check.check() == config_check.check(default_config())


# ---------- 8 类必检 error ----------


def test_no_employees():
    out = config_check.check(config(employees=[]))
    assert "no_employees" in codes(out) and out["ok"] is False


def test_supply_lt_demand_points_at_the_slot():
    cfg = config(
        day_defs=days(2), shift_defs=TWO_SHIFTS,
        employees=[emp("E01"), emp("E02"), emp("E03", unavailable=[UnavailableSlot(day="d1")])],
        rules=[rule("MS", RULE_MIN_STAFF, default=3)],
    )
    out = config_check.check(cfg)
    hit = [e for e in out["errors"] if e["code"] == "supply_lt_demand"]
    # d1 只有 2 人可排、需要 3 人；d2 三人齐全，不该被牵连
    assert {(e["where"]["day"], e["where"]["shift"]) for e in hit} == {("d1", "早"), ("d1", "晚")}
    assert "第1天早需要 3 人" in hit[0]["message"]
    assert hit[0]["fix"]


def test_attribute_absent():
    cfg = config(
        day_defs=days(1), shift_defs=shifts(DAY_SHIFT), employees=emps(3), skill_pool=["咖啡"],
        rules=[rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1)],
    )
    out = config_check.check(cfg)
    assert "attribute_absent" in codes(out)
    assert next(e for e in out["errors"] if e["code"] == "attribute_absent")["where"]["rule_id"] == "A1"


def test_attribute_supply_lt_demand():
    cfg = config(
        day_defs=days(2), shift_defs=shifts(DAY_SHIFT),
        employees=[
            emp("E01", skills=["咖啡"], unavailable=[UnavailableSlot(day="d2")]),
            emp("E02"), emp("E03"),
        ],
        skill_pool=["咖啡"],
        rules=[rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1)],
    )
    out = config_check.check(cfg)
    hit = [e for e in out["errors"] if e["code"] == "attribute_supply_lt_demand"]
    assert [e["where"]["day"] for e in hit] == ["d2"]
    # 属性存在于员工池，所以不该报 attribute_absent
    assert "attribute_absent" not in codes(out)


def test_capacity_lt_total_demand():
    cfg = config(
        day_defs=days(3), shift_defs=TWO_SHIFTS,
        employees=[emp(f"E{i:02d}", max_shifts=1) for i in range(1, 7)],
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("MX", RULE_MAX_SHIFTS, max=1)],
    )
    out = config_check.check(cfg)
    # 需求 12 人次，6 人 × 每人 1 班 = 6 人次
    assert "capacity_lt_total_demand" in codes(out)
    assert out["capacity"]["supply_person_shifts"] == 6
    assert out["capacity"]["demand_person_shifts"] == 12


def test_empty_scenario():
    assert "empty_scenario" in codes(config_check.check(config(day_defs=[])))
    assert "empty_scenario" in codes(config_check.check(config(shift_defs=[])))


def test_duplicate_employee_id():
    cfg = config(employees=[emp("E01"), emp("E01"), emp("E02")])
    out = config_check.check(cfg)
    assert "duplicate_employee_id" in codes(out) and "E01" in out["errors"][0]["message"]


def test_unknown_skill():
    cfg = config(employees=[emp("E01", skills=["调酒"])], skill_pool=["咖啡"])
    out = config_check.check(cfg)
    hit = next(e for e in out["errors"] if e["code"] == "unknown_skill")
    assert hit["where"]["employee_id"] == "E01" and "调酒" in hit["message"]


# ---------- 结构性问题与告警 ----------


def test_scenario_too_large_is_rejected_before_solving():
    cfg = config(day_defs=days(MAX_DAYS + 1), shift_defs=TWO_SHIFTS)
    assert "scenario_too_large" in codes(config_check.check(cfg))


def test_duplicate_dimension_ids():
    cfg = config(day_defs=[*days(1), *days(1)], shift_defs=TWO_SHIFTS)
    assert "duplicate_day_id" in codes(config_check.check(cfg))
    dup_shift = config(shift_defs=[*shifts(DAY_SHIFT), *shifts(DAY_SHIFT)])
    assert "duplicate_shift_id" in codes(config_check.check(dup_shift))


def test_unknown_rule_type_and_invalid_params():
    cfg = config(rules=[
        rule("Z", "forbid_pair_same_shift", a="E01"),
        rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="", min=0),
    ])
    out = config_check.check(cfg)
    assert "unknown_rule_type" in codes(out) and "invalid_rule_params" in codes(out)


def test_no_headroom_is_warning_not_error():
    """供给刚好等于需求仍然可解，只能是 warning——报 error 会拦住一份能用的配置。"""
    cfg = config(
        day_defs=days(1), shift_defs=shifts(DAY_SHIFT),
        employees=[emp("E01", max_shifts=1), emp("E02", max_shifts=1)],
        rules=[rule("MS", RULE_MIN_STAFF, default=2), rule("MX", RULE_MAX_SHIFTS, max=1)],
    )
    out = config_check.check(cfg)
    assert out["ok"] is True
    assert "no_headroom" in codes(out, "warnings")
    assert out["capacity"]["headroom_pct"] == 0.0


def test_locked_rule_disabled_gives_warning_only():
    cfg = default_config()
    for r in cfg.rules:
        if r.locked:
            r.enabled = False
    out = config_check.check(cfg)
    assert out["ok"] is True
    assert codes(out, "warnings").count("locked_rule_forced") == 2


def test_dangling_unavailable_reference_is_warning():
    cfg = config(employees=[emp("E01", unavailable=[UnavailableSlot(day="星期八")]), *emps(3, start=2)])
    out = config_check.check(cfg)
    assert out["ok"] is True and "unknown_unavailable_ref" in codes(out, "warnings")


def test_slot_level_errors_are_truncated():
    """同类问题只列前几条：一个「全员某天不可用」的误操作不该刷出几十条 error。"""
    cfg = config(
        day_defs=days(7), shift_defs=TWO_SHIFTS, employees=emps(1),
        rules=[rule("MS", RULE_MIN_STAFF, default=2)],
    )
    out = config_check.check(cfg)
    hit = [e for e in out["errors"] if e["code"] == "supply_lt_demand"]
    assert len(hit) == config_check.MAX_PER_CODE + 1
    assert "另有" in hit[-1]["message"]
