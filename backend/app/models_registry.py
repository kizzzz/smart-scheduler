"""可选模型白名单：清单来自 2026-09-18 对生产 Key 的逐个实测，不是文档抄录。

几条实测结论直接决定了这里的数据结构：
- `glm-z1-flash` 三次探测两次 429，可用性不达标，因此只进 `locked`——让用户知道
  它存在、也知道为什么不能选，比从清单里悄悄抹掉更诚实；
- 全部付费模型返回 `1113 余额不足`，同样只进 `locked`，靠 `GLM_EXTRA_MODELS`
  在充值后免改代码放开；
- `glm-4v-flash` 的 `max_tokens` 实测硬上限是 1024（超了返回 `1210 max_tokens参数非法`），
  所以 `max_output_tokens` 是调用方必须夹取的真实约束，不是展示用字段。

模块名刻意叫 models_registry 而非 models：`models.py` 是 Pydantic 内部模型，两者混淆
会让 import 语句读起来像在拿数据模型。

边界提醒：换模型只影响自然语言理解与解释质量，排班正确性由 solver + validator 保证。
`boundary_note` 是给前端强制展示的，防止用户误以为「选贵的模型排得更好」。
"""
from __future__ import annotations

import copy
import os
from typing import Dict, List, Optional

DEFAULT_TEXT_MODEL = "glm-4-flash"
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
        "is_default": True,
        "latency_hint": "约 4–8 秒",
        "accuracy_hint": "均衡",
        "note": "默认。实测偶发编造未提及员工的约束，已被护栏拦截",
        "measured": {"avg_latency_s": 8.3, "hallucinated_cases": "2/3", "probed_at": _PROBED_AT},
    },
    {
        "id": "glm-4-flash-250414",
        "label": "GLM-4-Flash-250414",
        "tier": "free",
        "is_default": False,
        "latency_hint": "约 4 秒（最快）",
        "accuracy_hint": "均衡",
        "note": "最快。实测同样偶发编造未提及员工的约束，已被护栏拦截",
        "measured": {"avg_latency_s": 3.7, "hallucinated_cases": "2/3", "probed_at": _PROBED_AT},
    },
    {
        "id": "glm-4.5-flash",
        "label": "GLM-4.5-Flash",
        "tier": "free",
        "is_default": False,
        "latency_hint": "约 30 秒（慢）",
        "accuracy_hint": "最准",
        "note": "最准但慢。实测 3 个意图 case 全部正确、零编造",
        "measured": {"avg_latency_s": 30.1, "hallucinated_cases": "0/3", "probed_at": _PROBED_AT},
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


def reject_text_model(model: str) -> str:
    """非白名单文本模型的 400 文案：必须把可用清单一并告诉用户，否则等于让人猜。"""
    return f"模型 {model} 不可用。可用：{' / '.join(text_model_ids())}"


def reject_vision_model(model: str) -> str:
    return f"视觉模型 {model} 不可用。可用：{' / '.join(vision_model_ids())}"
