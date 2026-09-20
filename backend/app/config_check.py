"""配置自检：在用户点「生成」之前就回答「这份配置有没有解」。

为什么值得单独一层：无解诊断（solver.diagnose）只有在跑完一次求解之后才能给出，而配置期的
错误大多是**结构性**的——员工池根本凑不出每班下限、要求的技能一个人都没有。这类问题让用户
等一次求解再看到「无解」是纯粹的浪费，而且「无解」这个词会把他引向砍规则，而不是补员工。

两条边界：

1. **只报「一定无解」的 error。** 判定必须是充分条件（供给上界 < 需求下界），不做启发式猜测；
   拿不准的一律降级成 warning。误报一个 error 会把用户挡在生成之外，比漏报更糟。
2. **error 的 where 指到格子。** 前端配置页要能把红点画在具体的天/班上，
   所以每条 slot 级 error 都带 day/shift id。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .config import (
    LOCKED_RULE_TYPES,
    MAX_DAYS,
    MAX_SHIFTS,
    RULE_MAX_CONSECUTIVE,
    RULE_MAX_SHIFTS,
    RULE_MIN_REST,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    RULE_TYPES,
    ConfigIndex,
    ConfigLike,
    RuleDef,
    attribute_noun,
    index_of,
    valid_hours,
    valid_limit,
)

# 每类 slot 级问题最多列几条：56 格 × 多条规则会刷出上百条同质 error，
# 配置页滚动三屏找不到重点。超出的部分折成一句「另有 N 格同类问题」
MAX_PER_CODE = 4

# 冗余告警阈值。10% 来自契约的 fix 文案「建议至少留 10% 冗余」
HEADROOM_WARN_PCT = 10.0


def _issue(code: str, message: str, fix: str, where: Optional[dict] = None) -> dict:
    return {"code": code, "message": message, "where": where, "fix": fix}


def _where(day: Optional[str] = None, shift: Optional[str] = None,
           employee_id: Optional[str] = None, rule_id: Optional[str] = None) -> dict:
    out: Dict[str, str] = {}
    if day is not None:
        out["day"] = day
    if shift is not None:
        out["shift"] = shift
    if employee_id is not None:
        out["employee_id"] = employee_id
    if rule_id is not None:
        out["rule_id"] = rule_id
    return out


def check(config: ConfigLike = None) -> dict:
    """返回与 /api/config/validate 契约同构的自检结果。"""
    index = index_of(config)
    errors: List[dict] = []
    warnings: List[dict] = []

    _check_scenario(index, errors)
    _check_employees(index, errors, warnings)
    _check_rules(index, errors, warnings)
    capacity = _capacity(index)
    # 容量类判定依赖维度与员工池都成立，前面已经报错时它只会输出噪声
    if not errors:
        _check_supply(index, capacity, errors, warnings)

    return {"ok": not errors, "errors": errors, "warnings": warnings, "capacity": capacity}


# ---------- 结构 ----------


def _check_scenario(index: ConfigIndex, errors: List[dict]) -> None:
    days, shifts = index.config.scenario.days, index.config.scenario.shifts
    if not days or not shifts:
        errors.append(_issue(
            "empty_scenario",
            f"场景需要至少 1 天和 1 个班次，当前 {len(days)} 天 × {len(shifts)} 班",
            "在「场景」页添加日期与班次",
        ))
        return

    if len(days) > MAX_DAYS or len(shifts) > MAX_SHIFTS:
        # 上限不是产品口味：超过 14 天 × 4 班后回溯搜索无法在秒级预算内收敛，
        # 放开只会把「配置错误」换成「等 60 秒拿到超时」
        errors.append(_issue(
            "scenario_too_large",
            f"当前 {len(days)} 天 × {len(shifts)} 班超出上限（最多 {MAX_DAYS} 天 × {MAX_SHIFTS} 班）",
            f"拆成多个周期，每个周期不超过 {MAX_DAYS} 天 × {MAX_SHIFTS} 班",
        ))

    for label, code, ids in (
        ("日期", "duplicate_day_id", [d.id for d in days]),
        ("班次", "duplicate_shift_id", [s.id for s in shifts]),
    ):
        dup = _dups(ids)
        if dup:
            # id 重复会让后面的定义静默覆盖前面的，排班格子凭空少掉几个且没有任何提示
            errors.append(_issue(
                code,
                f"{label} id 重复：{'、'.join(dup)}",
                f"给每个{label}一个唯一 id",
            ))


def _check_employees(index: ConfigIndex, errors: List[dict], warnings: List[dict]) -> None:
    cfg = index.config
    dup = _dups([e.id for e in cfg.employees])
    if dup:
        errors.append(_issue(
            "duplicate_employee_id",
            f"工号重复：{'、'.join(dup)}",
            "工号是排班表的唯一标识，请改成不重复的编号",
        ))

    if not index.employee_ids:
        errors.append(_issue(
            "no_employees",
            "没有启用状态的员工，无法排班",
            "在「员工」页添加员工，或把已停用的员工重新启用",
        ))

    pool = set(cfg.skill_pool)
    unknown: List[Tuple[str, str]] = [
        (e.id, s) for e in cfg.employees for s in e.skills if s not in pool
    ]
    if unknown:
        # 技能字典是 R-09 的配置化形态：档案外的技能等于「凭空补技能」，
        # require_attribute 会永远匹配不到它，表现为莫名其妙的无解
        for eid, skill in unknown[:MAX_PER_CODE]:
            errors.append(_issue(
                "unknown_skill",
                f"{eid} 的技能「{skill}」不在技能字典中",
                "把该技能加入技能字典，或从员工档案里移除",
                _where(employee_id=eid),
            ))
        _truncated(errors, "unknown_skill", len(unknown))

    for e in cfg.employees:
        bad_days = sorted({u.day for u in e.unavailable if u.day not in index.days})
        bad_shifts = sorted({u.shift for u in e.unavailable if u.shift and u.shift not in index.shifts})
        bad_prefs = [s for s in e.preferred_shifts if s not in index.shifts]
        if bad_days or bad_shifts:
            # 只是 warning：悬空引用不会造成错排，但会让用户以为已经设了请假
            warnings.append(_issue(
                "unknown_unavailable_ref",
                f"{e.id} 的不可用时段引用了不存在的日期/班次：{'、'.join(bad_days + bad_shifts)}",
                "改用当前场景里的日期与班次重新设置，否则这条不可用不会生效",
                _where(employee_id=e.id),
            ))
        if bad_prefs:
            warnings.append(_issue(
                "unknown_preferred_shift",
                f"{e.id} 的偏好班次「{'、'.join(bad_prefs)}」不在当前场景中",
                "重新选择偏好班次；不生效的偏好会让偏好满足率偏低",
                _where(employee_id=e.id),
            ))


def _check_rules(index: ConfigIndex, errors: List[dict], warnings: List[dict]) -> None:
    dup = _dups([r.id for r in index.config.rules])
    if dup:
        warnings.append(_issue(
            "duplicate_rule_id",
            f"规则编号重复：{'、'.join(dup)}",
            "改成唯一编号，否则校验报告里同一编号只会显示一条",
            _where(rule_id=dup[0]),
        ))

    for rule in index.config.rules:
        if rule.type not in RULE_TYPES:
            # 来自旧版本 localStorage 的规则会走到这里。报 error 而不是静默跳过：
            # 静默等于「用户以为这条规则在生效」，比配置页一条红字危险得多
            errors.append(_issue(
                "unknown_rule_type",
                f"规则 {rule.id} 的类型「{rule.type}」不在模板库中",
                f"改成模板库支持的类型（{'、'.join(RULE_TYPES)}），或删除该规则",
                _where(rule_id=rule.id),
            ))
            continue
        # 只查会真正生效的规则：停用的规则不参与求解，为它的参数拦住整份配置属于误拦。
        # 用户在配置页把它重新启用时，自检会立刻报出来
        if rule.enabled or rule.locked:
            _check_rule_params(index, rule, errors, warnings)
        if not rule.enabled and rule.type in LOCKED_RULE_TYPES:
            warnings.append(_issue(
                "locked_rule_forced",
                f"规则 {rule.id} 是系统内建的数据完整性规则，即使停用也会继续执行",
                "无需处理；这条规则保证不会给不可用时段或档案外的员工排班",
                _where(rule_id=rule.id),
            ))

    if not [r for r in index.active_rules if r.type not in LOCKED_RULE_TYPES]:
        warnings.append(_issue(
            "no_active_business_rules",
            "没有任何启用的业务规则，排班只会保证不排到不可用时段",
            "至少启用一条人数下限或资质要求规则",
        ))

    gap = index.min_day_gap()
    if gap > 1 and len(index.day_ids) > 1:
        # 休息间隔跨过一整天时，供给会被腰斩，而后面的容量数字看不出原因。
        # 不报 error：需求足够小时这份配置仍然可解，真的不够会由容量 error 命中
        rest_rules = index.rules_of(RULE_MIN_REST)
        hours = index.min_rest_hours
        span = "整个周期内每人最多只能上 1 个班" if gap > len(index.day_ids) - 1 \
            else f"每人两次上班至少要隔 {gap} 天"
        warnings.append(_issue(
            "rest_forces_day_gap",
            f"最少休息 {hours:g} 小时的要求跨过了一整天：按当前班次时间，{span}",
            "把休息小时数改到班次间隔之内（通常 10～14 小时），否则可排人次会大幅减少",
            _where(rule_id=rest_rules[0].id) if rest_rules else None,
        ))


# ---------- 规则参数 ----------

# 属性类规则允许的 attr。写死在这里而不是接受任意字符串：拼错的 attr 会让
# require_attribute 永远匹配不到人，表现为「明明有人有这个技能却说无解」
_ATTRS = ("skill", "role")


def _check_rule_params(index: ConfigIndex, rule: RuleDef, errors: List[dict], warnings: List[dict]) -> None:
    """规则参数的合法性。非法参数一律报 error，绝不替用户挑一种解释。

    为什么不兜底：`max_consecutive_days.max = 0` 字面意思是「一天都不许连续工作」，
    也就是谁都不能排班；但用户填 0 时想说的几乎一定是「不限制」。两种解释都不是配置
    写下来的东西，而引擎各兜一种的结果就是 solver 判死、validator 判合规（见
    config.valid_limit 的注释）。所以这里在入口就拒绝，让用户自己说清楚——要不限制就
    停用规则，要限制就填一个 ≥1 的数。

    返回的 code 统一是 invalid_rule_params：前端只需要把红字挂在这条规则上，
    具体错在哪个参数由 message 承担。
    """
    params = rule.params or {}

    def bad(message: str, fix: str) -> None:
        errors.append(_issue("invalid_rule_params", f"规则 {rule.id} {message}", fix, _where(rule_id=rule.id)))

    def limit(key: str, label: str, fix: str) -> None:
        """上限类参数：必须存在且 ≥1，口径与 config.valid_limit 一致。"""
        raw = params.get(key)
        if raw is None:
            bad(f"缺少「{label}」参数", fix)
        elif valid_limit(raw) is None:
            bad(f"的{label}是 {_show(raw)}，必须是 ≥1 的整数", fix)

    def count(key: str, label: str, raw: object) -> None:
        """人数类参数：0 有意义（这一格不要求人数），负数与非数字没有。"""
        if _as_count(raw) is None:
            bad(f"的{label}是 {_show(raw)}，必须是 ≥0 的整数",
                "填 ≥0 的整数；填 0 表示这里不设人数下限")

    if rule.type == RULE_MIN_STAFF:
        for key, label in (("default", "默认人数下限"), ("peak", "高峰日人数下限")):
            # 显式 null 与「没写这个键」一样是「不设这一项」，只有写了值又不是人数才算错。
            # 前端把「高峰日不另设人数」序列化成 peak: null 是完全正常的
            if params.get(key) is not None:
                count(key, label, params.get(key))
        _check_min_staff_overrides(index, rule, params, errors, warnings, bad)

    elif rule.type == RULE_REQUIRE_ATTRIBUTE:
        attr = params.get("attr")
        if attr is not None and str(attr) not in _ATTRS:
            bad(f"的属性类型「{attr}」无法识别", f"改成 {'、'.join(_ATTRS)} 之一")
        if not str(params.get("value") or "").strip():
            bad("没有指定要求的技能/角色", "填写这条规则要求的技能或角色名称")
        limit("min", "人数要求", "填 ≥1 的整数；不需要这项资质请停用这条规则")

    elif rule.type == RULE_MAX_SHIFTS:
        limit("max", "每人班次上限", "填 ≥1 的整数；不想限制班次总数请停用这条规则")

    elif rule.type == RULE_MAX_CONSECUTIVE:
        # 「不限制」的正确写法在 fix 里说清楚，否则用户会再填一次 0
        limit("max", "最多连续工作天数",
              f"最多连续工作天数至少为 1；想表达「不限制」请填 {len(index.day_ids)}（周期天数）"
              "或更大的值，或者直接停用这条规则")

    elif rule.type == RULE_MIN_REST:
        raw = params.get("hours")
        if raw is None:
            bad("缺少「最少休息小时数」参数", "填一个大于 0 的小时数")
        elif valid_hours(raw) is None:
            bad(f"的最少休息小时数是 {_show(raw)}，必须大于 0",
                "填大于 0 的小时数；不想限制休息间隔请停用这条规则")


def _check_min_staff_overrides(index: ConfigIndex, rule: RuleDef, params: dict,
                               errors: List[dict], warnings: List[dict], bad) -> None:
    overrides = params.get("overrides")
    if overrides is None:
        return
    if not isinstance(overrides, list):
        bad("的单格人数覆盖不是列表", "删除这项，或改成 [{day, shift, min}] 形式")
        return
    dangling: List[str] = []
    for ov in overrides:
        if not isinstance(ov, dict):
            bad(f"的单格人数覆盖里有一项不是对象（{_show(ov)}）", "每一项都要写成 {day, shift, min}")
            continue
        day, shift = str(ov.get("day") or ""), str(ov.get("shift") or "")
        if "min" not in ov:
            bad(f"的单格人数覆盖（{day or '?'}/{shift or '?'}）没有填人数", "补上 min，或删除这一项")
        elif _as_count(ov.get("min")) is None:
            bad(f"的单格人数覆盖（{day or '?'}/{shift or '?'}）人数是 {_show(ov.get('min'))}，"
                "必须是 ≥0 的整数",
                "填 ≥0 的整数；填 0 表示这一格不设人数下限")
        if day not in index.days or shift not in index.shifts:
            dangling.append(f"{day or '?'}/{shift or '?'}")
    if dangling:
        # 悬空覆盖不会错排，但会静默失效——用户以为「周六晚班已经加到 3 人」
        warnings.append(_issue(
            "unknown_override_ref",
            f"规则 {rule.id} 的单格人数覆盖引用了不存在的日期/班次：{'、'.join(dangling)}",
            "改用当前场景里的日期与班次，否则这条覆盖不会生效",
            _where(rule_id=rule.id),
        ))


def _as_count(value: object) -> Optional[int]:
    """人数类参数的口径：非负整数，其余（负数、非数字）返回 None。"""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _show(value: object) -> str:
    return "空" if value is None or value == "" else f"「{value}」"


# ---------- 容量 ----------


def _capacity(index: ConfigIndex) -> dict:
    per_slot = [
        {
            "day": day,
            "shift": shift,
            "min_required": index.min_required(day, shift),
            "eligible": len(_eligible(index, day, shift)),
        }
        for day, shift in index.slots
    ]
    demand = sum(s["min_required"] for s in per_slot)
    supply = sum(_employee_capacity(index, eid) for eid in index.employee_ids)
    return {
        "demand_person_shifts": demand,
        "supply_person_shifts": supply,
        # 需求为 0 时冗余率无意义，给 0 而不是 inf：前端要拿它画进度条
        "headroom_pct": round((supply - demand) / demand * 100, 1) if demand else 0.0,
        "per_slot": per_slot,
    }


def _employee_capacity(index: ConfigIndex, eid: str) -> int:
    """一个员工在本周期内最多能承担几个班次（取「可排格数」与「班次上限」的较小值）。"""
    reachable = index.assignable_cells(eid)
    cap = index.max_shifts(eid)
    return min(reachable, cap) if cap is not None else reachable


def _eligible(index: ConfigIndex, day: str, shift: str) -> List[str]:
    return [e for e in index.employee_ids if index.can_work(e, day, shift)]


def _eligible_day(index: ConfigIndex, day: str) -> List[str]:
    """当天能排班的员工。整日不可用才算排除，与 solver.diagnose 的按天口径一致。"""
    return [e for e in index.employee_ids if index.can_work(e, day)]


def _attr_rules(index: ConfigIndex) -> List[Tuple[RuleDef, str, str, int]]:
    """生效中的资质规则，连同解析好的 (attr, value, min)。参数不合法的规则不参与容量判定。

    不合法的参数已经由 _check_rule_params 报成 error，配置根本过不了自检；这里跳过它们
    只是为了不让一个脏参数把容量判定也带崩（口径见 config.valid_limit）。
    """
    out: List[Tuple[RuleDef, str, str, int]] = []
    for rule in index.require_attribute_rules():
        params = rule.params or {}
        attr = str(params.get("attr") or "skill")
        value = str(params.get("value") or "")
        need = valid_limit(params.get("min"))
        if value and need is not None:
            out.append((rule, attr, value, need))
    return out


def _headcount_rule(index: ConfigIndex) -> Optional[RuleDef]:
    """人数类问题归属的规则。没有 min_staff 规则时返回 None，绝不编一个不存在的 id。"""
    rules = index.rules_of(RULE_MIN_STAFF)
    return rules[0] if rules else None


def _cover_note(index: ConfigIndex, cover: int) -> str:
    """把「一人一天最多几个班」这个上界的来源说清楚。

    店长看到「8 人排不了 9 人次」的第一反应是「一个人多上一个班不就够了」，
    所以证据里必须写明为什么不能。
    """
    rules = index.rules_of(RULE_ONE_SHIFT_PER_DAY)
    if rules:
        return f"规则 {rules[0].id} 限制每人每天最多 1 个班"
    if cover == 1:
        return "班次时间重叠或最小休息间隔限制，一人一天最多 1 个班"
    return f"一人一天最多 {cover} 个班"


def _cover_fix(index: ConfigIndex) -> str:
    rules = index.rules_of(RULE_ONE_SHIFT_PER_DAY)
    relax = f"、停用规则 {rules[0].id}（允许一人一天上多个班）" if rules else ""
    return f"降低当天的人数下限{relax}，或给这天补上可排班的员工"


def _check_supply(index: ConfigIndex, capacity: dict, errors: List[dict], warnings: List[dict]) -> None:
    """三层容量判定，从最具体到最笼统：单格 → 单日 → 全周期。

    层次顺序不是排版口味：单格不足是最可操作的结论（改这一格的下限或不可用设置），
    而全周期总量不足只能靠加人解决。同一份配置常常三层都命中，先报最具体的那层
    才能让用户第一步就改对地方。这三层与 solver.diagnose 的下界一一对应——自检漏掉
    任何一层，都会变成「配置页说没问题、点生成拿到一条求解器证明的无解」。
    """
    short: List[dict] = []
    for row in capacity["per_slot"]:
        if row["eligible"] < row["min_required"]:
            short.append(row)
    for row in short[:MAX_PER_CODE]:
        label = index.slot_label(row["day"], row["shift"])
        errors.append(_issue(
            "supply_lt_demand",
            f"{label}需要 {row['min_required']} 人，但当天可排班的员工只有 {row['eligible']} 人",
            "降低该班人数下限，或减少当天的不可用设置",
            _where(day=row["day"], shift=row["shift"]),
        ))
    _truncated(errors, "supply_lt_demand", len(short))
    # 已经报过单格问题的天不再报按天问题：同一天两条 error 里，格子级那条更可操作
    hit_days = {row["day"] for row in short}

    for rule, attr, value, need in _attr_rules(index):
        noun = attribute_noun(rule)
        pool = index.attribute_pool(attr, value)
        if not pool:
            errors.append(_issue(
                "attribute_absent",
                f"规则 {rule.id} 要求每班 ≥{need} 名具备{noun}的员工，但员工池里没有任何人具备",
                f"给至少 {need} 名员工补上该项，或停用规则 {rule.id}",
                _where(rule_id=rule.id),
            ))
            continue
        lacking = [
            (day, shift, have)
            for day, shift in index.slots
            if (have := sum(1 for e in _eligible(index, day, shift) if index.has_attribute(e, attr, value))) < need
        ]
        for day, shift, have in lacking[:MAX_PER_CODE]:
            errors.append(_issue(
                "attribute_supply_lt_demand",
                f"{index.slot_label(day, shift)}可排班员工中仅 {have} 名具备{noun}，规则 {rule.id} 需 ≥{need}",
                "调整该班的不可用设置，或给更多员工补上该项",
                _where(day=day, shift=shift, rule_id=rule.id),
            ))
        _truncated(errors, "attribute_supply_lt_demand", len(lacking))
        hit_days |= {day for day, _shift, _have in lacking}

    _check_daily(index, hit_days, errors, warnings)

    demand, supply = capacity["demand_person_shifts"], capacity["supply_person_shifts"]
    if demand and supply < demand:
        errors.append(_issue(
            "capacity_lt_total_demand",
            f"全周期需求 {demand} 人次，但全员上限之和只有 {supply} 人次",
            "增加员工、放宽每人班次上限，或降低人数下限",
        ))
    elif demand and capacity["headroom_pct"] < HEADROOM_WARN_PCT:
        message = (
            "总供给刚好等于总需求，任何一人请假都会导致无解"
            if supply == demand
            else f"总供给仅比总需求多 {capacity['headroom_pct']}%，个别员工请假就可能无解"
        )
        warnings.append(_issue(
            "no_headroom",
            message,
            f"建议至少留 {HEADROOM_WARN_PCT:g}% 冗余：增加员工或放宽班次上限",
        ))


def _check_daily(index: ConfigIndex, skip_days: set, errors: List[dict], warnings: List[dict]) -> None:
    """按天的容量下界：一人一天最多覆盖 cover 个班，所以当天需求人次 ≤ 可排人数 × cover。

    全周期总量够、每一格单独看也够，但**某一天**凑不齐，是配置化之后最容易踩的坑：
    3 天 × 3 班 8 人、其中一天是高峰日（3+3+3=9 人次），总量绰绰有余，可这天只有 8 个人，
    而每人一天只能上一个班——求解器能证明这必然无解，自检以前却放行。

    error 的门槛取「数学上可证明」，而不是「one_shift_per_day 有没有开」：cover 已经把
    规则关闭的情形折算进去了（关掉且班次互不重叠时 cover = 班次数，判定自动放宽，不会误拦）；
    反过来按「规则是否开启」判会漏掉「规则关着、但班次两两重叠」的配置——默认的
    09–17 / 13–21 正是这种，一个人一天照样只能上一个班。
    """
    cover = index.max_shifts_per_day()
    note = _cover_note(index, cover)
    fix = _cover_fix(index)
    head = _headcount_rule(index)
    n_shifts = len(index.shift_ids)
    attr_rules = _attr_rules(index)

    short: List[dict] = []
    lacking: List[dict] = []
    tight: List[dict] = []
    for day in index.day_ids:
        if day in skip_days:
            continue
        pool = _eligible_day(index, day)
        day_need = sum(index.min_required(day, s) for s in index.shift_ids)

        # 资质的按天下界：每班都要 need 名具备该项的**在岗**员工，一人一天只能覆盖 cover 个班，
        # 所以当天至少要有 ⌈need × 班次数 / cover⌉ 名不同的持证员工
        blocked = False
        for rule, attr, value, need in attr_rules:
            required = _ceil_div(need * n_shifts, cover)
            have = sum(1 for e in pool if index.has_attribute(e, attr, value))
            if have >= required:
                continue
            lacking.append({"day": day, "have": have, "required": required, "rule": rule,
                            "noun": attribute_noun(rule)})
            # 一天报一条就够：同一天多条资质都不够时，先解决最先命中的那条
            blocked = True
            break
        if blocked or day_need <= 0:
            continue

        capacity = len(pool) * cover
        if capacity < day_need:
            short.append({"day": day, "pool": len(pool), "need": day_need})
        elif capacity == day_need:
            tight.append({"day": day, "pool": len(pool), "need": day_need, "capacity": capacity})

    for row in lacking[:MAX_PER_CODE]:
        errors.append(_issue(
            "daily_attribute_capacity_lt_demand",
            f"{index.day_label(row['day'])}：全天仅 {row['have']} 名具备{row['noun']}的员工可排班，"
            f"但当天 {n_shifts} 个班共需 {row['required']} 名不同员工（{note}）",
            f"给更多员工补上该项、减少当天的不可用设置，或停用规则 {row['rule'].id}",
            _where(day=row["day"], rule_id=row["rule"].id),
        ))
    _truncated(errors, "daily_attribute_capacity_lt_demand", len(lacking))

    for row in short[:MAX_PER_CODE]:
        errors.append(_issue(
            "daily_capacity_lt_demand",
            f"{index.day_label(row['day'])}：全天可排班 {row['pool']} 人，"
            f"当天各班共需 {row['need']} 人次（{note}）",
            fix,
            _where(day=row["day"], rule_id=head.id if head else None),
        ))
    _truncated(errors, "daily_capacity_lt_demand", len(short))

    for row in tight[:MAX_PER_CODE]:
        # 刚好够不是错误，但这天已经没有任何容错：临时请假一个人就会无解。
        # cover > 1 时「人数」和「人次」不是同一个量，文案必须把折算写出来，否则像笔误
        headcount = (
            f"全天可排班 {row['pool']} 人"
            if row["capacity"] == row["pool"]
            else f"全天可排班 {row['pool']} 人最多承担 {row['capacity']} 人次"
        )
        warnings.append(_issue(
            "no_daily_headroom",
            f"{index.day_label(row['day'])}：{headcount}，恰好等于当天需求 "
            f"{row['need']} 人次（{note}），这天一个人都不能请假",
            "给这天多留 1～2 名可排班的员工，或降低当天的人数下限",
            _where(day=row["day"]),
        ))
    _truncated(warnings, "no_daily_headroom", len(tight))


# ---------- 工具 ----------


def _ceil_div(a: int, b: int) -> int:
    return -(-a // max(1, b))


def _dups(ids: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for i in ids:
        if i in seen and i not in out:
            out.append(i)
        seen.add(i)
    return out


def _truncated(issues: List[dict], code: str, total: int) -> None:
    """同类问题只列前几条，剩下的折成一句计数（error 与 warning 同一套口径）。

    不折的话，一个「全员周一不可用」的误操作会刷出几十条 error，把真正需要先处理的
    结构性问题挤出视野。
    """
    if total > MAX_PER_CODE:
        issues.append(_issue(
            code,
            f"另有 {total - MAX_PER_CODE} 处同类问题未逐条列出（共 {total} 处）",
            "先处理上面列出的几处，保存后会重新自检",
        ))
