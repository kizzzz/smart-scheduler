"""一键修复建议：把违规翻译成「换谁上谁」的具体动作。

约束：建议只能落在同一个班次内（remove / add），且必须先自证「换完之后这条违规确实消失、
且不引入新的违规」。做不到就不给建议 —— 宁可不建议，也不给店长挖坑。

配置化后建议的语义不变，但「要补什么」不再按 R-01/02/03 编号查表：编号是用户可改的显示值，
自建规则会是 C-1，按编号查表在改过编号后会静默失效（不报错、只是不再给建议）。
现在按规则**类型与参数**判断，规则怎么编号都不影响。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import solver
from . import validator as V
from .config import RULE_REQUIRE_ATTRIBUTE, ConfigLike, RuleDef, index_of, slot_key
from .models import Schedule, ScheduleRequest, ValidationReport, Violation

MAX_PER_VIOLATION = 2


def _state_of(schedule: Schedule, ctx: solver.Context) -> solver.State:
    """把现有排班折成求解器状态，让候选名单知道每个人已经排了多少班。

    只计入配置里存在的员工：档案外的工号（R-09 违规）没有任何约束参数可用，
    放进状态只会让上限计算失真。
    """
    st = solver.State()
    for s in schedule.slots:
        for eid in dict.fromkeys(s.employee_ids):
            if ctx.index.known(eid):
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
    config: ConfigLike,
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

    before = V.validate(schedule, intent, config=config)
    after = V.validate(trial, intent, config=config)
    if len(after.violations) >= len(before.violations):
        return False
    still = any(
        v.rule_id == target.rule_id and v.day == target.day and v.shift == target.shift
        and v.employee_id == target.employee_id
        for v in after.violations
    )
    return not still


def _candidates(ctx: solver.Context, st: solver.State, day: str, shift: str, exclude: set) -> List[str]:
    pool = [
        e for e in ctx.index.employee_ids
        if e not in exclude and solver.can_add(ctx, st, e, day, shift)
    ]
    pool.sort(key=lambda e: (solver._score(ctx, st, e, day, shift), e))
    return pool


def _attr_rule_of(ctx: solver.Context, rule_id: str) -> Optional[RuleDef]:
    """违规归属的规则如果是资质类，取出它的 (attr, value) 用于筛选候选人。"""
    rule = next((r for r in ctx.index.rules if r.id == rule_id), None)
    return rule if rule is not None and rule.type == RULE_REQUIRE_ATTRIBUTE else None


def _has(ctx: solver.Context, eid: str, rule: Optional[RuleDef]) -> bool:
    if rule is None:
        return True
    params = rule.params or {}
    return ctx.index.has_attribute(eid, str(params.get("attr") or "skill"), str(params.get("value") or ""))


def suggest(
    schedule: Schedule,
    report: ValidationReport,
    intent: Optional[ScheduleRequest] = None,
    config: ConfigLike = None,
) -> Dict[Tuple[str, str, str, str], List[dict]]:
    """返回 {(rule_id, day, shift, employee_id): [suggestion, ...]}。"""
    if report.passed:
        return {}
    index = index_of(config)
    ctx = solver.build_context(intent or ScheduleRequest(), None, config=index)
    st = _state_of(schedule, ctx)
    out: Dict[Tuple[str, str, str, str], List[dict]] = {}

    for v in report.violations:
        if not v.day:
            continue
        # 违规没带 shift（连班、单日多班这类按天成立的规则）时，逐个班次找可修点
        shifts = [v.shift] if v.shift else list(index.shift_ids)
        found: List[dict] = []
        for shift in shifts:
            slot = _slot_of(schedule, v.day, shift)
            if slot is None:
                continue
            members = list(slot.employee_ids)
            if v.employee_id and v.employee_id not in members:
                continue

            # 场景 A：某人不该在这个班（班次上限/连班/休息间隔/不可用/档案外）→ 换人
            if v.employee_id:
                pool = _candidates(ctx, st, v.day, shift, set(members) | {v.employee_id})
                for cand in pool[:6]:
                    if _try(schedule, intent, v.day, shift, v.employee_id, cand, v, index):
                        found.append({
                            "label": f"把 {v.employee_id} 换成 {cand}",
                            "day": v.day, "shift": shift, "remove": v.employee_id, "add": cand,
                        })
                        if len(found) >= MAX_PER_VIOLATION:
                            break
                if not found and _try(schedule, intent, v.day, shift, v.employee_id, None, v, index):
                    found.append({
                        "label": f"直接移除 {v.employee_id}",
                        "day": v.day, "shift": shift, "remove": v.employee_id, "add": None,
                    })

            # 场景 B：本班缺资质或缺人 → 补人或换人
            else:
                rule = _attr_rule_of(ctx, v.rule_id)
                pool = [e for e in _candidates(ctx, st, v.day, shift, set(members)) if _has(ctx, e, rule)]
                need = index.min_required(v.day, shift)
                for cand in pool[:6]:
                    # 人数已达下限时优先「换」，否则直接「补」
                    if len(members) > need and rule is not None:
                        droppable = [
                            m for m in members
                            if not _has(ctx, m, rule) and m not in ctx.pinned.get(slot_key(v.day, shift), set())
                        ]
                        for drop in droppable[:4]:
                            if _try(schedule, intent, v.day, shift, drop, cand, v, index):
                                found.append({
                                    "label": f"把 {drop} 换成 {cand}",
                                    "day": v.day, "shift": shift, "remove": drop, "add": cand,
                                })
                                break
                    if len(found) < MAX_PER_VIOLATION and _try(
                        schedule, intent, v.day, shift, None, cand, v, index
                    ):
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
