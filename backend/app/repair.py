"""一键修复建议：把违规翻译成「换谁上谁」的具体动作。

约束：建议只能落在同一个班次内（remove / add），且必须先自证「换完之后这条违规确实消失、
且不引入新的违规」。做不到就不给建议 —— 宁可不建议，也不给店长挖坑。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import solver
from . import validator as V
from .data import (
    EMPLOYEE_IDS,
    SKILL_CASHIER,
    SKILL_DRINK,
    SKILL_KEEPER,
    has_skill,
    min_required,
)
from .models import Schedule, ScheduleRequest, ValidationReport, Violation

MAX_PER_VIOLATION = 2

_SKILL_OF_RULE = {"R-01": SKILL_KEEPER, "R-02": SKILL_DRINK, "R-03": SKILL_CASHIER}


def _state_of(schedule: Schedule) -> solver.State:
    st = solver.State()
    for s in schedule.slots:
        for eid in dict.fromkeys(s.employee_ids):
            if eid in st.counts:
                st.add(eid, s.day, s.shift)
    return st


def _slot_of(schedule: Schedule, day: str, shift: str):
    return next((s for s in schedule.slots if s.day == day and s.shift == shift), None)


def _try(
    schedule: Schedule,
    intent: Optional[ScheduleRequest],
    day: str,
    shift: str,
    remove: Optional[str],
    add: Optional[str],
    target: Violation,
) -> bool:
    """在副本上试一次改动，要求：目标违规消失，且总违规数严格下降。"""
    trial = schedule.model_copy(deep=True)
    slot = _slot_of(trial, day, shift)
    if slot is None:
        return False
    members = list(slot.employee_ids)
    if remove:
        if remove not in members:
            return False
        members.remove(remove)
    if add:
        if add in members:
            return False
        members.append(add)
    slot.employee_ids = members

    before = V.validate(schedule, intent)
    after = V.validate(trial, intent)
    if len(after.violations) >= len(before.violations):
        return False
    still = any(
        v.rule_id == target.rule_id and v.day == target.day and v.shift == target.shift
        and v.employee_id == target.employee_id
        for v in after.violations
    )
    return not still


def _candidates(ctx: solver.Context, st: solver.State, day: str, shift: str, exclude: set[str]) -> List[str]:
    pool = [e for e in EMPLOYEE_IDS if e not in exclude and solver.can_add(ctx, st, e, day, shift)]
    pool.sort(key=lambda e: (solver._score(ctx, st, e, day, shift), e))
    return pool


def suggest(
    schedule: Schedule, report: ValidationReport, intent: Optional[ScheduleRequest] = None
) -> Dict[Tuple[str, str, str, str], List[dict]]:
    """返回 {(rule_id, day, shift, employee_id): [suggestion, ...]}。"""
    if report.passed:
        return {}
    ctx = solver.build_context(intent or ScheduleRequest(), None)
    st = _state_of(schedule)
    out: Dict[Tuple[str, str, str, str], List[dict]] = {}

    for v in report.violations:
        if not v.day:
            continue
        shifts = [v.shift] if v.shift else ["早班", "晚班"]
        found: List[dict] = []
        for shift in shifts:
            slot = _slot_of(schedule, v.day, shift)
            if slot is None:
                continue
            members = list(slot.employee_ids)
            if v.employee_id and v.employee_id not in members:
                continue

            # 场景 A：某人不该在这个班（R-05/R-06/R-07/R-08/R-09）→ 换人
            if v.employee_id:
                pool = _candidates(ctx, st, v.day, shift, set(members) | {v.employee_id})
                for cand in pool[:6]:
                    if _try(schedule, intent, v.day, shift, v.employee_id, cand, v):
                        found.append({
                            "label": f"把 {v.employee_id} 换成 {cand}",
                            "day": v.day, "shift": shift, "remove": v.employee_id, "add": cand,
                        })
                        if len(found) >= MAX_PER_VIOLATION:
                            break
                if not found and _try(schedule, intent, v.day, shift, v.employee_id, None, v):
                    found.append({
                        "label": f"直接移除 {v.employee_id}",
                        "day": v.day, "shift": shift, "remove": v.employee_id, "add": None,
                    })

            # 场景 B：本班缺技能或缺人（R-01/R-02/R-03/R-04）→ 补人或换人
            else:
                skill = _SKILL_OF_RULE.get(v.rule_id)
                pool = _candidates(ctx, st, v.day, shift, set(members))
                if skill:
                    pool = [e for e in pool if has_skill(e, skill)]
                for cand in pool[:6]:
                    # 人数已达下限时优先「换」，否则直接「补」
                    if len(members) > min_required(v.day) and skill:
                        droppable = [
                            m for m in members
                            if not has_skill(m, skill) and m not in ctx.pinned.get(f"{v.day}|{shift}", set())
                        ]
                        for drop in droppable[:4]:
                            if _try(schedule, intent, v.day, shift, drop, cand, v):
                                found.append({
                                    "label": f"把 {drop} 换成 {cand}",
                                    "day": v.day, "shift": shift, "remove": drop, "add": cand,
                                })
                                break
                    if len(found) < MAX_PER_VIOLATION and _try(schedule, intent, v.day, shift, None, cand, v):
                        found.append({
                            "label": f"补入 {cand}",
                            "day": v.day, "shift": shift, "remove": None, "add": cand,
                        })
                    if len(found) >= MAX_PER_VIOLATION:
                        break
            if found:
                break
        if found:
            out[(v.rule_id, v.day or "", v.shift or "", v.employee_id or "")] = found[:MAX_PER_VIOLATION]
    return out
