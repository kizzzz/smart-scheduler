"""FastAPI 应用：智能排班助手后端。

分层严格对应规划文档：
  L1 llm.parse_intent      自然语言 → ScheduleRequest（失败降级为规则解析）
  L2 solver.solve          确定性回溯求解 + 前向检查 + 局部优化
  L3 validator.validate    唯一真相源，生成/手工微调共用同一套校验
  L4 llm.explain           只基于已校验事实生成解释，且被确定性规则清洗

对外接口只输出前端契约（serializers.py），内部模型不外泄。
"""
from __future__ import annotations

import os
import time
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import llm, serializers, solver
from . import validator as V
from .models import Diagnosis, Schedule, ScheduleRequest
from .scenarios import SCENARIOS

app = FastAPI(
    title="智能排班助手 API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 出入参（前端契约） ----------


class SlotIn(BaseModel):
    day: str
    shift: str
    employees: List[str] = Field(default_factory=list)


class GenerateIn(BaseModel):
    instruction: str = ""
    base_slots: Optional[List[SlotIn]] = None


class ValidateIn(BaseModel):
    slots: List[SlotIn]


def _to_schedule(slots: Optional[List[SlotIn]]) -> Optional[Schedule]:
    return serializers.slots_in([s.model_dump() for s in slots]) if slots else None


# ---------- 基础接口 ----------


@app.get("/api/health")
async def health(deep: bool = False):
    out = {"ok": True, "llm_configured": llm.llm_enabled(), "model": llm.GLM_MODEL}
    if deep:
        out["llm"] = await llm.health()
    return out


@app.get("/api/meta")
async def meta():
    """员工档案、9 条规则、班次定义。前端所有技能标签都以此为准（R-09）。"""
    return serializers.meta_payload()


@app.get("/api/scenarios")
async def scenarios():
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "description": s["note"],
            "instruction": s["text"],
            "base_required": s["id"] in {"temp_leave", "pin"},
        }
        for s in SCENARIOS
    ]


# ---------- 主链路 ----------


@app.post("/api/generate")
async def generate(body: GenerateIn):
    t_all = time.perf_counter()
    timing = {"parse_ms": 0, "solve_ms": 0, "validate_ms": 0, "explain_ms": 0, "total_ms": 0}
    base = _to_schedule(body.base_slots)

    # L1 意图解析
    t0 = time.perf_counter()
    intent = await llm.parse_intent(body.instruction or "")
    timing["parse_ms"] = int((time.perf_counter() - t0) * 1000)

    # 无法判断：不猜，直接反问
    if intent.action == "unknown" or intent.clarification_needed:
        timing["total_ms"] = int((time.perf_counter() - t_all) * 1000)
        questions = intent.clarification_needed or ["指令无法唯一确定，请补充员工编号、日期或班次"]
        return {
            "ok": False,
            "mode": "clarify",
            "intent": serializers.intent_out(intent),
            "solution": None,
            "validation": serializers.empty_validation(),
            "explanation": {
                "bullets": ["这条指令还不能唯一确定，先确认以下问题再排，避免排错班"] + questions,
                "unmet_preferences": [],
                "degraded": intent.parse_source != "llm",
            },
            "infeasible": None,
            "clarification": {"questions": questions, "raw": intent.raw_text},
            "diff": None,
            "timing": timing,
        }

    # L2 确定性求解
    t0 = time.perf_counter()
    schedule, trace, diagnosis = solver.solve(intent, base)
    timing["solve_ms"] = int((time.perf_counter() - t0) * 1000)

    # 无解：给证据和解锁路径，不给假方案
    if schedule is None:
        t0 = time.perf_counter()
        text, src = await llm.explain(None, None, intent, diagnosis)
        timing["explain_ms"] = int((time.perf_counter() - t0) * 1000)
        timing["total_ms"] = int((time.perf_counter() - t_all) * 1000)
        return {
            "ok": False,
            "mode": intent.action,
            "intent": serializers.intent_out(intent),
            "solution": None,
            "validation": serializers.empty_validation(),
            "explanation": serializers.explanation_out(text, src, None),
            "infeasible": serializers.infeasible_out(diagnosis),
            "clarification": None,
            "diff": None,
            "timing": timing,
        }

    # L3 独立校验
    t0 = time.perf_counter()
    report = V.validate(schedule, intent)
    timing["validate_ms"] = int((time.perf_counter() - t0) * 1000)

    # L4 解释生成
    t0 = time.perf_counter()
    text, src = await llm.explain(schedule, report, intent, diagnosis)
    timing["explain_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = int((time.perf_counter() - t_all) * 1000)

    return {
        "ok": report.passed,
        "mode": intent.action,
        "intent": serializers.intent_out(intent),
        "solution": {
            "found": True,
            "slots": serializers.slots_out(schedule),
            "soft_metrics": serializers.soft_metrics_out(report, schedule),
            "restarts_used": max(0, len(trace) - 1),
        },
        "validation": serializers.validation_out(report, schedule, intent),
        "explanation": serializers.explanation_out(text, src, report),
        "infeasible": None,
        "clarification": None,
        "diff": serializers.diff_out(base, schedule, len(report.violations)),
        "timing": timing,
    }


@app.post("/api/validate")
async def validate_schedule(body: ValidateIn):
    """手工微调后的实时校验：与生成路径共用同一个校验器，杜绝两套标准。"""
    schedule = _to_schedule(body.slots) or Schedule(slots=[])
    report = V.validate(schedule, None)
    return {
        "validation": serializers.validation_out(report, schedule, None),
        "soft_metrics": serializers.soft_metrics_out(report, schedule),
    }


@app.get("/api/candidates")
async def candidates(day: str, shift: str, taken: str = ""):
    """点击 chip 换人时的候选名单，已按 R-05/R-06/R-07/R-08 过滤。"""
    ctx = solver.build_context(ScheduleRequest(), None)
    st = solver.State()
    used = {x for x in taken.split(",") if x}
    pool = [e for e in solver.eligible(ctx, st, day, shift) if e not in used]
    from .data import EMPLOYEES

    return {"day": day, "shift": shift, "candidates": [EMPLOYEES[e].to_dict() for e in pool]}
