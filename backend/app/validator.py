"""L3 规则校验层 —— 全系统唯一真相源。

任何排班表，无论来自 LLM、求解器、手工微调还是外部导入，都必须过这一套校验。
本模块刻意不引用求解器，也不知道排班表是怎么来的。
"""
from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Set

from .data import (
    DAYS,
    EMPLOYEES,
    MAX_CONSECUTIVE_DAYS,
    MAX_SHIFTS_PER_WEEK,
    RULES,
    SHIFTS,
    SKILL_CASHIER,
    SKILL_DRINK,
    SKILL_KEEPER,
    WEEKEND,
    base_available,
    has_skill,
    min_required,
)
from .models import (
    RuleResult,
    Schedule,
    ScheduleRequest,
    SoftMetrics,
    ValidationReport,
    Violation,
)

_RULE_TEXT: Dict[str, str] = {r["id"]: r["text"] for r in RULES}


def _v(rule_id: str, detail: str, day=None, shift=None, employee_id=None) -> Violation:
    return Violation(
        rule_id=rule_id,
        rule_text=_RULE_TEXT[rule_id],
        day=day,
        shift=shift,
        employee_id=employee_id,
        detail=detail,
    )


def blocked_days(intent: Optional[ScheduleRequest]) -> Dict[str, Set[str]]:
    """把意图中的临时请假折算成 employee_id -> {不可排班的日期}。"""
    out: Dict[str, Set[str]] = {}
    if intent is None:
        return out
    for tl in intent.temp_leaves:
        out.setdefault(tl.employee_id, set()).update(tl.days)
    return out


def validate(schedule: Schedule, intent: Optional[ScheduleRequest] = None) -> ValidationReport:
    grid = schedule.as_map()
    extra_leave = blocked_days(intent)
    violations: List[Violation] = []
    counts: Dict[str, int] = {eid: 0 for eid in EMPLOYEES}

    # 逐班检查 R-01 ~ R-04、R-08、R-09
    for day in DAYS:
        for shift in SHIFTS:
            members = grid.get(f"{day}|{shift}", [])
            uniq = list(dict.fromkeys(members))
            if len(uniq) != len(members):
                violations.append(
                    _v("R-04", "同一班次出现重复员工", day=day, shift=shift)
                )
            for eid in uniq:
                counts[eid] = counts.get(eid, 0) + 1

                # R-09：员工必须存在于员工数据中
                if eid not in EMPLOYEES:
                    violations.append(
                        _v("R-09", f"{eid} 不在员工数据中", day=day, shift=shift, employee_id=eid)
                    )
                    continue

                # R-08：请假与不可工作日期
                e = EMPLOYEES[eid]
                if day not in e.available_days:
                    violations.append(
                        _v("R-08", f"{eid} 的可工作日期不含周{day}", day=day, shift=shift, employee_id=eid)
                    )
                elif day in e.leave_days:
                    violations.append(
                        _v("R-08", f"{eid} 周{day}请假", day=day, shift=shift, employee_id=eid)
                    )
                elif day in extra_leave.get(eid, set()):
                    violations.append(
                        _v("R-08", f"{eid} 周{day}临时请假", day=day, shift=shift, employee_id=eid)
                    )

            valid_members = [x for x in uniq if x in EMPLOYEES]

            # R-01 店长值守
            keepers = [x for x in valid_members if has_skill(x, SKILL_KEEPER)]
            if len(keepers) < 1:
                violations.append(
                    _v("R-01", "本班无具备店长值守资格的员工", day=day, shift=shift)
                )
            # R-02 饮品制作 ≥2
            drinks = [x for x in valid_members if has_skill(x, SKILL_DRINK)]
            if len(drinks) < 2:
                violations.append(
                    _v("R-02", f"本班具备饮品制作技能的员工仅 {len(drinks)} 名，需 ≥2", day=day, shift=shift)
                )
            # R-03 收银 ≥1
            cashiers = [x for x in valid_members if has_skill(x, SKILL_CASHIER)]
            if len(cashiers) < 1:
                violations.append(
                    _v("R-03", "本班无具备收银技能的员工", day=day, shift=shift)
                )
            # R-04 人数下限
            need = min_required(day)
            if len(valid_members) < need:
                violations.append(
                    _v("R-04", f"本班 {len(valid_members)} 人，低于下限 {need} 人", day=day, shift=shift)
                )

    # R-05 每人每周最多 5 个班
    for eid, n in counts.items():
        if n > MAX_SHIFTS_PER_WEEK:
            violations.append(
                _v("R-05", f"{eid} 本周 {n} 个班（{n * 8} 小时），超出上限 {MAX_SHIFTS_PER_WEEK} 个班", employee_id=eid)
            )

    # R-06 / R-07 依赖每人的按天班次分布
    per_day: Dict[str, Dict[str, Set[str]]] = {
        eid: {d: set() for d in DAYS} for eid in EMPLOYEES
    }
    for day in DAYS:
        for shift in SHIFTS:
            for eid in dict.fromkeys(grid.get(f"{day}|{shift}", [])):
                if eid in per_day:
                    per_day[eid][day].add(shift)

    for eid, daymap in per_day.items():
        # 同日早晚班时间重叠（09:00–17:00 与 13:00–21:00），实际不可能同时在岗，
        # 且单日 16 小时必然挤占周工时，归入 R-05 工时口径
        for d in DAYS:
            if len(daymap[d]) == 2:
                violations.append(
                    _v("R-05", f"{eid} 周{d}被同时安排早班与晚班，两班时间重叠且单日达 16 小时", day=d, employee_id=eid)
                )

        # R-06 连续工作天数
        run = 0
        run_days: List[str] = []
        for d in DAYS:
            if daymap[d]:
                run += 1
                run_days.append(d)
                if run > MAX_CONSECUTIVE_DAYS:
                    violations.append(
                        _v(
                            "R-06",
                            f"{eid} 连续工作 {run} 天（周{'、周'.join(run_days)}），超出上限 {MAX_CONSECUTIVE_DAYS} 天",
                            employee_id=eid,
                        )
                    )
                    break
            else:
                run = 0
                run_days = []
        # R-07 晚班接次日早班
        for i in range(len(DAYS) - 1):
            d, nxt = DAYS[i], DAYS[i + 1]
            if "晚班" in daymap[d] and "早班" in daymap[nxt]:
                violations.append(
                    _v(
                        "R-07",
                        f"{eid} 周{d}晚班后被安排周{nxt}早班，间隔不足",
                        day=nxt,
                        shift="早班",
                        employee_id=eid,
                    )
                )

    # 按规则汇总
    rule_results: List[RuleResult] = []
    for r in RULES:
        n = sum(1 for x in violations if x.rule_id == r["id"])
        rule_results.append(
            RuleResult(rule_id=r["id"], rule_text=r["text"], passed=n == 0, violation_count=n)
        )

    return ValidationReport(
        passed=len(violations) == 0,
        violations=violations,
        rule_results=rule_results,
        per_employee_shifts={k: v for k, v in counts.items() if v},
        soft_metrics=soft_metrics(schedule),
    )


