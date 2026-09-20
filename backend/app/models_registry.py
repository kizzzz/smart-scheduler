"""可选模型白名单：清单来自 2026-09-18 对生产 Key 的逐个实测，不是文档抄录。

几条实测结论直接决定了这里的数据结构：
- `glm-z1-flash` 三次探测两次 429，可用性不达标，因此只进 `locked`——让用户知道
  它存在、也知道为什么不能选，比从清单里悄悄抹掉更诚实；
- 全部付费模型返回 `1113 余额不足`，同样只进 `locked`，靠 `GLM_EXTRA_MODELS`
  在充值后免改代码放开；
- `glm-4v-flash` 的 `max_tokens` 实测硬上限是 1024（超了返回 `1210 max_tokens参数非法`），
  所以 `max_output_tokens` 是调用方必须夹取的真实约束，不是展示用字段。

`measured` 里刻意区分两个耗时，因为它们差得很远、而用户是照着数字做选择的：
- `avg_latency_s`：单次模型调用的探测均值；
- `e2e_latency_s`：线上 `/api/generate` 一整次请求的实测耗时。一次生成要跑意图解析和
  解释生成两趟 LLM，所以整体耗时接近单次的两倍——`glm-4.5-flash` 单次约 30s，
  整体三次实测 43s / 52s / 85s，波动大，登记值取 60s 作为中间量级。
  展示给用户的 `latency_hint` 用整体耗时区间，不用单次耗时：用户等的是整个请求。
  这个值同时驱动边缘超时预算（deploy/Caddyfile 的 read_timeout）与前端进度条节奏，
  改它之前先确认这两处还够用。

模块名刻意叫 models_registry 而非 models：`models.py` 是 Pydantic 内部模型，两者混淆
会让 import 语句读起来像在拿数据模型。

边界提醒：换模型只影响自然语言理解与解释质量，排班正确性由 solver + validator 保证。
`boundary_note` 是给前端强制展示的，防止用户误以为「选贵的模型排得更好」。
"""
from __future__ import annotations

import copy
import os
from typing import Dict, List, Optional

# 默认取「最准」而非「最快」：意图解析错了，后面 solver 排得再对也是排错了需求。
# 代价是首屏生成从约 10 秒变成约 50 秒——这是显式选择，不是疏漏，前端会展示预计耗时与已等待秒数。
# 想换回快模型：设置环境变量 GLM_MODEL=glm-4-flash-250414（约 6 秒），无需改代码。
DEFAULT_TEXT_MODEL = "glm-4.5-flash"
DEFAULT_VISION_MODEL = "glm-4v-flash"

_PROBED_AT = "2026-09-18"

BOUNDARY_NOTE = (
    "模型只影响自然语言理解与解释的质量，不影响排班正确性。"
    "排班可行性由确定性求解器与独立校验器保证，换模型不会让违规的排班变合规。"
)
UNLOCK_NOTE = "充值后设置环境变量 GLM_EXTRA_MODELS=glm-4-plus,glm-4.6 即可放开，无需改代码。"


_TEXT_MODELS: List[dict] = [
    {
        "id": "glm-4-flash",
        "label": "GLM-4-Flash",
        "tier": "free",
        "is_default": False,
        "latency_hint": "整体约 10 秒",
        "accuracy_hint": "均衡",
        "note": "速度与准确度折中。实测偶发编造未提及员工的约束，已被护栏拦截",
        "measured": {
            "avg_latency_s": 8.3,
            "e2e_latency_s": 9.6,
            "hallucinated_cases": "2/3",
            "probed_at": _PROBED_AT,
        },
    },
    {
        "id": "glm-4-flash-250414",
        "label": "GLM-4-Flash-250414",
        "tier": "free",
        "is_default": False,
        "latency_hint": "整体约 6 秒（最快）",
        "accuracy_hint": "均衡",
        "note": "最快。实测同样偶发编造未提及员工的约束，已被护栏拦截",
        "measured": {
            "avg_latency_s": 3.7,
            "e2e_latency_s": 5.6,
            "hallucinated_cases": "2/3",
            "probed_at": _PROBED_AT,
        },
    },
    {
        "id": "glm-4.5-flash",
        "label": "GLM-4.5-Flash",
        "tier": "free",
        "is_default": True,
        "latency_hint": "整体约 45–85 秒（很慢）",
        "accuracy_hint": "最准",
        "note": "默认，最准但很慢：一次请求跑意图解析和解释生成两趟，线上 3 次实测 43s / 52s / 85s。实测 3 个意图 case 全部正确、零编造。赶时间可切 GLM-4-Flash-250414",
        "measured": {
            "avg_latency_s": 30.1,
            # 取 3 次线上实测（43 / 52 / 85 秒）的中间量级。这个值会驱动前端进度条与
            # 「预计等待」文案，宁可略偏保守也不要低报——低报会让用户以为卡死。
            "e2e_latency_s": 60.0,
            "hallucinated_cases": "0/3",
            "probed_at": _PROBED_AT,
        },
    },
]

