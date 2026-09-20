"""L3 规则引擎：按 config.rules 逐条执行的 checker 注册表。

为什么是注册表而不是 if/elif：规则实例数量由用户决定（同一类可以配多条，例如「每班 ≥1 名
店长值守」和「每班 ≥1 名库存管理」是两条 require_attribute），硬写分支只能表达「每类一条」。
dispatch 之后，加一类规则 = 加一个纯函数 + 注册一行，validator 本身不再变化。

三条不变式：

1. **违规必须能定位到格子/员工。** 每条 Violation 都带 rule_id + day/shift/employee_id，
   前端据此把红点画在具体格子上，这是「LLM 不参与合规判定」的可验证性来源。
2. **locked 规则无视 enabled。** respect_unavailability 与 skill_source_integrity 是数据
   完整性，不是业务偏好；允许关掉等于允许「给请假的人排班」。
3. **物理不可行不是业务规则。** 同格重复员工、同一人被排进两个时间重叠的班次，
   在任何配置下都必须报出来，因此它们是引擎级不变式（integrity_violations），
   而不是模板库里的一条可关规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .config import (
    RULE_MAX_CONSECUTIVE,
    RULE_MAX_SHIFTS,
    RULE_MIN_REST,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    RULE_RESPECT_UNAVAILABILITY,
    RULE_SKILL_INTEGRITY,
    ConfigIndex,
    RuleDef,
    attribute_noun,
    rule_min_staff,
    valid_hours,
    valid_limit,
)
from .models import Schedule, Violation

Assignment = Tuple[str, str]      # (day, shift)


@dataclass
class ScheduleView:
    """一张排班表在校验视角下的全部事实。

    先统一算好、再交给各 checker，是为了让「同一份事实喂给所有规则」——
    否则每条规则各自遍历一遍 slots，去重口径迟早会出现分歧。
    """

    index: ConfigIndex
    members: Dict[Assignment, List[str]] = field(default_factory=dict)      # 去重后的成员（含未知工号）
    known: Dict[Assignment, List[str]] = field(default_factory=dict)        # 只含配置里存在的工号
    duplicated: List[Assignment] = field(default_factory=list)              # 出现重复成员的格子
    unknown: List[Tuple[str, str, str]] = field(default_factory=list)       # (day, shift, 未知工号)
    counts: Dict[str, int] = field(default_factory=dict)                    # 含未知工号，与旧口径一致
    per_employee: Dict[str, Dict[str, Set[str]]] = field(default_factory=dict)
    blocked: Dict[str, Set[str]] = field(default_factory=dict)              # 意图里的临时请假

    def assignments_of(self, eid: str) -> List[Assignment]:
        daymap = self.per_employee.get(eid, {})
        pos = self.index.shift_pos
        return [
            (d, s)
            for d in self.index.day_ids
            for s in sorted(daymap.get(d, ()), key=lambda x: pos.get(x, 0))
        ]


def build_view(
    schedule: Schedule,
    index: ConfigIndex,
    blocked: Optional[Dict[str, Set[str]]] = None,
) -> ScheduleView:
    view = ScheduleView(index=index, blocked=dict(blocked or {}))
    grid = schedule.as_map()
    view.per_employee = {eid: {d: set() for d in index.day_ids} for eid in index.all_employees}

    for day, shift in index.slots:
        raw = grid.get(f"{day}|{shift}", [])
        uniq = list(dict.fromkeys(raw))
        if len(uniq) != len(raw):
            view.duplicated.append((day, shift))
        view.members[(day, shift)] = uniq
        known: List[str] = []
        for eid in uniq:
            view.counts[eid] = view.counts.get(eid, 0) + 1
            if index.known(eid):
                known.append(eid)
                view.per_employee[eid][day].add(shift)
            else:
                view.unknown.append((day, shift, eid))
        view.known[(day, shift)] = known
    return view


def _v(rule: RuleDef, detail: str, day: Optional[str] = None, shift: Optional[str] = None,
       employee_id: Optional[str] = None) -> Violation:
    return Violation(
        rule_id=rule.id,
        rule_text=rule.name,
        day=day,
        shift=shift,
        employee_id=employee_id,
        detail=detail,
    )


# ---------- 8 类 checker ----------


def check_min_staff_per_shift(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    out: List[Violation] = []
    idx = view.index
    for day, shift in idx.slots:
        need = rule_min_staff(rule, idx.days.get(day), day, shift)
        have = len(view.known.get((day, shift), []))
        if have < need:
            out.append(_v(rule, f"本班 {have} 人，低于下限 {need} 人", day=day, shift=shift))
    return out


def check_require_attribute(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    idx = view.index
    params = rule.params or {}
    attr = str(params.get("attr") or "skill")
    value = str(params.get("value") or "")
    need = valid_limit(params.get("min"))
    noun = attribute_noun(rule)
    out: List[Violation] = []
    # 参数不合法 = 这条规则不生效（口径见 config.valid_limit，入口由 config_check 拦）
    if not value or need is None:
        return out
    for day, shift in idx.slots:
        have = sum(1 for e in view.known.get((day, shift), []) if idx.has_attribute(e, attr, value))
        if have >= need:
            continue
        # 「一个都没有」和「差几个」是店长要采取的两种不同动作，文案分开
        detail = f"本班无具备{noun}的员工" if need == 1 else f"本班具备{noun}的员工仅 {have} 名，需 ≥{need}"
        out.append(_v(rule, detail, day=day, shift=shift))
    return out


def check_max_shifts_per_period(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    idx = view.index
    global_max = valid_limit((rule.params or {}).get("max"))
    out: List[Violation] = []
    for eid, n in view.counts.items():
        emp = idx.employee(eid)
        # 个人上限优先：契约里 max_shifts=null 才回落到全局规则。
        # 个人上限 0 是「本周期不排这个人」，必须照字面判——solver 也是这么执行的，
        # 这里若当成「没配」回落到全局，同一张表就会出现 solver 不排、校验说合规的分裂
        personal = int(emp.max_shifts) if (emp is not None and emp.max_shifts is not None) else None
        limit = personal if personal is not None else global_max
        if limit is None or n <= limit:
            continue
        hours = sum(idx.shifts[s].hours for d, s in view.assignments_of(eid)) if idx.known(eid) else n * 8
        out.append(_v(
            rule,
            f"{eid} 本周 {n} 个班（{_num(hours)} 小时），超出上限 {limit} 个班",
            employee_id=eid,
        ))
    return out


def check_max_consecutive_days(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    idx = view.index
    limit = valid_limit((rule.params or {}).get("max"))
    out: List[Violation] = []
    # 口径见 config.valid_limit：max < 1 是非法参数（由 config_check 报 invalid_rule_params），
    # 这里与 solver 一致地视为「这条规则不生效」，而不是各自猜一种解释
    if limit is None:
        return out
    for eid, daymap in view.per_employee.items():
        run = 0
        run_days: List[str] = []
        for d in idx.day_ids:
            if daymap.get(d):
                run += 1
                run_days.append(d)
                if run > limit:
                    labels = "、".join(idx.day_label(x) for x in run_days)
                    out.append(_v(
                        rule,
                        f"{eid} 连续工作 {run} 天（{labels}），超出上限 {limit} 天",
                        employee_id=eid,
                    ))
                    # 一个人报一次就够：继续数下去只会把同一段连班刷成 N 条重复违规
                    break
            else:
                run = 0
                run_days = []
    return out


def check_min_rest_hours(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    """相邻班次休息间隔。R-07「晚班接次日早班」的泛化形态。

    两个边界值得写下来：

    - **恰好等于 hours 合规，只有严格小于才违规。** 参数在界面上叫「最少休息 X 小时」，
      配置的人据此理解为「给够 X 就行」，引擎必须按这个语义判，否则填 12 实际要 12 以上，
      等于用参数名骗人。旧 R-07 的等价不靠比较符维持，而靠 default_config() 把默认值取成
      13：晚班 21:00 → 次日早班 09:00 只有 12 小时，12 < 13 仍然违规，旧行为不变。
    - **时间重叠（间隔为负）不在这里报。** 那不是休息不够，而是同一人不可能同时在两处，
      属于引擎级不变式，见 integrity_violations()。
    """
    idx = view.index
    hours = valid_hours((rule.params or {}).get("hours"))
    out: List[Violation] = []
    if hours is None:
        return out
    for eid in view.per_employee:
        seq = view.assignments_of(eid)
        if len(seq) < 2:
            continue
        ordered = sorted(seq, key=lambda a: idx.span(*a)[0])
        for prev, cur in zip(ordered, ordered[1:]):
            gap = idx.rest_gap_hours(prev, cur)
            if gap < 0 or gap >= hours:
                continue
            out.append(_v(
                rule,
                f"{eid} {idx.slot_label(*prev)}后被安排{idx.slot_label(*cur)}，间隔不足",
                day=cur[0],
                shift=cur[1],
                employee_id=eid,
            ))
    return out


def check_one_shift_per_day(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    idx = view.index
    out: List[Violation] = []
    for eid, daymap in view.per_employee.items():
        for d in idx.day_ids:
            shifts = daymap.get(d) or set()
            if len(shifts) <= 1:
                continue
            names = "、".join(idx.shift_name(s) for s in sorted(shifts, key=lambda x: idx.shift_pos.get(x, 0)))
            out.append(_v(
                rule,
                f"{eid} {idx.day_label(d)}被安排 {len(shifts)} 个班（{names}），超出每天 1 个班",
                day=d,
                employee_id=eid,
            ))
    return out


def check_respect_unavailability(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    """locked：不可用时段绝对不得排班（含配置里的不可用与意图里的临时请假）。"""
    idx = view.index
    out: List[Violation] = []
    for day, shift in idx.slots:
        for eid in view.known.get((day, shift), []):
            emp = idx.employee(eid)
            label = idx.day_label(day)
            if emp is not None and not emp.active:
                out.append(_v(rule, f"{eid} 已停用，不应出现在新排班中", day=day, shift=shift, employee_id=eid))
                continue
            hit = idx.blocked_by(eid, day, shift)
            if hit is not None:
                if hit.reason:
                    detail = f"{eid} {label}{hit.reason}"
                elif hit.shift is None:
                    detail = f"{eid} 的可工作日期不含{label}"
                else:
                    detail = f"{eid} {label}{idx.shift_name(shift)}不可排班"
                out.append(_v(rule, detail, day=day, shift=shift, employee_id=eid))
            elif day in view.blocked.get(eid, set()):
                out.append(_v(rule, f"{eid} {label}临时请假", day=day, shift=shift, employee_id=eid))
    return out


def check_skill_source_integrity(rule: RuleDef, view: ScheduleView) -> List[Violation]:
    """locked：排班里出现的人必须存在于员工档案。

    这是「Agent 不得自行补技能」的可执行形态——技能只能从档案里查，
    档案里没有这个人，任何技能判断都无从谈起，必须当场拦下而不是按 0 技能继续算。
    """
    return [
        _v(rule, f"{eid} 不在员工数据中", day=day, shift=shift, employee_id=eid)
        for day, shift, eid in view.unknown
    ]


CHECKERS: Dict[str, Callable[[RuleDef, ScheduleView], List[Violation]]] = {
    RULE_MIN_STAFF: check_min_staff_per_shift,
    RULE_REQUIRE_ATTRIBUTE: check_require_attribute,
    RULE_MAX_SHIFTS: check_max_shifts_per_period,
    RULE_MAX_CONSECUTIVE: check_max_consecutive_days,
    RULE_MIN_REST: check_min_rest_hours,
    RULE_ONE_SHIFT_PER_DAY: check_one_shift_per_day,
    RULE_RESPECT_UNAVAILABILITY: check_respect_unavailability,
    RULE_SKILL_INTEGRITY: check_skill_source_integrity,
}


# ---------- 引擎级不变式 ----------


def integrity_rule(index: ConfigIndex, *prefer: str) -> Optional[RuleDef]:
    """物理不可行结论要挂到哪条规则上。

    必须挂到一条**生效中**的规则：挂到被用户关掉的规则上，前端会在一条灰掉的规则上
    显示红点；挂到不存在的 id 上更糟——serializers 按 rule_results 分组渲染，
    找不到归属的违规会彻底消失，出现「passed=false 但看不到任何违规」。

    默认配置下 prefer 命中 R-04（重复员工）与 R-05（同日两班重叠），与旧实现的归类一致。
    """
    for rtype in prefer:
        rules = index.rules_of(rtype)
        if rules:
            return rules[0]
    for rule in index.active_rules:
        if rule.locked:
            return rule
    return index.active_rules[0] if index.active_rules else None


def integrity_violations(view: ScheduleView) -> List[Violation]:
    idx = view.index
    out: List[Violation] = []

    dup_rule = integrity_rule(idx, RULE_MIN_STAFF)
    if dup_rule is not None:
        for day, shift in view.duplicated:
            # 重复成员会把人数下限「刷」到达标，所以归在人数口径下
            out.append(_v(dup_rule, "同一班次出现重复员工", day=day, shift=shift))

    overlap_rule = integrity_rule(idx, RULE_MAX_SHIFTS, RULE_MIN_REST, RULE_ONE_SHIFT_PER_DAY)
    if overlap_rule is None:
        return out
    for eid in view.per_employee:
        seq = view.assignments_of(eid)
        if len(seq) < 2:
            continue
        ordered = sorted(seq, key=lambda a: idx.span(*a)[0])
        for i, prev in enumerate(ordered):
            for cur in ordered[i + 1:]:
                if not idx.overlaps(prev, cur):
                    break            # 按开始时间排过序，后面的只会更晚
                hours = idx.shifts[prev[1]].hours + idx.shifts[cur[1]].hours
                if prev[0] == cur[0]:
                    detail = (
                        f"{eid} {idx.day_label(prev[0])}被同时安排"
                        f"{idx.shift_name(prev[1])}与{idx.shift_name(cur[1])}，"
                        f"两班时间重叠且单日达 {_num(hours)} 小时"
                    )
                else:
                    detail = (
                        f"{eid} {idx.slot_label(*prev)}与{idx.slot_label(*cur)}时间重叠，"
                        f"同一人不可能同时在岗"
                    )
                out.append(_v(overlap_rule, detail, day=prev[0], employee_id=eid))
    return out


# ---------- 引擎入口 ----------


def run(view: ScheduleView) -> List[Violation]:
    """按配置顺序执行规则，最后追加引擎级不变式。

    顺序即前端看到的顺序：同一条规则内先业务判定、后物理不可行，与旧实现一致
    （旧实现也是先数人数/工时，再报同日两班重叠）。
    """
    out: List[Violation] = []
    for rule in view.index.active_rules:
        checker = CHECKERS.get(rule.type)
        if checker is None:
            # 未知 type 由配置自检报 unknown_rule_type；这里静默跳过，
            # 否则一条来自旧版本 localStorage 的规则会让整次校验 500
            continue
        out.extend(checker(rule, view))
    out.extend(integrity_violations(view))
    return out


def rule_ids_in_order(index: ConfigIndex) -> List[RuleDef]:
    """rule_results 的顺序与去重口径。

    按配置顺序、按 id 去重：id 是前端的 React key 与文案锚点，重复 id 会让同一条规则
    渲染两遍，因此这里以第一条出现的实例为准。
    """
    seen: Set[str] = set()
    out: List[RuleDef] = []
    for rule in index.rules:
        if rule.id in seen:
            continue
        seen.add(rule.id)
        out.append(rule)
    return out


def _num(value: float) -> str:
    """8.0 → 8，7.5 → 7.5。工时是小时数，整数不要显示成 8.0。"""
    return f"{value:g}"


def attribute_counts(index: ConfigIndex, members: Sequence[str], rule: RuleDef) -> int:
    params = rule.params or {}
    attr = str(params.get("attr") or "skill")
    value = str(params.get("value") or "")
    return sum(1 for e in members if index.has_attribute(e, attr, value))
