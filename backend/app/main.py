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

import json
import logging
import os
import time
from typing import Any, List, Optional, Set, Tuple, Union

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from . import config as C
from . import config_check, importer, llm, models_registry, serializers, solver
from . import validator as V
from .models import Schedule, ScheduleRequest
from .scenarios import SCENARIOS

# 显式配置日志：降级路径（模型超时、限流、掉到规则解析）必须能在容器日志里看到。
# 不配的话自定义 logger 只会落到 logging 的 lastResort handler，格式与 uvicorn 不一致、
# 也没有时间戳，排查线上偶发问题时基本没用。
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

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


def _bad_request(message: str) -> JSONResponse:
    """入参本身讲不通 → 400。

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
            return _bad_request(
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
    # 收 Any 而不是 SchedulerConfig：配置来自浏览器 localStorage，可能是上一版本写下的。
    # 用类型注解硬校验的话，一个字段对不上就是 422 白屏；这里要的是一条能看懂的自检 error
    config: Optional[Any] = None


class ValidateIn(BaseModel):
    slots: List[SlotIn]
    config: Optional[Any] = None


class CandidatesIn(BaseModel):
    day: str
    shift: str
    # 已在该格里的人：换人对话框要把「已经排上的」从候选里去掉
    taken: List[str] = Field(default_factory=list)
    config: Optional[Any] = None


class ConfigIn(BaseModel):
    # 省略 config = 自检默认配置：前端首屏「用示例门店配置」时也能拿到 capacity 数字
    config: Optional[Any] = None


def _to_schedule(slots: Optional[List[SlotIn]], config: C.ConfigLike = None) -> Optional[Schedule]:
    return serializers.slots_in([s.model_dump() for s in slots], config) if slots else None


# ---------- 配置入参 ----------


def _config_issue_body(message: str, fix: str) -> dict:
    """与 /api/config/validate 同构的失败体。

    形状统一是为了让前端只写一个渲染器：配置解析失败和配置自检失败对用户是同一件事
    ——「这份配置现在用不了，原因在这里」。
    """
    return {
        "ok": False,
        "errors": [{"code": "invalid_config", "message": message, "where": None, "fix": fix}],
        "warnings": [],
        "capacity": {
            "demand_person_shifts": 0, "supply_person_shifts": 0, "headroom_pct": 0.0, "per_slot": [],
        },
    }


def _validation_error_body(exc: ValidationError) -> dict:
    # 只列前 3 条：pydantic 对一份嵌套配置能吐几十条，配置页需要的是「先改哪里」
    parts = [
        f"{'.'.join(str(x) for x in e['loc']) or 'config'}: {e['msg']}"
        for e in exc.errors()[:3]
    ]
    more = f"（另有 {len(exc.errors()) - 3} 处）" if len(exc.errors()) > 3 else ""
    return _config_issue_body(
        "配置格式不合法 —— " + "；".join(parts) + more,
        "对照 /api/config/default 返回的结构修正字段，或重新导入一份配置 JSON",
    )


def _parse_config(raw: Optional[Any]) -> Tuple[Optional[C.SchedulerConfig], Optional[dict]]:
    """把请求里的 config 解析成模型。返回 (配置, 失败体)，失败体非空时调用方必须直接返回。

    省略 config 时返回 (None, None)：None 一路传到底就是「用默认配置」，
    这条路径必须与改造前逐字节一致，所以刻意不在这里替换成 default_config()。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, _config_issue_body("config 必须是一个 JSON 对象", "改成 { \"config\": {...} } 的形式")
    try:
        return C.SchedulerConfig.model_validate(raw), None
    except ValidationError as exc:
        return None, _validation_error_body(exc)
    except ValueError as exc:
        # 班次时间这类字段在 model_validator 里主动抛 ValueError（猜一个默认值只会静默排错班）
        return None, _config_issue_body(f"配置字段取值不合法：{exc}", "检查班次的 start/end 是否为 HH:MM")