def soft_metrics(schedule: Schedule) -> SoftMetrics:
    """软约束满足度：不影响 passed，只用于展示排班质量。"""
    grid = schedule.as_map()
    hit = total = 0
    counts: Dict[str, int] = {eid: 0 for eid in EMPLOYEES}
    pt_weekend = pt_total = 0

    for day in DAYS:
        for shift in SHIFTS:
            for eid in dict.fromkeys(grid.get(f"{day}|{shift}", [])):
                if eid not in EMPLOYEES:
                    continue
                e = EMPLOYEES[eid]
                counts[eid] += 1
                if e.preference:
                    total += 1
                    if e.preference == shift:
                        hit += 1
                if e.is_part_time():
                    pt_total += 1
                    if day in WEEKEND:
                        pt_weekend += 1

    working = [v for v in counts.values() if v > 0]
    return SoftMetrics(
        preference_hit_rate=round(hit / total, 4) if total else 0.0,
        preference_hit=hit,
        preference_total=total,
        workload_min=min(working) if working else 0,
        workload_max=max(working) if working else 0,
        workload_stdev=round(statistics.pstdev(working), 3) if len(working) > 1 else 0.0,
        part_time_weekend_ratio=round(pt_weekend / pt_total, 4) if pt_total else 0.0,
        total_assignments=sum(counts.values()),
    )


def is_available(eid: str, day: str, intent: Optional[ScheduleRequest], extra: Dict[str, Set[str]] | None = None) -> bool:
    """求解器可复用的可用性判断，语义与 R-08 完全一致。"""
    if not base_available(eid, day):
        return False
    blocked = extra if extra is not None else blocked_days(intent)
    return day not in blocked.get(eid, set())