_VISION_MODELS: List[dict] = [
    {
        "id": "glm-4v-flash",
        "label": "GLM-4V-Flash",
        "tier": "free",
        "is_default": True,
        "latency_hint": "约 10 秒",
        "accuracy_hint": "清晰表格实测全对",
        "note": "默认。实测 14/14 格、64/64 人次、0 误报",
        # 1024 是实测上限，不是保守估计：写大一点就整个请求 1210 报错，一格都读不出来
        "max_output_tokens": 1024,
        "measured": {"avg_latency_s": 9.5, "cell_accuracy": "14/14", "probed_at": _PROBED_AT},
    },
    {
        "id": "glm-4.1v-thinking-flash",
        "label": "GLM-4.1V-Thinking-Flash",
        "tier": "free",
        "is_default": False,
        "latency_hint": "约 11 秒",
        "accuracy_hint": "偶发截断",
        "note": "备选。首次输出可能被思维链挤爆而截断，重试可恢复；识别结果仍需人工核对",
        # 思维链会先吃掉一大截输出预算，给到 2048 是为了让 JSON 有机会写完
        "max_output_tokens": 2048,
        "measured": {"avg_latency_s": 10.7, "cell_accuracy": "14/14（第二次）", "probed_at": _PROBED_AT},
    },
]

# 探测到但当前 Key 不可用。group 决定被 env 放开后归入文本还是视觉清单。
_LOCKED_MODELS: List[dict] = [
    {"id": "glm-z1-flash", "reason": "实测 3 次调用 2 次触发 429 速率限制，可用性不达标", "group": "text"},
    {"id": "glm-4-plus", "reason": "当前 API Key 余额不足（错误码 1113）", "group": "text"},
    {"id": "glm-4.6", "reason": "当前 API Key 余额不足（错误码 1113）", "group": "text"},
    {"id": "glm-4v-plus", "reason": "当前 API Key 余额不足（错误码 1113）", "group": "vision"},
    {"id": "glm-4.5v", "reason": "当前 API Key 余额不足（错误码 1113）", "group": "vision"},
]


def _extra_ids() -> List[str]:
    """GLM_EXTRA_MODELS：充值后不改代码放开付费模型的唯一入口。"""
    raw = os.getenv("GLM_EXTRA_MODELS", "")
    return list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))


def _locked_group(mid: str) -> str:
    for m in _LOCKED_MODELS:
        if m["id"] == mid:
            return m["group"]
    # 未在 locked 表里登记过的 id 一律当文本模型：视觉调用的出错代价高（读错基线会让
    # 校验结论全错），不能靠猜命名来判断一个模型是否真的支持 image_url。
    return "text"


def _extra_entry(mid: str, group: str) -> dict:
    return {
        "id": mid,
        "label": mid,
        "tier": "paid",
        "is_default": False,
        "latency_hint": "未实测",
        "accuracy_hint": "未实测",
        "note": "由环境变量 GLM_EXTRA_MODELS 放开，本项目未实测其表现",
        # 刻意不给 measured：没有实测数据就不要伪造一份给前端展示
        **({"max_output_tokens": 1024} if group == "vision" else {}),
    }


def text_models() -> List[dict]:
    out = copy.deepcopy(_TEXT_MODELS)
    out.extend(_extra_entry(m, "text") for m in _extra_ids() if _locked_group(m) == "text")
    return out


def vision_models() -> List[dict]:
    out = copy.deepcopy(_VISION_MODELS)
    out.extend(_extra_entry(m, "vision") for m in _extra_ids() if _locked_group(m) == "vision")
    return out


def locked_models() -> List[dict]:
    unlocked = set(_extra_ids())
    return [
        {"id": m["id"], "reason": m["reason"]}
        for m in _LOCKED_MODELS
        if m["id"] not in unlocked
    ]


def text_model_ids() -> List[str]:
    return [m["id"] for m in text_models()]


def vision_model_ids() -> List[str]:
    return [m["id"] for m in vision_models()]


def payload() -> dict:
    """GET /api/models 的响应体。每次现算，好让 GLM_EXTRA_MODELS 改完重启即生效。"""
    return {
        "default_text": DEFAULT_TEXT_MODEL,
        "default_vision": DEFAULT_VISION_MODEL,
        "text": text_models(),
        "vision": vision_models(),
        "locked": locked_models(),
        "boundary_note": BOUNDARY_NOTE,
        "unlock_note": UNLOCK_NOTE,
    }