def _config_rejected(body: dict) -> JSONResponse:
    """配置不可用 → 400，不进求解。

    detail/message 两个键都给：/api/generate 的既有错误用 detail，导入链路用 message，
    前端两套读法都能拿到同一句话。
    """
    first = body["errors"][0]["message"] if body.get("errors") else "配置不可用"
    return JSONResponse(status_code=400, content={**body, "detail": first, "message": first})


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


# ---------- 配置 ----------


@app.get("/api/config/default")
async def config_default():
    """默认配置（= 现有考题数据），前端首次加载用它预填。

    每次返回深拷贝（default_config 内部保证），因为前端拿到就会改；共享同一份对象
    会让「改一个门店污染下一个请求」这种最难查的问题变成常态。
    """
    return {"config": C.default_config().model_dump()}


@app.post("/api/config/validate")
async def config_validate(body: ConfigIn):
    """配置自检。在用户点「生成」之前就回答「这份配置有没有解」。

    刻意返回 200 而不是 400：自检结果本身就是这个接口的产物，ok=false 是正常业务结论，
    前端要按 errors/warnings 渲染，而不是当成请求失败。
    """
    cfg, bad = _parse_config(body.config)
    if bad is not None:
        return bad
    return config_check.check(cfg)


# ---------- 主链路 ----------


