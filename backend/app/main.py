"""FastAPI 应用：智能排班助手后端。

分层严格对应规划文档：
  L1 llm.parse_intent      自然语言 → ScheduleRequest（失败降级为规则解析）
  L2 solver.solve          确定性回溯求解 + 前向检查 + 局部优化
  L3 validator.validate    唯一真相源，生成/手工微调共用同一套校验
  L4 llm.explain           只基于已校验事实生成解释，且被确定性规则清洗

导入链路（/api/import）复用 L3：CSV/Excel 走确定性解析、图片走视觉模型，两者产出的
排班表都必须过同一个 validator.validate()，没有旁路。

对外接口只输出前端契约（serializers.py），内部模型不外泄。
"""
from __future__ import annotations

import os
import time
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import importer, llm, models_registry, serializers, solver
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


def _import_error(message: str) -> JSONResponse:
    """导入的入参错误统一 400。

    同时给 detail 与 message：契约在 /api/generate 用的是 FastAPI 默认的 detail，
    在导入章节写的是 message，两处措辞不一致，与其单方面改契约，不如两个键都给。
    """
    return JSONResponse(status_code=400, content={"ok": False, "detail": message, "message": message})


@app.middleware("http")
async def reject_oversized_upload(request, call_next):
    """在解析 multipart 之前按 Content-Length 拦掉超大上传。

    放在中间件里而不是只在接口里判长度：FastAPI 会先把整个请求体缓冲下来才进函数，
    等到那时再返回 400，5 MB 上限已经形同虚设。
    """
    if request.url.path == "/api/import":
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > importer.MAX_UPLOAD_BYTES + 4096:
            return _import_error(
                f"上传内容 {int(declared) / 1024 / 1024:.2f} MB 超过 "
                f"{importer.MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限，请压缩或改用 CSV 导入"
            )
    return await call_next(request)


# ---------- 出入参（前端契约） ----------


class SlotIn(BaseModel):
    day: str
    shift: str
    employees: List[str] = Field(default_factory=list)


class GenerateIn(BaseModel):
    instruction: str = ""
    base_slots: Optional[List[SlotIn]] = None
    # 省略即用注册表默认文本模型，老前端不传也照常工作
    model: Optional[str] = None


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


@app.get("/api/models")
async def models():
    """可选模型清单。含 boundary_note——前端必须展示，防止用户以为换模型能改善排班合规性。"""
    return models_registry.payload()


# ---------- 主链路 ----------


@app.post("/api/generate")
async def generate(body: GenerateIn):
    # 白名单外的模型直接 400，并把可用清单一起给出：让用户选，而不是让用户猜
    if body.model and body.model not in models_registry.text_model_ids():
        raise HTTPException(status_code=400, detail=models_registry.reject_text_model(body.model))

    t_all = time.perf_counter()
    # import_ms 在 generate 恒为 0，字段常在，前端无需对两条链路做兼容判断
    timing = {"parse_ms": 0, "solve_ms": 0, "validate_ms": 0, "explain_ms": 0, "import_ms": 0, "total_ms": 0}
    base = _to_schedule(body.base_slots)

    # L1 意图解析
    t0 = time.perf_counter()
    intent = await llm.parse_intent(body.instruction or "", model=body.model)
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
        text, src = await llm.explain(None, None, intent, diagnosis, model=body.model)
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
    text, src = await llm.explain(schedule, report, intent, diagnosis, model=body.model)
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


# ---------- 排班表导入 ----------


@app.post("/api/import")
async def import_schedule(
    file: UploadFile = File(...),
    vision_model: Optional[str] = Form(None),
):
    """导入已有排班表（CSV/TSV/Excel/图片），解析后立刻用同一个校验器体检。

    体检是这个接口的全部价值：导入本身只是把别处的排班搬进来，「有没有违规」必须由 L3
    给结论，因此任何来源解析出的 slots 都走 validator.validate()，没有旁路。
    """
    t_all = time.perf_counter()
    data = await file.read()
    ext = importer.extension_of(file.filename or "")
    if err := importer.check_upload(file.filename or "", len(data)):
        return _import_error(err)

    is_image = ext in importer.IMAGE_EXTS
    if is_image:
        # 图片必须过模型，没配 Key 时提前说清替代方案，而不是让用户上传完才失败
        if not llm.llm_enabled():
            return _import_error("未配置 LLM，图片识别不可用，请改用 CSV/Excel 导入")
        if vision_model and vision_model not in models_registry.vision_model_ids():
            return _import_error(models_registry.reject_vision_model(vision_model))

    t0 = time.perf_counter()
    if is_image:
        ex = await importer.parse_image(data, ext, vision_model)
    elif ext in {".xlsx", ".xls"}:
        ex = importer.parse_excel(data)
    else:
        ex = importer.parse_csv(data)
    extract_ms = int((time.perf_counter() - t0) * 1000)

    # 解析失败不抛 5xx：前端要能把「为什么读不出来」原话显示给店长
    schedule = Schedule(slots=ex.slots) if ex.slots else None
    report = None
    validate_ms = 0
    if schedule is not None:
        t0 = time.perf_counter()
        report = V.validate(schedule, None)
        validate_ms = int((time.perf_counter() - t0) * 1000)

    timing = {
        "extract_ms": extract_ms,
        "validate_ms": validate_ms,
        "total_ms": int((time.perf_counter() - t_all) * 1000),
    }
    return serializers.import_out(ex, report, schedule, timing)


@app.get("/api/import/template")
async def import_template(fmt: str = "csv"):
    """导入模板下载。存在的唯一目的是把导入失败率压下去。"""
    if fmt.lower() != "csv":
        return _import_error(f"暂不支持 {fmt} 模板，可用：csv")
    return Response(
        content=importer.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="schedule_template.csv"'},
    )
