"""API 表现层：把内部模型映射成前端契约。

内部模型（models.py）服务于求解与校验的正确性，前端契约服务于界面表达，两者刻意分开，
中间只在这一个文件里做转换，避免界面需求污染规则内核。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import repair
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

# ---------- meta ----------


def meta_payload() -> dict:
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


# ---------- slots ----------


def slots_out(schedule: Schedule) -> List[dict]:
    return [
        {
            "day": s.day,
            "day_label": DAY_LABELS.get(s.day, s.day),
            "shift": s.shift,
            "shift_time": SHIFT_TIME.get(s.shift, ""),
            "min_required": min_required(s.day),
            "employees": list(s.employee_ids),
        }
        for s in schedule.slots
    ]


def slots_in(raw: Optional[List[dict]]) -> Optional[Schedule]:
    if not raw:
        return None
    slots: List[Slot] = []
    for item in raw:
        day = str(item.get("day", ""))
        shift = str(item.get("shift", ""))
        if day not in DAYS or shift not in SHIFTS:
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


def redundancy_of(schedule: Schedule) -> float:
    """平均每班的技能冗余人数：值守超出 1 名 + 饮品超出 2 名 + 收银超出 1 名。"""
    from .data import SKILL_CASHIER, SKILL_DRINK, SKILL_KEEPER, has_skill

    if not schedule.slots:
        return 0.0
    total = 0
    for s in schedule.slots:
        ids = [e for e in dict.fromkeys(s.employee_ids) if e in EMPLOYEES]
        total += max(0, sum(1 for e in ids if has_skill(e, SKILL_KEEPER)) - 1)
        total += max(0, sum(1 for e in ids if has_skill(e, SKILL_DRINK)) - 2)
        total += max(0, sum(1 for e in ids if has_skill(e, SKILL_CASHIER)) - 1)
    return total / len(schedule.slots)


def soft_metrics_out(report: ValidationReport, schedule: Optional[Schedule]) -> dict:
    out = soft_out(report)
    # 归一化到 0–1：平均每班 8 人次冗余记满分，便于前端直接画进度条
    out["skill_redundancy"] = round(min(1.0, redundancy_of(schedule) / 8), 3) if schedule else 0.0
    return out


# ---------- 校验报告 ----------


def validation_out(
    report: ValidationReport, schedule: Optional[Schedule], intent: Optional[ScheduleRequest]
) -> dict:
    fixes = repair.suggest(schedule, report, intent) if schedule else {}
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


def empty_validation() -> dict:
    return {
        "passed": False,
        "violation_count": 0,
        "rules": [{"id": r["id"], "text": r["text"], "passed": False, "violations": []} for r in RULES],
    }


# ---------- 意图回显 ----------

_OBJECTIVE_DEFAULT = "满足 9 条硬规则，兼顾班次偏好与工时均衡"


def intent_out(intent: ScheduleRequest) -> dict:
    chips: List[dict] = []
    for t in intent.temp_leaves:
        chips.append({
            "key": f"leave:{t.employee_id}",
            "label": "临时请假",
            "value": f"{t.employee_id}｜周{'、周'.join(t.days)}",
            "editable": True,
        })
    for p in intent.pins:
        chips.append({
            "key": f"pin:{p.employee_id}:{p.day}:{p.shift}",
            "label": "指定在岗",
            "value": f"{p.employee_id}｜周{p.day}{p.shift}",
            "editable": True,
        })
    for f in intent.forbids:
        chips.append({
            "key": f"forbid:{f.employee_id}:{f.day}",
            "label": "禁止排班",
            "value": f"{f.employee_id}｜周{f.day}{f.shift or '全天'}",
            "editable": True,
        })
    for e in intent.exclude_employees:
        chips.append({"key": f"exclude:{e}", "label": "整周不可用", "value": e, "editable": True})
    for k, v in intent.min_staff_override.items():
        label = k if k == "all" else ("周" + k.replace("|", " "))
        chips.append({"key": f"min:{k}", "label": "人数上调", "value": f"{label} ≥ {v} 人", "editable": True})
    if not chips:
        chips.append({"key": "base", "label": "约束", "value": "仅 9 条硬规则，无额外约束", "editable": False})

    # 只有「模型不可用被迫降级」才算 degraded；空指令直接生成基础排班不是降级
    degraded = intent.parse_source == "fallback_rule"
    reason = "语言模型暂不可用，已降级为规则解析，仅识别高置信约束" if degraded else None

    return {
        "operation": intent.action,
        "period_label": "本周 · 周一至周日 × 早班/晚班",
        "objective_label": intent.notes[0] if intent.notes else _OBJECTIVE_DEFAULT,
        "chips": chips,
        "temp_constraints": [
            {"employee_id": t.employee_id, "days": t.days, "type": "leave", "raw": t.reason or "临时请假"}
            for t in intent.temp_leaves
        ],
        "degraded": degraded,
        "degrade_reason": reason,
        "model_used": intent.model_used,
        "guardrail": guardrail_out(intent.guardrail),
    }


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
    a, b = base.as_map(), new.as_map()
    changed: List[dict] = []
    for s in new.slots:
        k = f"{s.day}|{s.shift}"
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

_EXPECTED_SLOTS = len(DAYS) * len(SHIFTS)

# 解析失败时软指标一律给 0，而不是套用空排班算出来的值：空排班的班次标准差是 0，
# 均衡度会算成满分 1.0，等于给一份读不出内容的表打了个漂亮分数
_ZERO_SOFT = {"preference_rate": 0.0, "balance_score": 0.0, "skill_redundancy": 0.0}


def import_out(
    ex: ImportExtraction,
    report: Optional[ValidationReport],
    schedule: Optional[Schedule],
    timing: Dict[str, int],
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
            "slots_expected": _EXPECTED_SLOTS,
            "assignments": ex.assignments,
            "resolved": ex.resolved,
            "unresolved": len(ex.unresolved),
        },
        "unresolved": [u.model_dump() for u in ex.unresolved],
        "warnings": ex.warnings,
        "confidence": ex.confidence,
        "requires_confirmation": ex.requires_confirmation,
        # 与 /api/validate 完全同构：导入的体检结论必须和手工微调用同一套渲染与同一套标准
        "validation": validation_out(report, schedule, None) if report else empty_validation(),
        "soft_metrics": soft_metrics_out(report, schedule) if report else dict(_ZERO_SOFT),
        "timing_ms": timing,
    }
