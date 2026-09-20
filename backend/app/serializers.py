"""API 表现层：把内部模型映射成前端契约。

内部模型（models.py）服务于求解与校验的正确性，前端契约服务于界面表达，两者刻意分开，
中间只在这一个文件里做转换，避免界面需求污染规则内核。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import repair
from . import rules as R
from .config import RULE_REQUIRE_ATTRIBUTE, ConfigIndex, ConfigLike, index_of, ordered_skills, slot_key
from .data import (
    DAY_LABELS,
    DAYS,
    EMPLOYEES,
    RULES,
    SHIFT_TIME,
    SHIFTS,
    WEEKEND,
    min_required,
)
from .models import (
    Diagnosis,
    GuardrailStats,
    ImportExtraction,
    Schedule,
    ScheduleRequest,
    Slot,
    ValidationReport,
)

# ---------- meta / scenario ----------


def meta_payload() -> dict:
    """默认配置的员工与规则。

    刻意仍读 data.py 而不是配置索引：契约 5.6 要求这个接口保持原样，它是老前端的启动依赖，
    而「当前生效的配置」由 /api/config/default 与请求体里的 config 表达。
    """
    return {
        "employees": [
            {
                "id": e.id,
                "role": e.role,
                "skills": e.to_dict()["skills"],
                "available_days": e.available_days,
                "leave_days": e.leave_days,
                "preference": e.preference or "",
            }
            for e in EMPLOYEES.values()
        ],
        "rules": [{"id": r["id"], "text": r["text"]} for r in RULES],
        "days": [
            {"key": d, "label": DAY_LABELS[d], "is_weekend": d in WEEKEND, "min_required": min_required(d)}
            for d in DAYS
        ],
        "shifts": [{"key": s, "label": s, "time": SHIFT_TIME[s]} for s in SHIFTS],
    }


def scenario_out(config: ConfigLike = None) -> dict:
    """维度回显。

    看板必须按它渲染，而不是自己按「周末=6 人、两班制」推：一旦前端自己推，
    配置化就只做了一半——后端换成三班制，界面还画两列。
    """
    index = index_of(config)
    return {
        "name": index.config.scenario.name,
        "days": [
            {"id": d, "label": index.day_label(d), "peak": index.is_peak(d)} for d in index.day_ids
        ],
        "shifts": [
            {"id": s, "name": index.shift_name(s), "time_label": index.shift_time_label(s)}
            for s in index.shift_ids
        ],
    }


# ---------- 员工 ----------


def employee_out(index: ConfigIndex, eid: str) -> dict:
    """单个员工的对外形状：EmployeeDef 字段 + 老前端读的三个派生字段。

    只有一种员工形状是硬要求：/api/candidates 与 /api/config/default 会被同一个界面
    先后调用，如果一个给 unavailable、另一个给 available_days，前端就得写两套解析。
    这里给并集——EmployeeDef 原字段用于新界面，available_days/leave_days/preference
    是从 unavailable 与 preferred_shifts 折算出来的兼容视图，不是第二份真相。
    """
    e = index.all_employees.get(eid)
    if e is None:
        # 停用或已从配置里删掉的人仍可能出现在历史排班里，返回 id 也比抛异常有用
        return {
            "id": eid, "name": eid, "role": "", "skills": [], "unavailable": [],
            "max_shifts": None, "preferred_shifts": [], "active": False,
            "available_days": [], "leave_days": [], "preference": None,
        }
    leave_days: List[str] = []
    for d in index.day_ids:
        hit = index.blocked_by(eid, d, None)
        # 有具名原因（请假）才算 leave；无原因的整日不可用是「本来就不上班」
        if hit is not None and hit.reason:
            leave_days.append(d)
    return {
        "id": e.id,
        "name": e.name,
        "role": e.role,
        "skills": ordered_skills(index, e.skills),
        "unavailable": [u.model_dump() for u in e.unavailable],
        "max_shifts": e.max_shifts,
        "preferred_shifts": list(e.preferred_shifts),
        "active": e.active,
        # 兼容视图：roster_days 正是旧 data.Employee.available_days 的语义（结构性可工作日）
        "available_days": index.roster_days(eid),
        "leave_days": leave_days,
        "preference": e.preferred_shifts[0] if e.preferred_shifts else None,
    }


# ---------- slots ----------


def slots_out(schedule: Schedule, config: ConfigLike = None) -> List[dict]:
    index = index_of(config)
    return [
        {
            "day": s.day,
            "day_label": index.day_label(s.day),
            "shift": s.shift,
            "shift_time": index.shift_time_label(s.shift),
            # 来自配置的逐格下限：看板据此显示「4/6」这类提示，不再按「周末=6」自己算
            "min_required": index.min_required(s.day, s.shift),
            "employees": list(s.employee_ids),
        }
        for s in schedule.slots
    ]


def slots_in(raw: Optional[List[dict]], config: ConfigLike = None) -> Optional[Schedule]:
    if not raw:
        return None
    index = index_of(config)
    slots: List[Slot] = []
    for item in raw:
        day = str(item.get("day", ""))
        shift = str(item.get("shift", ""))
        # 丢弃当前场景之外的格子：拿旧配置的排班去校验新配置，只会得到一份看不懂的报告
        if day not in index.days or shift not in index.shifts:
            continue
        emps = [e for e in (item.get("employees") or []) if isinstance(e, str)]
        slots.append(Slot(day=day, shift=shift, employee_ids=emps))
    return Schedule(slots=slots) if slots else None


# ---------- 软指标 ----------


def soft_out(report: ValidationReport) -> dict:
    m = report.soft_metrics
    # 均衡度：把班次数标准差映射到 0–1，标准差 0 记 1 分，≥1.5 记 0 分
    balance = max(0.0, min(1.0, 1 - m.workload_stdev / 1.5))
    return {
        "preference_rate": round(m.preference_hit_rate, 4),
        "balance_score": round(balance, 4),
        "skill_redundancy": 0.0,
    }


def redundancy_of(schedule: Schedule, config: ConfigLike = None) -> float:
    """平均每班的技能冗余人次：每条 require_attribute 规则超出其 min 的部分之和。

    按规则算而不是按「值守/饮品/收银」三个写死的技能：默认配置下结果与旧实现逐字相同
    （1 名值守 + 2 名饮品 + 1 名收银），换成别的资质组合也不必改这里。
    """
    index = index_of(config)
    if not schedule.slots:
        return 0.0
    rules = index.rules_of(RULE_REQUIRE_ATTRIBUTE)
    total = 0
    for s in schedule.slots:
        ids = [e for e in dict.fromkeys(s.employee_ids) if index.known(e)]
        for rule in rules:
            need = int((rule.params or {}).get("min") or 0)
            if need <= 0:
                continue
            total += max(0, R.attribute_counts(index, ids, rule) - need)
    return total / len(schedule.slots)


def _redundancy_scale(config: ConfigLike = None) -> float:
    """技能冗余度归一化的分母：资质需求总量的 2 倍记满分。

    默认配置下 (1+2+1)×2 = 8，与旧实现写死的 8 完全一致；配置变了分母跟着变，
    所以指标恒在 0–1，不会像固定分母那样在三班制门店画出 240% 的进度条。
    """
    index = index_of(config)
    need = sum(
        int((r.params or {}).get("min") or 0) for r in index.rules_of(RULE_REQUIRE_ATTRIBUTE)
    )
    return float(max(1, need * 2))


def soft_metrics_out(
    report: ValidationReport, schedule: Optional[Schedule], config: ConfigLike = None
) -> dict:
    out = soft_out(report)
    out["skill_redundancy"] = (
        round(min(1.0, redundancy_of(schedule, config) / _redundancy_scale(config)), 3)
        if schedule else 0.0
    )
    return out


# ---------- 校验报告 ----------


def validation_out(
    report: ValidationReport,
    schedule: Optional[Schedule],
    intent: Optional[ScheduleRequest],
    config: ConfigLike = None,
) -> dict:
    fixes = repair.suggest(schedule, report, intent, config=config) if schedule else {}
    rules: List[dict] = []
    for r in report.rule_results:
        vs = []
        for v in report.violations:
            if v.rule_id != r.rule_id:
                continue
            key = (v.rule_id, v.day or "", v.shift or "", v.employee_id or "")
            vs.append({
                "day": v.day or "",
                "shift": v.shift or "",
                "employees": [v.employee_id] if v.employee_id else [],
                "message": v.detail,
                "suggestions": fixes.get(key, []),
            })
        rules.append({"id": r.rule_id, "text": r.rule_text, "passed": r.passed, "violations": vs})
    return {"passed": report.passed, "violation_count": len(report.violations), "rules": rules}


# ---------- 意图回显 ----------


def _objective_default(config: ConfigLike = None) -> str:
    return f"满足 {len(R.rule_ids_in_order(index_of(config)))} 条硬规则，兼顾班次偏好与工时均衡"


def _period_label(config: ConfigLike = None) -> str:
    index = index_of(config)
    if not index.day_ids or not index.shift_ids:
        return index.config.scenario.name
    span = index.day_label(index.day_ids[0])
    if len(index.day_ids) > 1:
        span += f"至{index.day_label(index.day_ids[-1])}"
    return f"本周 · {span} × {'/'.join(index.shift_name(s) for s in index.shift_ids)}"


def intent_out(intent: ScheduleRequest, config: ConfigLike = None) -> dict:
    index = index_of(config)
    chips: List[dict] = []
    for t in intent.temp_leaves:
        chips.append({
            "key": f"leave:{t.employee_id}",
            "label": "临时请假",
            "value": f"{t.employee_id}｜{'、'.join(index.day_label(d) for d in t.days)}",
            "editable": True,
        })
    for p in intent.pins:
        chips.append({
            "key": f"pin:{p.employee_id}:{p.day}:{p.shift}",
            "label": "指定在岗",
            "value": f"{p.employee_id}｜{index.slot_label(p.day, p.shift)}",
            "editable": True,
        })
    for f in intent.forbids:
        chips.append({
            "key": f"forbid:{f.employee_id}:{f.day}",
            "label": "禁止排班",
            "value": f"{f.employee_id}｜{index.day_label(f.day)}{index.shift_name(f.shift) if f.shift else '全天'}",
            "editable": True,
        })
    for e in intent.exclude_employees:
        chips.append({"key": f"exclude:{e}", "label": "整周不可用", "value": e, "editable": True})
    for k, v in intent.min_staff_override.items():
        label = _override_label(index, k)
        chips.append({"key": f"min:{k}", "label": "人数上调", "value": f"{label} ≥ {v} 人", "editable": True})
    if not chips:
        chips.append({
            "key": "base",
            "label": "约束",
            "value": f"仅 {len(R.rule_ids_in_order(index))} 条硬规则，无额外约束",
            "editable": False,
        })

    # 只有「模型不可用被迫降级」才算 degraded；空指令直接生成基础排班不是降级
    degraded = intent.parse_source == "fallback_rule"
    reason = "语言模型暂不可用，已降级为规则解析，仅识别高置信约束" if degraded else None
    # 换模型不算 degraded（解析仍由 LLM 完成），但必须单独告知：用户选的模型没生效
    if intent.model_fallback_from:
        reason = (
            f"{intent.model_fallback_from} 本次超时，已自动改用 {intent.model_used} 完成解析"
        )

    return {
        "operation": intent.action,
        "period_label": _period_label(index),
        "objective_label": intent.notes[0] if intent.notes else _objective_default(index),
        "chips": chips,
        "temp_constraints": [
            {"employee_id": t.employee_id, "days": t.days, "type": "leave", "raw": t.reason or "临时请假"}
            for t in intent.temp_leaves
        ],
        "degraded": degraded,
        "degrade_reason": reason,
        "model_used": intent.model_used,
        "model_fallback_from": intent.model_fallback_from,
        "guardrail": guardrail_out(intent.guardrail),
    }


def _override_label(index, key: str) -> str:
    """人数上调 chip 的显示文案。

    key 有三种形态（all / day / day|shift），三者都要显示成店长能对上号的中文，
    直接把 id 拼出来在自定义 label 的场景下会变成「d6 s1 ≥ 7 人」。
    """
    if key == "all":
        return "all"
    if "|" in key:
        day, shift = key.split("|", 1)
        return f"{index.day_label(day)} {index.shift_name(shift)}"
    return index.day_label(key)


def guardrail_out(g: GuardrailStats) -> dict:
    """护栏统计的对外形态。

    内部还有一个 dropped_leaves 计数，这里不外泄：契约没有这一位，而幻觉请假极罕见，
    多给一个字段只会让前端多一条不知道该不该显示的分支——它已经并进 summary 了。
    """
    return {
        "triggered": g.triggered,
        "dropped_pins": g.dropped_pins,
        "dropped_forbids": g.dropped_forbids,
        "dropped_excludes": g.dropped_excludes,
        "dropped_min_staff": g.dropped_min_staff,
        "invalid_employee_ids": list(g.invalid_employee_ids),
        "summary": g.summary,
    }


# ---------- 解释 ----------


def explanation_out(text: str, source: str, report: Optional[ValidationReport]) -> dict:
    bullets = [ln.strip().lstrip("-•* ").strip() for ln in (text or "").splitlines()]
    bullets = [b for b in bullets if b]
    unmet: List[str] = []
    if report:
        m = report.soft_metrics
        if m.preference_total and m.preference_hit < m.preference_total:
            unmet.append(f"班次偏好未满足 {m.preference_total - m.preference_hit} 人次（软约束，不影响合规）")
        if m.workload_max - m.workload_min >= 3:
            unmet.append(f"工时差异较大：最多 {m.workload_max} 个班，最少 {m.workload_min} 个班")
    return {"bullets": bullets, "unmet_preferences": unmet, "degraded": source != "llm"}


# ---------- 无解 ----------


def infeasible_out(d: Diagnosis) -> Optional[dict]:
    if not d.infeasible:
        return None
    summary = d.evidence[0] if d.evidence else "当前约束下不存在满足全部硬规则的排班"
    if d.kind == "search_timeout":
        summary = "在给定时间内未能找到可行解（未证明无解），建议放宽一项约束后重试：" + summary
    return {
        "proven": d.kind == "proven_infeasible",
        "summary": summary,
        "min_conflict_set": ([d.bottleneck_rule] if d.bottleneck_rule else []) + d.evidence[:3],
        "conflict_slot": d.bottleneck_slots[0] if d.bottleneck_slots else "",
        "unlock_paths": [
            {
                "title": o.title,
                "detail": o.detail,
                "extra_cost": o.cost,
                "needs_approval": ("审批" in o.cost or "确认" in o.cost or "合规" in o.cost),
            }
            for o in d.unlock_options
        ],
    }


# ---------- diff ----------


def diff_out(base: Optional[Schedule], new: Optional[Schedule], new_violations: int) -> Optional[dict]:
    if base is None or new is None:
        return None
    a = base.as_map()
    changed: List[dict] = []
    for s in new.slots:
        k = slot_key(s.day, s.shift)
        before, after = set(a.get(k, [])), set(s.employee_ids)
        if before != after:
            changed.append({
                "day": s.day,
                "shift": s.shift,
                "added": sorted(after - before),
                "removed": sorted(before - after),
            })
    return {"changed_count": len(changed), "changed_slots": changed, "new_violations": new_violations}


# ---------- 导入 ----------

# 解析失败时软指标一律给 0，而不是套用空排班算出来的值：空排班的班次标准差是 0，
# 均衡度会算成满分 1.0，等于给一份读不出内容的表打了个漂亮分数
_ZERO_SOFT = {"preference_rate": 0.0, "balance_score": 0.0, "skill_redundancy": 0.0}


def import_out(
    ex: ImportExtraction,
    report: Optional[ValidationReport],
    schedule: Optional[Schedule],
    timing: Dict[str, int],
    config: ConfigLike = None,
) -> dict:
    return {
        "ok": ex.ok,
        "source": ex.source,
        "extractor": ex.extractor,
        "model_used": ex.model_used,
        "layout": ex.layout,
        "slots": [
            {"day": s.day, "shift": s.shift, "employees": list(s.employee_ids)} for s in ex.slots
        ],
        "stats": {
            "slots_found": len(ex.slots),
            # 期望格数由配置推导：写死 14 的话，三班制门店导入一张完整表也会被判「缺失班次」
            "slots_expected": index_of(config).slot_count(),
            "assignments": ex.assignments,
            "resolved": ex.resolved,
            "unresolved": len(ex.unresolved),
        },
        "unresolved": [u.model_dump() for u in ex.unresolved],
        "warnings": ex.warnings,
        "confidence": ex.confidence,
        "requires_confirmation": ex.requires_confirmation,
        # 解析出内容时与 /api/validate 完全同构：导入的体检结论必须和手工微调用同一套渲染
        # 与同一套标准。解析失败时给 null，绝不给「每条规则 passed=false」的空骨架——
        # 那是在报告一个没发生过的失败，前端会把规则清单画满红叉，用户以为问题出在规则上，
        # 而真正的问题是文件没读懂。失败原因由 ok / warnings / unresolved 承载。
        "validation": validation_out(report, schedule, None, config) if report else None,
        "soft_metrics": soft_metrics_out(report, schedule, config) if report else dict(_ZERO_SOFT),
        "timing_ms": timing,
    }
