"""L3 规则校验层 —— 全系统唯一真相源。

任何排班表，无论来自 LLM、求解器、手工微调还是外部导入，都必须过这一套校验。
本模块刻意不引用求解器，也不知道排班表是怎么来的。

配置化之后这里只剩「编排」：规则语义在 rules.py 的 checker 里，维度与员工池在 config.py 的
索引里。省略 config 的调用一律落到默认配置，这样老前端与旧调用点的行为逐字不变。
"""
from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Set

from . import rules as R
from .config import ConfigIndex, ConfigLike, default_index, index_of
from .models import (
    RuleResult,
    Schedule,
    ScheduleRequest,
    SoftMetrics,
    ValidationReport,
    Violation,
)

# 旧调用点（含测试）按 `V.EMPLOYEES[eid].skills` 取技能，配置化后仍保留这个默认配置视图：
# 默认员工池就是回归基线，不值得为了改造把每个引用点都改成「先拿 index 再查」。
EMPLOYEES = default_index().all_employees

# 兼职周末占比这个软指标在契约里没有对应字段，只能靠角色名约定识别（见交付说明的契约缺口）
PART_TIME_ROLE = "兼职"


def blocked_days(intent: Optional[ScheduleRequest]) -> Dict[str, Set[str]]:
    """把意图中的临时请假折算成 employee_id -> {不可排班的日期}。"""
    out: Dict[str, Set[str]] = {}
    if intent is None:
        return out
    for tl in intent.temp_leaves:
        out.setdefault(tl.employee_id, set()).update(tl.days)
    return out


def validate(
    schedule: Schedule,
    intent: Optional[ScheduleRequest] = None,
    config: ConfigLike = None,
) -> ValidationReport:
    index = index_of(config)
    view = R.build_view(schedule, index, blocked_days(intent))
    violations: List[Violation] = R.run(view)

    by_rule: Dict[str, int] = {}
    for v in violations:
        by_rule[v.rule_id] = by_rule.get(v.rule_id, 0) + 1
    rule_results: List[RuleResult] = [
        RuleResult(
            rule_id=rule.id,
            rule_text=rule.name,
            passed=by_rule.get(rule.id, 0) == 0,
            violation_count=by_rule.get(rule.id, 0),
        )
        for rule in R.rule_ids_in_order(index)
    ]

    return ValidationReport(
        passed=len(violations) == 0,
        violations=violations,
        rule_results=rule_results,
        per_employee_shifts={k: v for k, v in view.counts.items() if v},
        soft_metrics=soft_metrics(schedule, config=index),
    )


def soft_metrics(schedule: Schedule, config: ConfigLike = None) -> SoftMetrics:
    """软约束满足度：不影响 passed，只用于展示排班质量。

    所有分母都来自实际排班与实际维度，不再有「14 格」这类常量：
    固定分母换成三班制后会算出 >100% 的指标（技能冗余度画成 240% 就是这么来的）。
    """
    index = index_of(config)
    grid = schedule.as_map()
    hit = total = 0
    counts: Dict[str, int] = {}
    pt_peak = pt_total = 0

    for day, shift in index.slots:
        for eid in dict.fromkeys(grid.get(f"{day}|{shift}", [])):
            emp = index.employee(eid)
            if emp is None:
                continue
            counts[eid] = counts.get(eid, 0) + 1
            if emp.preferred_shifts:
                total += 1
                if shift in emp.preferred_shifts:
                    hit += 1
            if emp.role == PART_TIME_ROLE:
                pt_total += 1
                if index.is_peak(day):
                    pt_peak += 1

    working = [v for v in counts.values() if v > 0]
    return SoftMetrics(
        preference_hit_rate=round(hit / total, 4) if total else 0.0,
        preference_hit=hit,
        preference_total=total,
        workload_min=min(working) if working else 0,
        workload_max=max(working) if working else 0,
        workload_stdev=round(statistics.pstdev(working), 3) if len(working) > 1 else 0.0,
        part_time_weekend_ratio=round(pt_peak / pt_total, 4) if pt_total else 0.0,
        total_assignments=sum(counts.values()),
    )


def is_available(
    eid: str,
    day: str,
    intent: Optional[ScheduleRequest],
    extra: Dict[str, Set[str]] | None = None,
    config: ConfigLike = None,
    shift: Optional[str] = None,
) -> bool:
    """求解器可复用的可用性判断，语义与 respect_unavailability 完全一致。

    shift 省略 = 只判「这一天能不能上班」，用于按天做容量下界推导；
    传了 shift 才会把按班次配置的不可用时段算进去。
    """
    index: ConfigIndex = index_of(config)
    if not index.can_work(eid, day, shift):
        return False
    blocked = extra if extra is not None else blocked_days(intent)
    return day not in blocked.get(eid, set())
