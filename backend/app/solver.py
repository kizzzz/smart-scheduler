"""L2 确定性约束求解层。

CSP 回溯搜索 + 前向检查 + 启发式局部优化，全程不调用任何模型。
求解器负责剪枝与构造，正确性最终由 validator 独立验收（刻意解耦）。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import validator as V
from .data import (
    DAYS,
    EMPLOYEES,
    EMPLOYEE_IDS,
    MAX_CONSECUTIVE_DAYS,
    MAX_SHIFTS_PER_WEEK,
    SHIFTS,
    SKILL_CASHIER,
    SKILL_DRINK,
    SKILL_KEEPER,
    WEEKEND,
    all_slots,
    has_skill,
    min_required,
    slot_key,
)
from .models import (
    Diagnosis,
    Schedule,
    ScheduleRequest,
    Slot,
    UnlockOption,
)

TIME_BUDGET_S = 4.0
NODE_BUDGET = 60000
CREW_VARIANTS = 8


# ---------- 求解状态 ----------


@dataclass
class State:
    counts: Dict[str, int] = field(default_factory=lambda: {e: 0 for e in EMPLOYEE_IDS})
    day_shifts: Dict[str, Dict[str, Set[str]]] = field(
        default_factory=lambda: {e: {d: set() for d in DAYS} for e in EMPLOYEE_IDS}
    )
    assigned: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, eid: str, day: str, shift: str) -> None:
        self.counts[eid] += 1
        self.day_shifts[eid][day].add(shift)

    def remove(self, eid: str, day: str, shift: str) -> None:
        self.counts[eid] -= 1
        self.day_shifts[eid][day].discard(shift)


@dataclass
class Context:
    intent: ScheduleRequest
    extra_leave: Dict[str, Set[str]]
    pinned: Dict[str, Set[str]]        # slot_key -> 必须在岗的员工
    forbidden: Dict[str, Set[str]]     # slot_key -> 不得在岗的员工
    excluded: Set[str]
    seed_crew: Dict[str, List[str]]    # slot_key -> 基线排班成员（最小扰动用）
    min_override: Dict[str, int]
    rng: random.Random


def build_context(intent: ScheduleRequest, base: Optional[Schedule]) -> Context:
    pinned: Dict[str, Set[str]] = {}
    for p in intent.pins:
        pinned.setdefault(slot_key(p.day, p.shift), set()).add(p.employee_id)
    forbidden: Dict[str, Set[str]] = {}
    for f in intent.forbids:
        shifts = [f.shift] if f.shift else list(SHIFTS)
        for s in shifts:
            forbidden.setdefault(slot_key(f.day, s), set()).add(f.employee_id)
    seed = base.as_map() if base else {}
    return Context(
        intent=intent,
        extra_leave=V.blocked_days(intent),
        pinned=pinned,
        forbidden=forbidden,
        excluded=set(intent.exclude_employees),
        seed_crew=seed,
        min_override=dict(intent.min_staff_override or {}),
        rng=random.Random(20260101),
    )


def need_of(ctx: Context, day: str, shift: str) -> int:
    """人数下限：默认按 R-04，允许意图显式抬高（只允许抬高，不允许降低）。"""
    base = min_required(day)
    for key in (slot_key(day, shift), day, "all"):
        if key in ctx.min_override:
            base = max(base, int(ctx.min_override[key]))
    return base


# ---------- 单点可行性 ----------


def can_add(ctx: Context, st: State, eid: str, day: str, shift: str) -> bool:
    if eid in ctx.excluded:
        return False
    if eid in ctx.forbidden.get(slot_key(day, shift), set()):
        return False
    if not V.is_available(eid, day, None, ctx.extra_leave):       # R-08
        return False
    if st.counts[eid] >= MAX_SHIFTS_PER_WEEK:                     # R-05
        return False
    if st.day_shifts[eid][day]:                                   # 同日早晚班时间重叠，物理不可行
        return False
    if shift == "早班" and _prev_evening(st, eid, day):           # R-07
        return False
    if shift == "晚班" and _next_morning(st, eid, day):           # R-07（基线残留场景）
        return False
    if _consecutive_with(st, eid, day) > MAX_CONSECUTIVE_DAYS:     # R-06
        return False
    return True


def _prev_evening(st: State, eid: str, day: str) -> bool:
    i = DAYS.index(day)
    return i > 0 and "晚班" in st.day_shifts[eid][DAYS[i - 1]]


def _next_morning(st: State, eid: str, day: str) -> bool:
    i = DAYS.index(day)
    return i < len(DAYS) - 1 and "早班" in st.day_shifts[eid][DAYS[i + 1]]


def _consecutive_with(st: State, eid: str, day: str) -> int:
    """假设 eid 在 day 上班，计算其所在连续工作段长度。"""
    i = DAYS.index(day)
    run = 1
    j = i - 1
    while j >= 0 and st.day_shifts[eid][DAYS[j]]:
        run += 1
        j -= 1
    j = i + 1
    while j < len(DAYS) and st.day_shifts[eid][DAYS[j]]:
        run += 1
        j += 1
    return run


def eligible(ctx: Context, st: State, day: str, shift: str) -> List[str]:
    return [e for e in EMPLOYEE_IDS if can_add(ctx, st, e, day, shift)]


# ---------- 班次成员构造 ----------


def _score(ctx: Context, st: State, eid: str, day: str, shift: str) -> float:
    """越小越优先。核心思想：不要把稀缺的店长值守资格当普通人力烧掉。"""
    e = EMPLOYEES[eid]
    s = 0.0
    s += st.counts[eid] * 3.0                                  # 均衡工时
    if has_skill(eid, SKILL_KEEPER):
        s += 6.0                                               # 保护值守资源
    if e.preference and e.preference != shift:
        s += 2.0
    if e.preference == shift:
        s -= 1.5
    if e.is_part_time():
        s += 0.0 if day in WEEKEND else 4.0                    # 兼职优先用在周末
    s += len(e.available_days) * 0.15                           # 可用天数少的人留给窄班次
    if eid in ctx.seed_crew.get(slot_key(day, shift), []):
        s -= 12.0                                              # 最小扰动：优先保留基线成员
    return s


def build_crew(ctx: Context, st: State, day: str, shift: str, variant: int) -> Optional[List[str]]:
    """构造一个满足 R-01/R-02/R-03/R-04 的班次成员集合；不可行返回 None。"""
    need = need_of(ctx, day, shift)
    key = slot_key(day, shift)
    crew: List[str] = []

    # 锁定成员必须先落位
    for eid in sorted(ctx.pinned.get(key, set())):
        if not can_add(ctx, st, eid, day, shift):
            return None
        crew.append(eid)
        st.add(eid, day, shift)

    def rollback() -> None:
        for x in crew:
            st.remove(x, day, shift)

    def take(eid: str) -> None:
        crew.append(eid)
        st.add(eid, day, shift)

    pool = [e for e in eligible(ctx, st, day, shift) if e not in crew]
    pool.sort(key=lambda e: (_score(ctx, st, e, day, shift), e))

    # 1) R-01 值守位优先，variant 用于换人回溯
    if not any(has_skill(x, SKILL_KEEPER) for x in crew):
        keepers = [e for e in pool if has_skill(e, SKILL_KEEPER)]
        if not keepers:
            rollback()
            return None
        take(keepers[variant % len(keepers)])
        pool = [e for e in pool if e not in crew]

    # 2) R-02 饮品制作 ≥2
    def fill(skill: str, target: int, offset: int) -> bool:
        nonlocal pool
        have = sum(1 for x in crew if has_skill(x, skill))
        while have < target:
            cands = [e for e in pool if has_skill(e, skill)]
            if not cands:
                return False
            take(cands[offset % len(cands)] if len(cands) > 1 else cands[0])
            pool = [e for e in pool if e not in crew]
            have += 1
        return True

    if not fill(SKILL_DRINK, 2, variant // 2):
        rollback()
        return None
    # 3) R-03 收银 ≥1
    if not fill(SKILL_CASHIER, 1, variant // 3):
        rollback()
        return None

    # 4) R-04 补齐人数
    while len(crew) < need:
        if not pool:
            rollback()
            return None
        take(pool[0])
        pool = pool[1:]

    return crew


# ---------- 回溯搜索 ----------


def _forward_check(ctx: Context, st: State, remaining: Sequence[Tuple[str, str]]) -> bool:
    """对每个未排班次做单班可行性下界检查，尽早剪枝。"""
    for day, shift in remaining:
        pool = eligible(ctx, st, day, shift)
        if len(pool) < need_of(ctx, day, shift):
            return False
        if not any(has_skill(e, SKILL_KEEPER) for e in pool):
            return False
        if sum(1 for e in pool if has_skill(e, SKILL_DRINK)) < 2:
            return False
        if not any(has_skill(e, SKILL_CASHIER) for e in pool):
            return False
    return True


def search(ctx: Context) -> Tuple[Optional[Schedule], List[str], bool]:
    """返回 (排班表, 决策痕迹, 是否搜索超时)。"""
    slots = all_slots()
    st = State()
    trace: List[str] = []
    deadline = time.monotonic() + TIME_BUDGET_S
    nodes = [0]
    timeout = [False]

    def dfs(i: int) -> bool:
        if i == len(slots):
            return True
        if time.monotonic() > deadline or nodes[0] > NODE_BUDGET:
            timeout[0] = True
            return False
        day, shift = slots[i]
        nodes[0] += 1
        for variant in range(CREW_VARIANTS):
            crew = build_crew(ctx, st, day, shift, variant)
            if crew is None:
                continue
            st.assigned[slot_key(day, shift)] = crew
            if _forward_check(ctx, st, slots[i + 1:]) and dfs(i + 1):
                return True
            for eid in crew:
                st.remove(eid, day, shift)
            st.assigned.pop(slot_key(day, shift), None)
            if variant == 0:
                trace.append(f"周{day}{shift}：首选方案在后续班次不可行，换人重试")
            if timeout[0]:
                return False
        return False

    ok = dfs(0)
    if not ok:
        return None, trace, timeout[0]

    schedule = Schedule(
        slots=[Slot(day=d, shift=s, employee_ids=list(st.assigned[slot_key(d, s)])) for d, s in slots]
    )
    trace.insert(0, f"回溯搜索完成，展开 {nodes[0]} 个节点")
    return schedule, trace, False


# ---------- 局部优化（只在硬约束全通过的前提下） ----------


def _soft_score(sch: Schedule, ctx: Optional[Context] = None) -> float:
    m = V.soft_metrics(sch)
    score = m.preference_hit_rate * 10 - m.workload_stdev * 2 - (1 - m.part_time_weekend_ratio)
    if ctx and ctx.seed_crew:
        # 最小扰动：相对基线每改动一个班次都要付代价，避免为了软指标大面积翻盘
        grid = sch.as_map()
        changed = sum(1 for k, v in grid.items() if set(ctx.seed_crew.get(k, [])) != set(v))
        score -= changed * 1.2
    return score


def optimize(ctx: Context, sch: Schedule, rounds: int = 400) -> Tuple[Schedule, List[str]]:
    """随机换人/换班，只接受「硬约束仍然 0 违规且软指标变好」的改动。"""
    best = sch.model_copy(deep=True)
    best_score = _soft_score(best, ctx)
    trace: List[str] = []
    accepted = 0
    slots = all_slots()
    for _ in range(rounds):
        cand = best.model_copy(deep=True)
        grid = {s.day + "|" + s.shift: s for s in cand.slots}
        day, shift = ctx.rng.choice(slots)
        target = grid[slot_key(day, shift)]
        if not target.employee_ids:
            continue
        out = ctx.rng.choice(target.employee_ids)
        if out in ctx.pinned.get(slot_key(day, shift), set()):
            continue
        pool = [
            e for e in EMPLOYEE_IDS
            if e not in target.employee_ids
            and e not in ctx.excluded
            and e not in ctx.forbidden.get(slot_key(day, shift), set())
            and V.is_available(e, day, None, ctx.extra_leave)
        ]
        if not pool:
            continue
        inn = ctx.rng.choice(pool)
        target.employee_ids = [inn if x == out else x for x in target.employee_ids]
        rep = V.validate(cand, ctx.intent)
        if not rep.passed:
            continue
        score = _soft_score(cand, ctx)
        if score > best_score + 1e-9:
            best, best_score = cand, score
            accepted += 1
    if accepted:
        trace.append(f"局部优化接受 {accepted} 次改动，软指标提升至 {round(best_score, 3)}")
    return best, trace


# ---------- 无解诊断 ----------


def diagnose(ctx: Context, timeout: bool) -> Diagnosis:
    st = State()
    evidence: List[str] = []
    bottleneck_slots: List[str] = []
    bottleneck_rule: Optional[str] = None

    for day, shift in all_slots():
        pool = eligible(ctx, st, day, shift)
        need = need_of(ctx, day, shift)
        keepers = [e for e in pool if has_skill(e, SKILL_KEEPER)]
        drinks = [e for e in pool if has_skill(e, SKILL_DRINK)]
        cashiers = [e for e in pool if has_skill(e, SKILL_CASHIER)]
        label = f"周{day}{shift}"
        if not keepers:
            bottleneck_rule = bottleneck_rule or "R-01"
            bottleneck_slots.append(label)
            evidence.append(f"{label}：可排班人员中 0 名具备店长值守资格，R-01 不可能满足")
        elif len(drinks) < 2:
            bottleneck_rule = bottleneck_rule or "R-02"
            bottleneck_slots.append(label)
            evidence.append(f"{label}：可排班人员中仅 {len(drinks)} 名具备饮品制作技能，R-02 需 ≥2")
        elif not cashiers:
            bottleneck_rule = bottleneck_rule or "R-03"
            bottleneck_slots.append(label)
            evidence.append(f"{label}：可排班人员中 0 名具备收银技能，R-03 不可能满足")
        elif len(pool) < need:
            bottleneck_rule = bottleneck_rule or "R-04"
            bottleneck_slots.append(label)
            evidence.append(f"{label}：可排班人员 {len(pool)} 名，低于人数下限 {need} 名")

    # 单日下界：同一员工不能同时上当天早晚班（两班时间重叠），
    # 因此每天需要 2 名不同的值守资格员工、4 名不同的饮品制作员工、2 名不同的收银员工
    for day in DAYS:
        pool = [e for e in EMPLOYEE_IDS if e not in ctx.excluded and V.is_available(e, day, None, ctx.extra_leave)]
        day_need = need_of(ctx, day, "早班") + need_of(ctx, day, "晚班")
        checks = [
            ("R-01", SKILL_KEEPER, 2, "具备店长值守资格"),
            ("R-02", SKILL_DRINK, 4, "具备饮品制作技能"),
            ("R-03", SKILL_CASHIER, 2, "具备收银技能"),
        ]
        for rid, skill, low, desc in checks:
            n = sum(1 for e in pool if has_skill(e, skill))
            if n < low:
                bottleneck_rule = bottleneck_rule or rid
                bottleneck_slots.append(f"周{day}")
                evidence.append(
                    f"周{day}：全天仅 {n} 名{desc}的员工可排班，但早晚两班共需 {low} 名不同员工（同一人不能连上当天两班）"
                )
                break
        else:
            if len(pool) < day_need:
                bottleneck_rule = bottleneck_rule or "R-04"
                bottleneck_slots.append(f"周{day}")
                evidence.append(f"周{day}：全天可排班 {len(pool)} 人，早晚两班共需 {day_need} 人次")

    # 全周容量下界：供给 vs 需求
    supply = sum(
        min(MAX_SHIFTS_PER_WEEK, sum(1 for d in DAYS if V.is_available(e, d, None, ctx.extra_leave)))
        for e in EMPLOYEE_IDS if e not in ctx.excluded
    )
    demand = sum(need_of(ctx, d, s) for d, s in all_slots())
    if supply < demand:
        bottleneck_rule = bottleneck_rule or "R-04"
        evidence.append(f"全周供给上限 {supply} 人班 < 全周需求 {demand} 人班，总量不足")

    proven = bool(evidence)
    kind = "proven_infeasible" if proven else ("search_timeout" if timeout else "proven_infeasible")
    if not proven and not timeout:
        evidence.append("已穷尽当前约束下的候选组合，未找到满足 9 条硬规则的排班方案")

    return Diagnosis(
        infeasible=True,
        kind=kind,  # type: ignore[arg-type]
        bottleneck_rule=bottleneck_rule,
        bottleneck_slots=bottleneck_slots[:6],
        evidence=evidence[:8],
        unlock_options=_unlock_options(ctx, bottleneck_rule, bottleneck_slots),
    )


def _unlock_options(ctx: Context, rule: Optional[str], slots: List[str]) -> List[UnlockOption]:
    where = "、".join(slots[:2]) if slots else "瓶颈班次"
    opts: List[UnlockOption] = []
    if rule == "R-01":
        opts.append(UnlockOption(
            title="临时授予店长值守资格",
            detail=f"为 {where} 指定 1 名高级店员临时获得值守资格（建议 E06/E07/E08 中资历最高者）",
            cost="需店长线下确认，属人事授权变更",
        ))
        opts.append(UnlockOption(
            title="撤销与值守资格员工冲突的临时请假",
            detail="请 E01–E05 中一人在瓶颈日改期请假，即可恢复可行",
            cost="影响 1 名员工的休假安排",
        ))
    elif rule == "R-02":
        opts.append(UnlockOption(
            title="调整饮品制作岗配置",
            detail=f"为 {where} 补充 1 名具备饮品制作技能的员工，或临时完成一次技能认证",
            cost="需培训或跨店调人",
        ))
    elif rule == "R-03":
        opts.append(UnlockOption(
            title="补充收银人力",
            detail=f"为 {where} 安排 1 名具备收银技能的员工顶班",
            cost="需跨店调人",
        ))
    else:
        opts.append(UnlockOption(
            title="下调瓶颈班次人数下限",
            detail=f"将 {where} 的人数下限临时下调 1 人，需运营负责人审批",
            cost="服务承诺降级，需书面审批",
        ))
        opts.append(UnlockOption(
            title="放宽单周班次上限",
            detail="允许 1–2 名全职员工本周排 6 个班（涉及加班审批与工时合规）",
            cost="产生加班成本，需合规确认",
        ))
    opts.append(UnlockOption(
        title="扩充可用人力池",
        detail="临时借调 1 名邻店员工（需具备店长值守或饮品制作技能）",
        cost="跨店协调，1–2 天准备期",
    ))
    return opts[:3]


# ---------- 对外入口 ----------


def solve(intent: ScheduleRequest, base: Optional[Schedule] = None) -> Tuple[Optional[Schedule], List[str], Diagnosis]:
    ctx = build_context(intent, base)
    sch, trace, timeout = search(ctx)
    if sch is None:
        return None, trace, diagnose(ctx, timeout)
    sch, opt_trace = optimize(ctx, sch)
    return sch, trace + opt_trace, Diagnosis()


def changed_slots(base: Optional[Schedule], new: Optional[Schedule]) -> List[str]:
    if base is None or new is None:
        return []
    a, b = base.as_map(), new.as_map()
    out = []
    for k in b:
        if set(a.get(k, [])) != set(b[k]):
            out.append(k)
    return out
