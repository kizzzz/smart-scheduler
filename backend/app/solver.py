"""L2 确定性约束求解层。

CSP 回溯搜索 + 前向检查 + 启发式局部优化，全程不调用任何模型。
求解器负责剪枝与构造，正确性最终由 validator 独立验收（刻意解耦）。

配置化后要额外守住两件事：

1. **维度可变。** 1..14 天 × 1..4 班，最多 56 格；「每人每天一个班」从隐式假设变成
   可关的规则，关掉后一天多班只受时间重叠与最小休息间隔约束。
2. **搜索必须有显式上限。** 56 格 × 大员工池的组合空间是指数的，没有预算的回溯会把
   一次请求挂死在容器里。超预算返回「无解 + 未证明」，并把预算写进证据，
   让店长知道该缩小场景而不是继续等。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import validator as V
from .config import (
    RULE_MIN_REST,
    RULE_REQUIRE_ATTRIBUTE,
    ConfigIndex,
    ConfigLike,
    RuleDef,
    attribute_noun,
    index_of,
    slot_key,
    valid_limit,
)
from .models import (
    Diagnosis,
    Schedule,
    ScheduleRequest,
    Slot,
    UnlockOption,
)

# 默认维度（14 格）下的历史预算，改动会直接影响「单人请假会不会被误判无解」
TIME_BUDGET_S = 4.0
NODE_BUDGET = 60000
CREW_VARIANTS = 8
# 维度变大时按格数线性放宽，但要有硬顶：用户宁可拿到「场景太大」也不愿等一分钟
TIME_BUDGET_CAP_S = 12.0
NODE_BUDGET_CAP = 240000
BASE_SLOTS = 14


@dataclass(frozen=True)
class SearchBudget:
    """回溯搜索的显式上限。

    做成入参而不是全局常量，是为了让测试能用一个极小的预算逼出「超预算」分支——
    否则这条最关键的保护路径只能靠等 12 秒来验证，没人会跑那样的测试。
    """
    time_s: float = TIME_BUDGET_S
    nodes: int = NODE_BUDGET

    @classmethod
    def for_index(cls, index: ConfigIndex) -> "SearchBudget":
        scale = max(1.0, index.slot_count() / BASE_SLOTS)
        return cls(
            time_s=min(TIME_BUDGET_CAP_S, TIME_BUDGET_S * scale),
            nodes=min(NODE_BUDGET_CAP, int(NODE_BUDGET * scale)),
        )


@dataclass
class SearchStats:
    nodes: int = 0
    elapsed_s: float = 0.0
    budget_exceeded: bool = False


# ---------- 求解状态 ----------


@dataclass
class State:
    """已落位的分配。

    counts / day_shifts 都是懒建的：员工池由配置决定，预先按全量员工建表意味着
    每次 State() 都要知道当前配置，而 repair 之类的调用点只想要一个空状态。
    """
    counts: Dict[str, int] = field(default_factory=dict)
    day_shifts: Dict[str, Dict[str, Set[str]]] = field(default_factory=dict)
    assigned: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, eid: str, day: str, shift: str) -> None:
        self.counts[eid] = self.counts.get(eid, 0) + 1
        self.day_shifts.setdefault(eid, {}).setdefault(day, set()).add(shift)

    def remove(self, eid: str, day: str, shift: str) -> None:
        self.counts[eid] = self.counts.get(eid, 0) - 1
        self.day_shifts.get(eid, {}).get(day, set()).discard(shift)

    def shifts_on(self, eid: str, day: str) -> Set[str]:
        return self.day_shifts.get(eid, {}).get(day, _EMPTY)

    def days_worked(self, eid: str) -> Dict[str, Set[str]]:
        return self.day_shifts.get(eid, {})


_EMPTY: Set[str] = frozenset()  # type: ignore[assignment]


@dataclass
class Context:
    index: ConfigIndex
    intent: ScheduleRequest
    extra_leave: Dict[str, Set[str]]
    pinned: Dict[str, Set[str]]        # slot_key -> 必须在岗的员工
    forbidden: Dict[str, Set[str]]     # slot_key -> 不得在岗的员工
    excluded: Set[str]
    seed_crew: Dict[str, List[str]]    # slot_key -> 基线排班成员（最小扰动用）
    min_override: Dict[str, int]
    rng: random.Random
    budget: SearchBudget


def build_context(
    intent: ScheduleRequest,
    base: Optional[Schedule],
    config: ConfigLike = None,
    budget: Optional[SearchBudget] = None,
) -> Context:
    index = index_of(config)
    pinned: Dict[str, Set[str]] = {}
    for p in intent.pins:
        pinned.setdefault(slot_key(p.day, p.shift), set()).add(p.employee_id)
    forbidden: Dict[str, Set[str]] = {}
    for f in intent.forbids:
        shifts = [f.shift] if f.shift else list(index.shift_ids)
        for s in shifts:
            forbidden.setdefault(slot_key(f.day, s), set()).add(f.employee_id)
    seed = base.as_map() if base else {}
    return Context(
        index=index,
        intent=intent,
        extra_leave=V.blocked_days(intent),
        pinned=pinned,
        forbidden=forbidden,
        excluded=set(intent.exclude_employees),
        seed_crew=seed,
        min_override=dict(intent.min_staff_override or {}),
        rng=random.Random(20260101),
        budget=budget or SearchBudget.for_index(index),
    )


def need_of(ctx: Context, day: str, shift: str) -> int:
    """人数下限：默认按 min_staff_per_shift 规则，允许意图显式抬高（只允许抬高，不允许降低）。"""
    base = ctx.index.min_required(day, shift)
    for key in (slot_key(day, shift), day, "all"):
        if key in ctx.min_override:
            base = max(base, int(ctx.min_override[key]))
    return base


# ---------- 单点可行性 ----------


def can_add(ctx: Context, st: State, eid: str, day: str, shift: str) -> bool:
    idx = ctx.index
    if eid in ctx.excluded:
        return False
    if eid in ctx.forbidden.get(slot_key(day, shift), set()):
        return False
    if not idx.can_work(eid, day, shift):                          # respect_unavailability
        return False
    if day in ctx.extra_leave.get(eid, set()):                     # 意图里的临时请假
        return False
    cap = idx.max_shifts(eid)                                      # max_shifts_per_period + 个人上限
    if cap is not None and st.counts.get(eid, 0) >= cap:
        return False
    if st.shifts_on(eid, day) and idx.one_shift_per_day:           # one_shift_per_day
        return False
    if not _rest_ok(ctx, st, eid, day, shift):                     # 时间重叠 + min_rest_hours
        return False
    if idx.max_consecutive_days is not None and _consecutive_with(ctx, st, eid, day) > idx.max_consecutive_days:
        return False
    return True


def _rest_ok(ctx: Context, st: State, eid: str, day: str, shift: str) -> bool:
    """时间重叠一律不可行；最小休息间隔按 ShiftDef.start/end 实算。

    重叠的判定与规则无关（同一人不可能同时在两处），所以即使没有 min_rest_hours 规则也要拦。
    只看目标日 ±N 天：休息间隔不可能跨越比它本身更长的时间窗，扫全周期纯属浪费。
    """
    idx = ctx.index
    rest = idx.min_rest_hours
    window = 1 + int((rest or 0) // 24)
    pos = idx.day_pos.get(day, 0)
    target = (day, shift)
    for other_day, shifts in st.days_worked(eid).items():
        if abs(idx.day_pos.get(other_day, 0) - pos) > window:
            continue
        for other in shifts:
            gap = idx.rest_gap_hours((other_day, other), target)
            if gap < 0:
                return False
            if rest is not None and gap < rest:
                return False
    return True


def _consecutive_with(ctx: Context, st: State, eid: str, day: str) -> int:
    """假设 eid 在 day 上班，计算其所在连续工作段长度。"""
    days = ctx.index.day_ids
    i = ctx.index.day_pos.get(day, 0)
    run = 1
    j = i - 1
    while j >= 0 and st.shifts_on(eid, days[j]):
        run += 1
        j -= 1
    j = i + 1
    while j < len(days) and st.shifts_on(eid, days[j]):
        run += 1
        j += 1
    return run


def eligible(ctx: Context, st: State, day: str, shift: str) -> List[str]:
    return [e for e in ctx.index.employee_ids if can_add(ctx, st, e, day, shift)]


# ---------- 班次成员构造 ----------


def _score(ctx: Context, st: State, eid: str, day: str, shift: str) -> float:
    """越小越优先。核心思想：不要把最稀缺的资质当普通人力烧掉。"""
    idx = ctx.index
    e = idx.employee(eid)
    s = 0.0
    s += st.counts.get(eid, 0) * 3.0                            # 均衡工时
    if e is None:
        return s
    scarce = idx.scarce_attribute()
    if scarce and idx.has_attribute(eid, scarce[0], scarce[1]):
        s += 6.0                                                # 保护最紧的那项资质
    if e.preferred_shifts and shift not in e.preferred_shifts:
        s += 2.0
    if shift in e.preferred_shifts:
        s -= 1.5
    if e.role == V.PART_TIME_ROLE:
        s += 0.0 if idx.is_peak(day) else 4.0                   # 兼职优先用在高峰日
    s += len(idx.roster_days(eid)) * 0.15                       # 岗位灵活度低的人留给窄班次
    if eid in ctx.seed_crew.get(slot_key(day, shift), []):
        s -= 12.0                                               # 最小扰动：优先保留基线成员
    return s


def _attr_of(rule: RuleDef) -> Tuple[str, str, int]:
    """解析 require_attribute 的 (attr, value, min)。

    min 走 valid_limit：非法值（0、负数、"两个"）统一折成 0 = 这条规则不生效，与
    rules.check_require_attribute 同口径。int() 直接解析会在脏参数上抛异常，让一份
    该被 config_check 拒绝的配置变成 500。
    """
    params = rule.params or {}
    return (
        str(params.get("attr") or "skill"),
        str(params.get("value") or ""),
        valid_limit(params.get("min")) or 0,
    )


def build_crew(ctx: Context, st: State, day: str, shift: str, variant: int) -> Optional[List[str]]:
    """构造一个满足全部 require_attribute 与人数下限的班次成员集合；不可行返回 None。"""
    idx = ctx.index
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

    def fill(attr: str, value: str, target: int, offset: int) -> bool:
        nonlocal pool
        have = sum(1 for x in crew if idx.has_attribute(x, attr, value))
        while have < target:
            cands = [e for e in pool if idx.has_attribute(e, attr, value)]
            if not cands:
                return False
            take(cands[offset % len(cands)])
            pool = [e for e in pool if e not in crew]
            have += 1
        return True

    # 资质位按配置顺序补齐；variant 用于换人回溯，除数错开是为了让不同资质位的换人不同步，
    # 否则 8 个 variant 里有一半是同一个组合
    for k, rule in enumerate(idx.require_attribute_rules()):
        attr, value, low = _attr_of(rule)
        if not value or low <= 0:
            continue
        if not fill(attr, value, low, variant if k == 0 else variant // (k + 1)):
            rollback()
            return None

    # 补齐人数下限
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
    idx = ctx.index
    attr_rules = [(_attr_of(r)) for r in idx.require_attribute_rules()]
    for day, shift in remaining:
        pool = eligible(ctx, st, day, shift)
        if len(pool) < need_of(ctx, day, shift):
            return False
        for attr, value, low in attr_rules:
            if not value or low <= 0:
                continue
            if sum(1 for e in pool if idx.has_attribute(e, attr, value)) < low:
                return False
    return True


def search(ctx: Context) -> Tuple[Optional[Schedule], List[str], SearchStats]:
    """返回 (排班表, 决策痕迹, 搜索统计)。"""
    idx = ctx.index
    slots = idx.slots
    st = State()
    trace: List[str] = []
    started = time.monotonic()
    deadline = started + ctx.budget.time_s
    stats = SearchStats()

    def dfs(i: int) -> bool:
        if i == len(slots):
            return True
        if time.monotonic() > deadline or stats.nodes > ctx.budget.nodes:
            stats.budget_exceeded = True
            return False
        day, shift = slots[i]
        stats.nodes += 1
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
                trace.append(f"{idx.slot_label(day, shift)}：首选方案在后续班次不可行，换人重试")
            if stats.budget_exceeded:
                return False
        return False

    ok = dfs(0)
    stats.elapsed_s = time.monotonic() - started
    if not ok:
        return None, trace, stats

    schedule = Schedule(
        slots=[Slot(day=d, shift=s, employee_ids=list(st.assigned[slot_key(d, s)])) for d, s in slots]
    )
    trace.insert(0, f"回溯搜索完成，展开 {stats.nodes} 个节点")
    return schedule, trace, stats


# ---------- 局部优化（只在硬约束全通过的前提下） ----------


def _soft_score(sch: Schedule, ctx: Optional[Context] = None) -> float:
    m = V.soft_metrics(sch, config=ctx.index if ctx else None)
    score = m.preference_hit_rate * 10 - m.workload_stdev * 2 - (1 - m.part_time_weekend_ratio)
    if ctx and ctx.seed_crew:
        # 最小扰动：相对基线每改动一个班次都要付代价，避免为了软指标大面积翻盘
        grid = sch.as_map()
        changed = sum(1 for k, v in grid.items() if set(ctx.seed_crew.get(k, [])) != set(v))
        score -= changed * 1.2
    return score


def optimize(ctx: Context, sch: Schedule, rounds: int = 400) -> Tuple[Schedule, List[str]]:
    """随机换人/换班，只接受「硬约束仍然 0 违规且软指标变好」的改动。"""
    idx = ctx.index
    best = sch.model_copy(deep=True)
    best_score = _soft_score(best, ctx)
    trace: List[str] = []
    accepted = 0
    slots = idx.slots
    for _ in range(rounds):
        cand = best.model_copy(deep=True)
        grid = {slot_key(s.day, s.shift): s for s in cand.slots}
        day, shift = ctx.rng.choice(slots)
        target = grid.get(slot_key(day, shift))
        if target is None or not target.employee_ids:
            continue
        out = ctx.rng.choice(target.employee_ids)
        if out in ctx.pinned.get(slot_key(day, shift), set()):
            continue
        pool = [
            e for e in idx.employee_ids
            if e not in target.employee_ids
            and e not in ctx.excluded
            and e not in ctx.forbidden.get(slot_key(day, shift), set())
            and V.is_available(e, day, None, ctx.extra_leave, config=idx, shift=shift)
        ]
        if not pool:
            continue
        inn = ctx.rng.choice(pool)
        target.employee_ids = [inn if x == out else x for x in target.employee_ids]
        rep = V.validate(cand, ctx.intent, config=idx)
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


def diagnose(ctx: Context, stats: SearchStats) -> Diagnosis:
    idx = ctx.index
    st = State()
    evidence: List[str] = []
    bottleneck_slots: List[str] = []
    bottleneck_rule: Optional[str] = None
    attr_rules = idx.require_attribute_rules()

    for day, shift in idx.slots:
        pool = eligible(ctx, st, day, shift)
        need = need_of(ctx, day, shift)
        label = idx.slot_label(day, shift)
        hit = False
        for rule in attr_rules:
            attr, value, low = _attr_of(rule)
            if not value or low <= 0:
                continue
            have = sum(1 for e in pool if idx.has_attribute(e, attr, value))
            if have >= low:
                continue
            noun = attribute_noun(rule)
            bottleneck_rule = bottleneck_rule or rule.id
            bottleneck_slots.append(label)
            if low == 1:
                evidence.append(f"{label}：可排班人员中 0 名具备{noun}，{rule.id} 不可能满足")
            else:
                evidence.append(f"{label}：可排班人员中仅 {have} 名具备{noun}，{rule.id} 需 ≥{low}")
            hit = True
            break
        if not hit and len(pool) < need:
            rule_id = _headcount_rule_id(idx)
            bottleneck_rule = bottleneck_rule or rule_id
            bottleneck_slots.append(label)
            evidence.append(f"{label}：可排班人员 {len(pool)} 名，低于人数下限 {need} 名")

    # 单日下界：一个人一天最多覆盖 max_shifts_per_day 个班（受时间重叠与最小休息间隔限制），
    # 所以当天各班加起来需要的「不同员工数」有一个下界。旧实现把这个下界写死成 2，只在两班制成立。
    cover = idx.max_shifts_per_day()
    for day in idx.day_ids:
        pool = [
            e for e in idx.employee_ids
            if e not in ctx.excluded and V.is_available(e, day, None, ctx.extra_leave, config=idx)
        ]
        day_need = sum(need_of(ctx, day, s) for s in idx.shift_ids)
        blocked = False
        for rule in attr_rules:
            attr, value, low = _attr_of(rule)
            if not value or low <= 0:
                continue
            required = _ceil_div(low * len(idx.shift_ids), cover)
            have = sum(1 for e in pool if idx.has_attribute(e, attr, value))
            if have >= required:
                continue
            bottleneck_rule = bottleneck_rule or rule.id
            bottleneck_slots.append(idx.day_label(day))
            evidence.append(
                f"{idx.day_label(day)}：全天仅 {have} 名具备{attribute_noun(rule)}的员工可排班，"
                f"但当天 {len(idx.shift_ids)} 个班共需 {required} 名不同员工"
                f"（同一人一天最多上 {cover} 个班）"
            )
            blocked = True
            break
        if not blocked and len(pool) * cover < day_need:
            bottleneck_rule = bottleneck_rule or _headcount_rule_id(idx)
            bottleneck_slots.append(idx.day_label(day))
            evidence.append(
                f"{idx.day_label(day)}：全天可排班 {len(pool)} 人，当天各班共需 {day_need} 人次"
            )

    # 全周期容量下界：供给 vs 需求
    supply = 0
    for e in idx.employee_ids:
        if e in ctx.excluded:
            continue
        days = [d for d in idx.day_ids if V.is_available(e, d, None, ctx.extra_leave, config=idx)]
        cap = idx.max_shifts(e)
        reachable = len(days) * cover
        supply += min(cap, reachable) if cap is not None else reachable
    demand = sum(need_of(ctx, d, s) for d, s in idx.slots)
    if supply < demand:
        bottleneck_rule = bottleneck_rule or _headcount_rule_id(idx)
        evidence.append(f"全周期供给上限 {supply} 人班 < 全周期需求 {demand} 人班，总量不足")

    proven = bool(evidence)
    kind = "proven_infeasible" if proven else ("search_timeout" if stats.budget_exceeded else "proven_infeasible")
    if not proven:
        if stats.budget_exceeded:
            # 超预算 ≠ 无解：必须说清是「算不完」而不是「排不出」，否则店长会去砍约束
            evidence.append(
                f"已用尽求解预算（展开 {stats.nodes} 个节点、耗时 {stats.elapsed_s:.1f}s，"
                f"上限 {ctx.budget.nodes} 节点 / {ctx.budget.time_s:.1f}s），"
                f"当前场景 {idx.slot_count()} 格 × {len(idx.employee_ids)} 名可排员工，"
                f"未能在预算内找到可行解，也未证明无解"
            )
        else:
            evidence.append(
                f"已穷尽当前约束下的候选组合，未找到满足 {len(idx.active_rules)} 条生效规则的排班方案"
            )

    return Diagnosis(
        infeasible=True,
        kind=kind,  # type: ignore[arg-type]
        bottleneck_rule=bottleneck_rule,
        bottleneck_slots=bottleneck_slots[:6],
        evidence=evidence[:8],
        unlock_options=_unlock_options(ctx, bottleneck_rule, bottleneck_slots),
    )


def _headcount_rule_id(idx: ConfigIndex) -> str:
    """人数类瓶颈归属的规则 id。没有 min_staff 规则时退回场景名，避免给出一个不存在的 id。"""
    from .config import RULE_MIN_STAFF

    rules = idx.rules_of(RULE_MIN_STAFF)
    return rules[0].id if rules else "人数下限"


def _ceil_div(a: int, b: int) -> int:
    return -(-a // max(1, b))


def _unlock_options(ctx: Context, rule: Optional[str], slots: List[str]) -> List[UnlockOption]:
    """解锁选项：按瓶颈规则的**类型**给建议，而不是按 R-0x 编号。

    编号是用户可改的显示值，自建规则会是 C-1；按类型分派才不会在改了编号后给出错误建议。
    """
    idx = ctx.index
    where = "、".join(slots[:2]) if slots else "瓶颈班次"
    rule_def = next((r for r in idx.rules if r.id == rule), None)
    rtype = rule_def.type if rule_def else None
    opts: List[UnlockOption] = []

    if rtype == RULE_REQUIRE_ATTRIBUTE:
        noun = attribute_noun(rule_def) if rule_def else "该资质"
        scarce = idx.scarce_attribute()
        is_scarcest = bool(rule_def and scarce and _attr_of(rule_def)[:2] == scarce)
        opts.append(UnlockOption(
            title=f"临时补充{noun}",
            detail=f"为 {where} 指定 1 名员工临时具备{noun}，或从邻店调 1 名具备该资质的员工",
            cost="需培训/认证或人事授权变更",
        ))
        if is_scarcest:
            opts.append(UnlockOption(
                title=f"撤销与{noun}员工冲突的临时请假",
                detail=f"请具备{noun}的员工中一人在瓶颈日改期请假，即可恢复可行",
                cost="影响 1 名员工的休假安排",
            ))
    elif rtype == RULE_MIN_REST:
        opts.append(UnlockOption(
            title="下调最小休息间隔",
            detail=f"把 {where} 涉及的最小休息间隔从 {idx.min_rest_hours or 0:g} 小时下调，需合规确认",
            cost="影响员工休息，需合规确认",
        ))
    else:
        opts.append(UnlockOption(
            title="下调瓶颈班次人数下限",
            detail=f"将 {where} 的人数下限临时下调 1 人，需运营负责人审批",
            cost="服务承诺降级，需书面审批",
        ))
        opts.append(UnlockOption(
            title="放宽单周期班次上限",
            detail="允许 1–2 名全职员工本周期多排 1 个班（涉及加班审批与工时合规）",
            cost="产生加班成本，需合规确认",
        ))
    opts.append(UnlockOption(
        title="扩充可用人力池",
        detail="临时借调 1 名邻店员工（需具备瓶颈班次要求的资质）",
        cost="跨店协调，1–2 天准备期",
    ))
    return opts[:3]


# ---------- 对外入口 ----------


def solve(
    intent: ScheduleRequest,
    base: Optional[Schedule] = None,
    config: ConfigLike = None,
    budget: Optional[SearchBudget] = None,
) -> Tuple[Optional[Schedule], List[str], Diagnosis]:
    ctx = build_context(intent, base, config=config, budget=budget)
    sch, trace, stats = search(ctx)
    if sch is None:
        return None, trace, diagnose(ctx, stats)
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
