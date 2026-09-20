"""配置化测试的构造器。

放在这里而不是每个测试文件里各写一份，是因为「非默认维度」的用例只要维度一变就要重写
一整份配置；构造器让用例只声明它真正关心的那一两个字段（几天几班、谁有什么技能），
其余保持最小可解，读用例的人一眼能看出被测的是什么。
"""
from __future__ import annotations

import os
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import (  # noqa: E402
    DayDef,
    EmployeeDef,
    RuleDef,
    ScenarioDef,
    SchedulerConfig,
    ShiftDef,
    UnavailableSlot,
)
from app.models import Schedule, Slot  # noqa: E402

# 两班不重叠，用于「一天多班」类用例：默认 09-17/13-21 是重叠的，
# 在它上面测 one_shift_per_day 只会撞到物理不变式，测不到规则本身
DAY_SHIFT = ("早", "09:00", "13:00")
MID_SHIFT = ("中", "13:00", "17:00")
NIGHT_SHIFT = ("晚", "17:00", "21:00")


def day(did: str, label: str = "", peak: bool = False) -> DayDef:
    return DayDef(id=did, label=label or did, peak=peak)


def days(n: int, peak: Sequence[str] = ()) -> List[DayDef]:
    return [day(f"d{i}", f"第{i}天", peak=f"d{i}" in peak) for i in range(1, n + 1)]


def shift(sid: str, start: str, end: str, name: str = "") -> ShiftDef:
    return ShiftDef(id=sid, name=name or sid, start=start, end=end)


def shifts(*specs: Tuple[str, str, str]) -> List[ShiftDef]:
    return [shift(sid, start, end) for sid, start, end in specs]


def emp(
    eid: str,
    skills: Iterable[str] = (),
    role: str = "店员",
    unavailable: Iterable[UnavailableSlot] = (),
    max_shifts: Optional[int] = None,
    preferred: Iterable[str] = (),
    active: bool = True,
    name: str = "",
) -> EmployeeDef:
    return EmployeeDef(
        id=eid,
        name=name or eid,
        role=role,
        skills=list(skills),
        unavailable=list(unavailable),
        max_shifts=max_shifts,
        preferred_shifts=list(preferred),
        active=active,
    )


def emps(n: int, skills: Iterable[str] = (), start: int = 1, **kw) -> List[EmployeeDef]:
    return [emp(f"E{i:02d}", skills=skills, **kw) for i in range(start, start + n)]


def rule(rid: str, rtype: str, enabled: bool = True, name: str = "", **params) -> RuleDef:
    return RuleDef(id=rid, type=rtype, name=name or rid, enabled=enabled, params=params)


def config(
    day_defs: Optional[List[DayDef]] = None,
    shift_defs: Optional[List[ShiftDef]] = None,
    employees: Optional[List[EmployeeDef]] = None,
    rules: Optional[List[RuleDef]] = None,
    skill_pool: Sequence[str] = (),
    name: str = "测试门店",
) -> SchedulerConfig:
    """最小可用配置。不传 rules 时只有系统内建的两条 locked 规则。"""
    return SchedulerConfig(
        version=1,
        scenario=ScenarioDef(
            name=name,
            days=day_defs if day_defs is not None else days(2),
            shifts=shift_defs if shift_defs is not None else shifts(DAY_SHIFT, NIGHT_SHIFT),
        ),
        skill_pool=list(skill_pool),
        employees=employees if employees is not None else emps(4),
        rules=list(rules or []),
    )


def sched(grid: Dict[Tuple[str, str], Sequence[str]]) -> Schedule:
    return Schedule(slots=[Slot(day=d, shift=s, employee_ids=list(v)) for (d, s), v in grid.items()])


def full(cfg: SchedulerConfig, members: Sequence[str]) -> Schedule:
    """每格都排同一批人。用于「只有一处违规」的用例：其余格子必须干净，
    否则断言「这条规则报了」会被别的规则的噪声掩盖。"""
    return Schedule(slots=[
        Slot(day=d.id, shift=s.id, employee_ids=list(members))
        for d in cfg.scenario.days
        for s in cfg.scenario.shifts
    ])


def ids_of(report, rule_id: str) -> List[str]:
    return [v.detail for v in report.violations if v.rule_id == rule_id]


def failed_rules(report) -> List[str]:
    return [r.rule_id for r in report.rule_results if not r.passed]