def _limits() -> Dict[str, int]:
    return {
        m["id"]: int(m["max_output_tokens"])
        for m in vision_models()
        if m.get("max_output_tokens")
    }


def clamp_max_tokens(model: Optional[str], want: int) -> int:
    """按模型自身上限夹取 max_tokens。

    存在的理由很具体：glm-4v-flash 传 max_tokens>1024 会整体失败（1210 参数非法），
    调用方却往往按文本模型的习惯随手传 2000，于是图片识别全军覆没。
    """
    cap = _limits().get(model or "")
    return min(want, cap) if cap else want


# 推理型模型的输出预算下限。
#
# 线上实测逼出来的：glm-4.5-flash 是推理模型，会先花掉一大段 token 做思考再输出答案。
# 意图解析原本按普通模型的习惯只给 900 tokens，结果偶发在 JSON 还没写完时就被截断
# （finish_reason=length），`_extract_json` 拿不到合法 JSON，于是静默掉到规则解析——
# 现象是「模型明明返回了、耗时也正常，但解析质量突然变成规则级」。
_OUTPUT_FLOORS: Dict[str, int] = {
    "glm-4.5-flash": 2048,
}


def output_floor(model: Optional[str]) -> int:
    """该模型至少需要多少输出 token 才不会把答案写到一半被截断。0 表示无特殊要求。"""
    return _OUTPUT_FLOORS.get(model or "", 0)


def resolve_max_tokens(model: Optional[str], want: int) -> int:
    """先按推理型模型的下限抬高，再按模型硬上限夹取。顺序不能反：

    夹取是为了不触发上游参数非法（glm-4v-flash 超 1024 直接整体失败），
    抬高是为了不让推理模型把 JSON 写到一半被截断。硬上限永远优先。
    """
    return clamp_max_tokens(model, max(want, output_floor(model)))


def timeout_for(model: Optional[str], base: float) -> float:
    """按模型实测耗时推导超时，而不是所有模型共用一个 GLM_TIMEOUT。

    存在的理由很具体：glm-4.5-flash 实测约 30 秒，而 GLM_TIMEOUT 默认 12 秒。
    共用超时的结果是——用户在界面上选了「最准」的模型，后端每次都超时并静默降级成
    规则解析，于是他拿到的解析质量比默认模型**更差**。宁可让他多等，也不能给他
    一个永远无法生效的选项。

    留出 2.5 倍余量：实测值是均值，尾部延迟会明显更高。
    """
    latency = 0.0
    for m in _TEXT_MODELS + _VISION_MODELS:
        if m["id"] == model:
            latency = float((m.get("measured") or {}).get("avg_latency_s") or 0.0)
            break
    return max(base, latency * 2.5) if latency else base


# 慢模型偶发超时后的兜底模型：实测单次约 3.7s，是清单里最快的。
# 它的准确率不如默认模型（实测 3 个 case 里 2 个编造过约束），但编造会被护栏拦下，
# 所以「快模型 + 护栏」仍然明显优于掉到规则解析。
FAST_FALLBACK_TEXT_MODEL = "glm-4-flash-250414"


def fallback_model_for(model: Optional[str], base: float) -> Optional[str]:
    """慢模型超时后该改用哪个模型；没有合适目标时返回 None。

    这条路径是线上实测逼出来的：默认模型 glm-4.5-flash 单次耗时波动极大
    （实测 24s / 28s / 34s，也出现过 >75s 打满预算），一旦打满就直接掉到规则解析——
    用户选了「最准」，偶发拿到的却是**最差**的解析结果，而界面上只写「模型暂不可用」。
    先退到最快的模型再试一次，比直接放弃更接近用户意图。

    只对被判定为慢模型的情况生效（timeout_for 放宽过预算的才算），避免快模型失败时
    再无意义地多打一次请求。
    """
    if not model or model == FAST_FALLBACK_TEXT_MODEL:
        return None
    if FAST_FALLBACK_TEXT_MODEL not in text_model_ids():
        return None
    # 只有超时预算被放宽过的模型才算慢模型
    if timeout_for(model, base) <= base:
        return None
    return FAST_FALLBACK_TEXT_MODEL


def reject_text_model(model: str) -> str:
    """非白名单文本模型的 400 文案：必须把可用清单一并告诉用户，否则等于让人猜。"""
    return f"模型 {model} 不可用。可用：{' / '.join(text_model_ids())}"


def reject_vision_model(model: str) -> str:
    return f"视觉模型 {model} 不可用。可用：{' / '.join(vision_model_ids())}"