@app.post("/api/generate")
async def generate(body: GenerateIn):
    # 白名单外的模型直接 400，并把可用清单一起给出：让用户选，而不是让用户猜
    if body.model and body.model not in models_registry.text_model_ids():
        raise HTTPException(status_code=400, detail=models_registry.reject_text_model(body.model))

    cfg, bad = _parse_config(body.config)
    if bad is not None:
        return _config_rejected(bad)
    if cfg is not None:
        # 自检只对显式传来的配置跑：默认配置恒定可解，省略 config 的老前端不该为此多付一次开销
        check = config_check.check(cfg)
        if not check["ok"]:
            return _config_rejected(check)

    t_all = time.perf_counter()
    # import_ms 在 generate 恒为 0，字段常在，前端无需对两条链路做兼容判断
    timing = {"parse_ms": 0, "solve_ms": 0, "validate_ms": 0, "explain_ms": 0, "import_ms": 0, "total_ms": 0}
    base = _to_schedule(body.base_slots, cfg)
    scenario = serializers.scenario_out(cfg)

    # L1 意图解析
    t0 = time.perf_counter()
    intent = await llm.parse_intent(body.instruction or "", model=body.model, config=cfg)
    timing["parse_ms"] = int((time.perf_counter() - t0) * 1000)

    # 无法判断：不猜，直接反问
    if intent.action == "unknown" or intent.clarification_needed:
        timing["total_ms"] = int((time.perf_counter() - t_all) * 1000)
        questions = intent.clarification_needed or ["指令无法唯一确定，请补充员工编号、日期或班次"]
        return {
            "ok": False,
            "mode": "clarify",
            "intent": serializers.intent_out(intent, cfg),
            "scenario": scenario,
            "solution": None,
            # 没有排班表就没有校验结果：见下方无解分支的注释，两条 solution=null 的
            # 路径必须给同一个答案，否则前端要为「哪种 null 才是真的没校验」写分支
            "validation": None,
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
    schedule, trace, diagnosis = solver.solve(intent, base, config=cfg)
    timing["solve_ms"] = int((time.perf_counter() - t0) * 1000)

    # 无解：给证据和解锁路径，不给假方案
    if schedule is None:
        t0 = time.perf_counter()
        text, src = await llm.explain(None, None, intent, diagnosis, model=body.model, config=cfg)
        timing["explain_ms"] = int((time.perf_counter() - t0) * 1000)
        timing["total_ms"] = int((time.perf_counter() - t_all) * 1000)
        return {
            "ok": False,
            "mode": intent.action,
            "intent": serializers.intent_out(intent, cfg),
            "scenario": scenario,
            "solution": None,
            # null，而不是一份「每条规则 passed=false、violations 为空」的空报告：
            # 那种报告是在说谎——没有排班表，就没有任何一条规则被违反过，用户看到的却是
            # 满屏红叉，还会误以为是这些规则本身不达标。无解的信息只由 infeasible 承载。
            "validation": None,
            "explanation": serializers.explanation_out(text, src, None),
            "infeasible": serializers.infeasible_out(diagnosis),
            "clarification": None,
            "diff": None,
            "timing": timing,
        }

    # L3 独立校验
    t0 = time.perf_counter()
    report = V.validate(schedule, intent, config=cfg)
    timing["validate_ms"] = int((time.perf_counter() - t0) * 1000)

    # L4 解释生成
    t0 = time.perf_counter()
    text, src = await llm.explain(schedule, report, intent, diagnosis, model=body.model, config=cfg)
    timing["explain_ms"] = int((time.perf_counter() - t0) * 1000)
    timing["total_ms"] = int((time.perf_counter() - t_all) * 1000)

    return {
        "ok": report.passed,
        "mode": intent.action,
        "intent": serializers.intent_out(intent, cfg),
        "scenario": scenario,
        "solution": {
            "found": True,
            "slots": serializers.slots_out(schedule, cfg),
            "soft_metrics": serializers.soft_metrics_out(report, schedule, cfg),
            "restarts_used": max(0, len(trace) - 1),
        },
        "validation": serializers.validation_out(report, schedule, intent, cfg),
        "explanation": serializers.explanation_out(text, src, report),
        "infeasible": None,
        "clarification": None,
        "diff": serializers.diff_out(base, schedule, len(report.violations)),
        "timing": timing,
    }


@app.post("/api/validate")
async def validate_schedule(body: ValidateIn):
    """手工微调后的实时校验：与生成路径共用同一个校验器，杜绝两套标准。"""
    cfg, bad = _parse_config(body.config)
    if bad is not None:
        return _config_rejected(bad)
    schedule = _to_schedule(body.slots, cfg) or Schedule(slots=[])
    report = V.validate(schedule, None, config=cfg)
    return {
        "validation": serializers.validation_out(report, schedule, None, cfg),
        "soft_metrics": serializers.soft_metrics_out(report, schedule, cfg),
    }


def _candidates_payload(day: str, shift: str, taken: Set[str], cfg: C.ConfigLike) -> Union[dict, JSONResponse]:
    """换人候选：员工池与过滤规则全部来自配置，与求解器共用 solver.eligible。

    day/shift 不在配置里时必须 400 而不是返回一个「过滤后为空」的名单：前端的格子可能
    是上一份配置留下的，静默返回空候选会被读成「没人能上」，而真相是这个格子已经不存在。
    """
    index = C.index_of(cfg)
    if day not in index.days:
        return _bad_request(f"day={day!r} 不在当前配置里，有效取值：{'、'.join(index.day_ids)}")
    if shift not in index.shifts:
        return _bad_request(f"shift={shift!r} 不在当前配置里，有效取值：{'、'.join(index.shift_ids)}")
    # 空意图 + 空状态：候选只受配置约束（不可用/上限/连班/休息间隔），不掺入某次求解的中间态
    ctx = solver.build_context(ScheduleRequest(), None, config=cfg)
    st = solver.State()
    pool = [e for e in solver.eligible(ctx, st, day, shift) if e not in taken]
    return {
        "day": day,
        "shift": shift,
        "candidates": [serializers.employee_out(index, e) for e in pool],
    }


@app.get("/api/candidates")
async def candidates(day: str, shift: str, taken: str = ""):
    """点击 chip 换人时的候选名单，已按班次上限/连班/休息间隔/不可用过滤。

    只服务默认配置：GET 带不了一份配置。自定义配置请用 POST /api/candidates（同样的
    响应结构，多一个可选 config），这个 GET 仅为既有调用方保留。
    """
    return _candidates_payload(day, shift, {x for x in taken.split(",") if x}, None)


@app.post("/api/candidates")
async def candidates_post(body: CandidatesIn):
    """同 GET，但候选按请求携带的 config 计算；省略 config 即默认配置。

    响应结构与 GET 完全一致，前端换成 POST 不用改解析。
    """
    cfg, bad = _parse_config(body.config)
    if bad is not None:
        return _config_rejected(bad)
    return _candidates_payload(body.day, body.shift, set(body.taken), cfg)


# ---------- 排班表导入 ----------


@app.post("/api/import")
async def import_schedule(
    file: UploadFile = File(...),
    vision_model: Optional[str] = Form(None),
    config: Optional[str] = Form(None),
):
    """导入已有排班表（CSV/TSV/Excel/图片），解析后立刻用同一个校验器体检。

    体检是这个接口的全部价值：导入本身只是把别处的排班搬进来，「有没有违规」必须由 L3
    给结论，因此任何来源解析出的 slots 都走 validator.validate()，没有旁路。

    config 走 multipart 的字符串字段（JSON 文本）：这个接口是 form-data，没法像
    /api/generate 那样收一个 JSON body。
    """
    t_all = time.perf_counter()
    cfg, bad = _form_config(config)
    if bad is not None:
        return _config_rejected(bad)

    data = await file.read()
    ext = importer.extension_of(file.filename or "")
    if err := importer.check_upload(file.filename or "", len(data)):
        return _bad_request(err)

    is_image = ext in importer.IMAGE_EXTS
    if is_image:
        # 图片必须过模型，没配 Key 时提前说清替代方案，而不是让用户上传完才失败
        if not llm.llm_enabled():
            return _bad_request("未配置 LLM，图片识别不可用，请改用 CSV/Excel 导入")
        if vision_model and vision_model not in models_registry.vision_model_ids():
            return _bad_request(models_registry.reject_vision_model(vision_model))

    t0 = time.perf_counter()
    if is_image:
        ex = await importer.parse_image(data, ext, vision_model, cfg)
    elif ext in {".xlsx", ".xls"}:
        ex = importer.parse_excel(data, cfg)
    else:
        ex = importer.parse_csv(data, cfg)
    extract_ms = int((time.perf_counter() - t0) * 1000)

    # 解析失败不抛 5xx：前端要能把「为什么读不出来」原话显示给店长
    schedule = Schedule(slots=ex.slots) if ex.slots else None
    report = None
    validate_ms = 0
    if schedule is not None:
        t0 = time.perf_counter()
        report = V.validate(schedule, None, config=cfg)
        validate_ms = int((time.perf_counter() - t0) * 1000)

    timing = {
        "extract_ms": extract_ms,
        "validate_ms": validate_ms,
        "total_ms": int((time.perf_counter() - t_all) * 1000),
    }
    return serializers.import_out(ex, report, schedule, timing, cfg)


def _form_config(raw: Optional[str]) -> Tuple[Optional[C.SchedulerConfig], Optional[dict]]:
    """解析表单/查询串里的 config（JSON 文本）。返回与 `_parse_config` 相同的二元组。

    空串按「没传」处理：表单里的空字段是常态（前端只要挂上这个 input 就会带一个空串），
    把它当成一份空配置会静默把用户的门店换成 0 天 0 班。
    JSON 解析失败必须显式报错——绝不能退化成「忽略未知字段后的空配置」。
    """
    if raw is None or not raw.strip():
        return None, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, _config_issue_body(
            f"config 字段不是合法 JSON：{exc}", "把配置对象序列化后再放进 config 字段"
        )
    return _parse_config(parsed)


@app.get("/api/import/template")
async def import_template(fmt: str = "csv", config: Optional[str] = None):
    """导入模板下载。存在的唯一目的是把导入失败率压下去。"""
    if fmt.lower() != "csv":
        return _bad_request(f"暂不支持 {fmt} 模板，可用：csv")
    cfg, bad = _form_config(config)
    if bad is not None:
        return _config_rejected(bad)
    return Response(
        content=importer.template_csv(cfg),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="schedule_template.csv"'},
    )
