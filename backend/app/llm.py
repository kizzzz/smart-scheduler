"""GLM 接入层：L1 意图解析 与 L4 解释生成。

两条硬性边界：
1. LLM 只在输入端（理解自然语言）和输出端（解释已校验结果）出现，绝不参与排班正确性判定；
2. 任何一端失败都必须降级，不允许因模型不可用导致整体不可用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import models_registry
from .config import (
    RULE_MIN_STAFF,
    ConfigIndex,
    ConfigLike,
    index_of,
    slot_key,
)
from .models import (
    Diagnosis,
    GuardrailStats,
    Schedule,
    ScheduleRequest,
    ValidationReport,
)

# 每一次降级都必须留日志。踩过的坑：线上偶发掉到规则解析，页面只显示「语言模型暂不可用」，
# 而容器日志里一行都没有——异常全被 `except Exception` 吞掉，等于把最需要排查的路径做成了黑盒。
logger = logging.getLogger("scheduler.llm")

GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.getenv("GLM_MODEL", models_registry.DEFAULT_TEXT_MODEL)
GLM_TIMEOUT = float(os.getenv("GLM_TIMEOUT", "12"))
# 只认显式配置的代理：不继承环境变量，避免宿主机 NO_PROXY/CIDR 之类的脏配置把出网打挂
GLM_PROXY = os.getenv("GLM_PROXY") or None
GLM_RETRIES = int(os.getenv("GLM_RETRIES", "2"))
# 视觉识别比意图解析慢一倍（实测 ~10s），沿用文本超时会稳定超时
GLM_VISION_TIMEOUT = float(os.getenv("GLM_VISION_TIMEOUT", "40"))


def api_key() -> str:
    return os.getenv("GLM_API_KEY", "").strip()


def llm_enabled() -> bool:
    return bool(api_key())


def _err_detail(exc: Exception) -> str:
    """把上游错误压成一行可读信息：限流(429)、鉴权(401)、余额(1113) 的处置完全不同。"""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        body = (exc.response.text or "")[:200].replace("\n", " ")
        return f"status={exc.response.status_code} body={body}"
    return str(exc)[:200] or "-"


async def _chat(
    messages: List[Dict[str, Any]],
    *,
    temperature: float,
    json_mode: bool,
    max_tokens: int,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> str:
    # model 省略时沿用模块级默认，保证老调用方行为逐字不变
    used = model or GLM_MODEL
    payload: Dict[str, Any] = {
        "model": used,
        "messages": messages,
        # 先按推理模型的输出下限抬高，再按模型硬上限夹取：
        # 抬高防止 glm-4.5-flash 把 JSON 写到一半被截断，夹取防止 glm-4v-flash 触发 1210 参数非法
        "max_tokens": models_registry.resolve_max_tokens(used, max_tokens),
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    # 按模型实测耗时放宽超时：慢模型共用 GLM_TIMEOUT 会每次超时并静默降级，
    # 等于给用户一个永远无法生效的选项
    effective_timeout = models_registry.timeout_for(used, timeout or GLM_TIMEOUT)
    last: Exception | None = None
    for attempt in range(GLM_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=effective_timeout, trust_env=False, proxy=GLM_PROXY
            ) as client:
                r = await client.post(f"{GLM_BASE_URL}/chat/completions", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            choice = (data.get("choices") or [{}])[0]
            # 截断是静默降级的主要成因：内容看起来正常，只是 JSON 少了后半截。
            # 不记这一行的话，日志里只能看到「解析降级」而看不到原因。
            finish = choice.get("finish_reason")
            if finish and finish != "stop":
                logger.warning(
                    "llm 输出未正常结束 model=%s finish_reason=%s max_tokens=%s（可能被截断）",
                    used,
                    finish,
                    payload["max_tokens"],
                )
            return (choice.get("message") or {}).get("content") or ""
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # 5xx 与网络抖动可重试；4xx（鉴权/参数错误）直接失败，重试没有意义
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            # 超时不重试：超时说明已经等满了整个预算，再试两次只会把等待时间翻三倍。
            # 对 glm-4.5-flash 这种 75s 预算的慢模型，重试会让单次请求拖到 225s。
            if isinstance(exc, httpx.TimeoutException):
                raise
            last = exc
            if attempt < GLM_RETRIES:
                await asyncio.sleep(0.6 * (attempt + 1))
    raise last if last else RuntimeError("GLM 调用失败")


async def _chat_text(
    messages: List[Dict[str, Any]],
    *,
    temperature: float,
    json_mode: bool,
    max_tokens: int,
    model: Optional[str],
) -> tuple[str, str]:
    """调用文本模型，慢模型超时后自动退到最快的模型再试一次。

    返回 (内容, 真正产出内容的模型)。

    为什么需要这层：默认模型 glm-4.5-flash 单次耗时波动极大（线上实测 24s / 28s / 34s，
    也出现过打满 75s 预算）。打满就直接掉到规则解析，用户选了「最准」却偶发拿到**最差**
    的解析结果。先用最快的模型重试一次，绝大多数情况能在几秒内拿到一个仍过护栏的 LLM 结果。

    只兜超时，不兜 4xx：鉴权错、参数错换个模型也一样失败，重试只是浪费用户时间。
    """
    used = model or GLM_MODEL
    try:
        return await _chat(
            messages,
            temperature=temperature,
            json_mode=json_mode,
            max_tokens=max_tokens,
            model=used,
        ), used
    except httpx.TimeoutException:
        fb = models_registry.fallback_model_for(used, GLM_TIMEOUT)
        if not fb:
            logger.warning("llm timeout model=%s 无可用兜底模型，将降级", used)
            raise
        logger.warning("llm timeout model=%s，改用兜底模型 %s 重试", used, fb)
        # 兜底这一次不再重试：用户已经等满了慢模型的整个预算
        return await _chat(
            messages,
            temperature=temperature,
            json_mode=json_mode,
            max_tokens=max_tokens,
            model=fb,
        ), fb


# ---------- L1 意图解析 ----------


def _emp_lines(index: ConfigIndex) -> str:
    lines: List[str] = []
    for eid in index.employee_ids:
        e = index.employee(eid)
        if e is None:
            continue
        # name 与 id 相同时不重复显示：默认配置用工号占位姓名，写成「E01（E01）」纯属噪声
        who = eid if e.name == eid else f"{eid}（{e.name}）"
        days = "、".join(index.day_label(d) for d in index.available_days(eid))
        line = f"- {who}｜{e.role}｜技能 {'/'.join(sorted(e.skills))}｜可工作 {days}"
        leave = sorted({
            index.day_label(u.day) for u in e.unavailable if u.reason and u.day in index.days
        })
        if leave:
            line += f"｜固有请假 {'、'.join(leave)}"
        lines.append(line)
    return "\n".join(lines)


def _min_staff_lines(index: ConfigIndex) -> str:
    """把逐格人数下限压成一句可读描述，供模型判断 override 是否真的抬高了下限。

    不能再写死「工作日 4 人、周末 6 人」：那是默认配置的取值，不是规则本身。
    """
    if not index.rules_of(RULE_MIN_STAFF):
        return "当前配置未设置人数下限规则"
    groups: Dict[int, List[str]] = {}
    for day, shift in index.slots:
        groups.setdefault(index.min_required(day, shift), []).append(index.slot_label(day, shift))
    # 只有一档时说成「每班 N 人」，避免把 14 个格子逐个列出来把 prompt 撑爆
    if len(groups) == 1:
        return f"每班 {next(iter(groups))} 人"
    return "；".join(
        f"{n} 人：{'、'.join(labels[:8])}{'…' if len(labels) > 8 else ''}"
        for n, labels in sorted(groups.items())
    )


def intent_system_prompt(config: ConfigLike = None) -> str:
    """按当前配置拼意图解析的 system prompt。

    每次现拼而不是缓存：一次 LLM 调用要几秒，拼几十行字符串的开销可以忽略，
    而缓存会在配置改动后悄悄用旧员工池解析——那是最难发现的一类错。
    """
    index = index_of(config)
    day_desc = "、".join(f"{index.day_label(d)}" for d in index.day_ids)
    shift_desc = "、".join(
        f"{index.shift_name(s)}（{index.shift_time_label(s)}）" for s in index.shift_ids
    )
    sample_day = index.day_ids[-1] if index.day_ids else "一"
    sample_shift = index.shift_ids[-1] if index.shift_ids else "晚班"
    sample_key = slot_key(sample_day, sample_shift)
    day_ids = "、".join(index.day_ids)
    shift_ids = "、".join(index.shift_ids)
    # 示例里的工号必须取自当前员工池：写死 E06/E01 会让模型在自定义配置下
    # 「照着例子抄」出一个根本不存在的人，而那是最难在下游发现的一类幻觉。
    pool = index.employee_ids or ["E01"]
    ex = [pool[i % len(pool)] for i in range(4)]
    return f"""你是排班系统的意图解析器。你的唯一职责是把店长的自然语言指令翻译成 JSON，**不要排班、不要判断规则是否满足、不要给建议**。

可用员工（只能使用这些 ID）：
{_emp_lines(index)}

排班周期共 {len(index.day_ids)} 天：{day_desc}
日期字段只能填这些值：{day_ids}
班次字段只能填这些值：{shift_ids}（分别是 {shift_desc}）

输出 JSON，字段如下（不确定的字段就省略）：
{{
  "action": "generate | adjust | unknown",
  "temp_leaves": [{{"employee_id": "{ex[0]}", "days": ["{sample_day}"], "reason": "临时请假"}}],
  "pins": [{{"employee_id": "{ex[1]}", "day": "{sample_day}", "shift": "{sample_shift}"}}],
  "forbids": [{{"employee_id": "{ex[2]}", "day": "{sample_day}", "shift": "{sample_shift}"}}],
  "exclude_employees": ["{ex[3]}"],
  "soft_preferences": [{{"kind": "respect_shift_preference | balance_workload | protect_part_time_weekend", "weight": 1.0}}],
  "min_staff_override": {{"{sample_key}": 7}},
  "notes": ["把原话中的关键约束复述成一句话，便于店长确认"],
  "clarification_needed": []
}}

判定规则：
- 只是"排一周班""生成排班"→ action=generate。
- 在已有排班基础上改动（某人请假、某人必须上某班、某班加人）→ action=adjust。
- 出现下列任一情况，必须 action=unknown，并在 clarification_needed 写清要问店长什么：
  指代的人无法唯一对应到上面的员工 ID；日期或班次含糊（如"周末某天""下周"）；指令自相矛盾；要求降低人数下限或放宽硬规则。
- min_staff_override 只能用于**提高**人数要求，绝不能降低。值 = 该班次的目标总人数；当前下限是 {_min_staff_lines(index)}，所以只有高于这个值才有意义。
- 不要臆造员工 ID，不要给员工补技能。

只输出 JSON，不要 markdown 代码块。"""


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            return None
    return None


_EMP_MENTION_RE = re.compile(r"[EeＥｅ]\s*[0-9０-９]{1,2}")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def mentioned_employee_ids(text: str, config: ConfigLike = None) -> set[str]:
    """从原始指令里抽出**真的被提到过**的员工 ID。

    用途是反幻觉：模型偶发会凭空补出用户没提的人（实测 glm-4-flash 会给
    「E05 周六请假」顺带编一条「E01 固定在周六早班」）。这类约束会悄悄改变
    排班结果，且店长很难发现，所以必须在进求解器之前丢掉。

    容错写法：E01 / e1 / Ｅ０１ 都能识别，统一归一到 E0X；同时接受配置里原样出现的工号，
    因为自定义工号不一定是 E + 两位数字这种形状。
    """
    index = index_of(config)
    found: set[str] = set()
    for m in _EMP_MENTION_RE.findall(text or ""):
        digits = m.translate(_FULLWIDTH_DIGITS)
        digits = re.sub(r"[^0-9]", "", digits)
        if not digits:
            continue
        eid = f"E{int(digits):02d}"
        if index.known(eid):
            found.add(eid)
    flat = (text or "").lower()
    for eid in index.all_employees:
        # 自定义工号（"张三-01"、"S1" 这类）只能靠原样出现来判断，形状无从假设
        if eid not in found and len(eid) >= 2 and eid.lower() in flat:
            found.add(eid)
    return found


_GUARD_LABELS = (
    ("pins", "固定排班"),
    ("forbids", "禁止排班"),
    ("excludes", "整周排除"),
    ("min_staff", "无效人数下限"),
    ("leaves", "临时请假"),
)


def _guardrail(counts: Dict[str, int], invalid_ids: List[str]) -> GuardrailStats:
    """把拦截计数折成前端可直接显示的一句中文。

    措辞由后端定，前端不拼字符串——同一件事在两端各写一遍文案，早晚会说成两个意思。
    """
    parts = [f"{counts[k]} 条{label}" for k, label in _GUARD_LABELS if counts.get(k)]
    total = sum(counts.get(k, 0) for k, _ in _GUARD_LABELS)
    triggered = bool(total or invalid_ids)
    summary = ""
    if total:
        summary = f"已丢弃 {total} 条模型自造约束：" + "、".join(parts)
    elif triggered:
        # 兜底以维持不变式「triggered 为真 ⇒ summary 非空」：前端只会显示 summary，
        # 一个亮着灯却没有文案的护栏比不亮更糟
        summary = "已丢弃模型给出的非法工号：" + "、".join(invalid_ids)
    return GuardrailStats(
        triggered=triggered,
        dropped_pins=counts.get("pins", 0),
        dropped_forbids=counts.get("forbids", 0),
        dropped_excludes=counts.get("excludes", 0),
        dropped_min_staff=counts.get("min_staff", 0),
        dropped_leaves=counts.get("leaves", 0),
        invalid_employee_ids=invalid_ids,
        summary=summary,
    )


def _appears_verbatim(token: str, text: str) -> bool:
    """指令原文里是否真出现过这个 token（全角折半、忽略大小写）。

    用来区分两种「非法工号」：店长自己写错了（E99 请假）要反问；模型凭空编出来的
    （实测「帮我排下周的班」会被补出 E001/E002 的 pins）只能静默丢弃——
    拿一个用户从没提过的工号去反问，只会让人怀疑系统坏了。
    """
    flat = (text or "").translate(_FULLWIDTH_DIGITS).lower()
    return token.strip().lower() in flat


def _override_baseline(index: ConfigIndex, key: str) -> int:
    """该 override 键覆盖到的格子里最低的人数下限。

    取最低值是因为 override 只有在**至少一个**被覆盖的格子上真的抬高了下限时才有意义：
    对 "all" 用最高值会把一条合理的上调误判成 no-op 丢掉。
    """
    if key == "all":
        return min((index.min_required(d, s) for d, s in index.slots), default=0)
    if "|" in key:
        day, shift = key.split("|", 1)
        return index.min_required(day, shift)
    return min((index.min_required(key, s) for s in index.shift_ids), default=0)


def _sanitize(
    raw: dict,
    text: str,
    source: str,
    model: Optional[str] = None,
    config: ConfigLike = None,
) -> ScheduleRequest:
    """对模型输出做确定性清洗：非法员工/日期/班次一律丢弃并转为澄清项。

    白名单全部来自配置：员工池、日期、班次、人数下限基线。这是配置化最关键的一处护栏——
    白名单写死在代码里，等于「换一家门店，模型编出来的约束就再也拦不住了」。
    """
    index = index_of(config)
    problems: List[str] = []
    dropped: List[str] = []
    mentioned = mentioned_employee_ids(text, index)
    # 按约束种类分桶计数，才能在前端说清「丢的是固定排班还是人数下限」
    counts: Dict[str, int] = {}
    invalid_ids: List[str] = []

    def ok_emp(eid: Any, bucket: str) -> bool:
        if not (isinstance(eid, str) and eid in index.all_employees):
            counts[bucket] = counts.get(bucket, 0) + 1
            if isinstance(eid, str) and eid.strip():
                invalid_ids.append(eid.strip())
                if not _appears_verbatim(eid, text):
                    dropped.append(eid.strip())
                    return False
            problems.append(f"指令中的「{eid}」无法对应到员工数据中的任何 ID，请确认具体是谁")
            return False
        # 反幻觉：ID 合法但原文根本没提这个人，视为模型编造，静默丢弃。
        # 不转成澄清问题——就指令里不存在的人反问店长只会造成困惑。
        if eid not in mentioned:
            dropped.append(eid)
            counts[bucket] = counts.get(bucket, 0) + 1
            return False
        return True

    def ok_day(d: Any) -> bool:
        return isinstance(d, str) and d in index.days

    def ok_shift(s: Any) -> bool:
        return isinstance(s, str) and s in index.shifts

    def outside_scenario(*checked: tuple[Any, Any]) -> List[str]:
        """挑出「写得很具体、但当前场景里没有」的维度值。

        与「模型压根没说清哪天哪班」的区别只在措辞上：两种情况都要反问店长（人是他自己
        提的，问一句是有意义的），但前者能指名道姓地说清哪个值不在场景里。
        两种情况都要计入 dropped——这条约束确实没进求解器，护栏统计不能少记。
        """
        return [
            v.strip() for v, checker in checked
            if isinstance(v, str) and v.strip() and not checker(v)
        ]

    temp_leaves = []
    for tl in raw.get("temp_leaves") or []:
        if not isinstance(tl, dict) or not ok_emp(tl.get("employee_id"), "leaves"):
            continue
        raw_days = tl.get("days") or []
        days = [d for d in raw_days if ok_day(d)]
        if days:
            temp_leaves.append({"employee_id": tl["employee_id"], "days": days, "reason": tl.get("reason")})
            continue
        who = tl.get("employee_id")
        counts["leaves"] = counts.get("leaves", 0) + 1
        bad = outside_scenario(*((d, ok_day) for d in raw_days))
        if bad:
            dropped.append(f"{who}@{'/'.join(bad)}")
            problems.append(f"{who} 的请假日期「{'、'.join(bad)}」不在当前排班周期内，请确认是哪一天")
        else:
            problems.append(f"{who} 的请假日期不明确，请说明是周几")

    pins = []
    for p in raw.get("pins") or []:
        if not isinstance(p, dict) or not ok_emp(p.get("employee_id"), "pins"):
            continue
        if ok_day(p.get("day")) and ok_shift(p.get("shift")):
            pins.append({"employee_id": p["employee_id"], "day": p["day"], "shift": p["shift"]})
            continue
        who = p.get("employee_id")
        counts["pins"] = counts.get("pins", 0) + 1
        bad = outside_scenario((p.get("day"), ok_day), (p.get("shift"), ok_shift))
        if bad:
            dropped.append(f"{who}@{'/'.join(bad)}")
            problems.append(f"{who} 要固定到的「{'、'.join(bad)}」不在当前排班场景内，请确认")
        else:
            problems.append(f"{who} 要固定到哪一天的哪个班次不明确")

    forbids = []
    for f in raw.get("forbids") or []:
        if not isinstance(f, dict) or not ok_emp(f.get("employee_id"), "forbids"):
            continue
        if ok_day(f.get("day")):
            sh = f.get("shift") if ok_shift(f.get("shift")) else None
            forbids.append({"employee_id": f["employee_id"], "day": f["day"], "shift": sh})
            continue
        # 禁排缺日期时不反问：少一条禁排只是少一个软性偏好，问一句的打扰大于收益
        counts["forbids"] = counts.get("forbids", 0) + 1
        bad = outside_scenario((f.get("day"), ok_day))
        if bad:
            dropped.append(f"{f.get('employee_id')}@{'/'.join(bad)}")

    excludes = []
    for e in raw.get("exclude_employees") or []:
        if isinstance(e, str) and e in index.all_employees and e in mentioned:
            excludes.append(e)
            continue
        # 整周排除的代价最大（直接抽走一个人的全部班），编造的一律丢，且不反问
        counts["excludes"] = counts.get("excludes", 0) + 1
        if isinstance(e, str) and e.strip() and e not in index.all_employees:
            invalid_ids.append(e.strip())
        elif isinstance(e, str):
            dropped.append(e)

    prefs = []
    for sp in raw.get("soft_preferences") or []:
        if isinstance(sp, dict) and sp.get("kind") in {
            "respect_shift_preference", "balance_workload", "protect_part_time_weekend"
        }:
            prefs.append({"kind": sp["kind"], "weight": float(sp.get("weight") or 1.0)})

    overrides: Dict[str, int] = {}
    for k, v in (raw.get("min_staff_override") or {}).items():
        try:
            iv = int(v)
        except Exception:
            continue
        if iv <= 0:
            continue
        if not (k in index.days or k == "all" or ("|" in k and k.split("|")[0] in index.days)):
            continue
        # 只保留「真的抬高了下限」的 override。
        # 模型常把「周末早班多留一个收银」这类**技能位**诉求错译成 headcount=1，
        # 而人数下限规则本身已经要求更多人，这种值是纯 no-op，
        # 留着只会在前端 chip 上显示成误导性的「周六 早班 ≥ 1 人」。
        if iv <= _override_baseline(index, k):
            dropped.append(f"min_staff_override:{k}={iv}")
            counts["min_staff"] = counts.get("min_staff", 0) + 1
            continue
        overrides[k] = iv

    clar = [c for c in (raw.get("clarification_needed") or []) if isinstance(c, str) and c.strip()]
    clar.extend(problems)
    action = raw.get("action") if raw.get("action") in {"generate", "adjust", "unknown"} else "generate"
    if clar:
        action = "unknown"

    notes = [n for n in (raw.get("notes") or []) if isinstance(n, str)][:4]
    if dropped:
        notes.append("已丢弃模型凭空补充的约束：" + "、".join(dict.fromkeys(dropped)))

    return ScheduleRequest(
        action=action,  # type: ignore[arg-type]
        temp_leaves=temp_leaves,  # type: ignore[arg-type]
        pins=pins,  # type: ignore[arg-type]
        forbids=forbids,  # type: ignore[arg-type]
        exclude_employees=excludes,
        soft_preferences=prefs,  # type: ignore[arg-type]
        min_staff_override=overrides,
        notes=notes,
        clarification_needed=list(dict.fromkeys(clar))[:4],
        raw_text=text,
        parse_source=source,  # type: ignore[arg-type]
        parse_confidence=0.9 if source == "llm" else 0.5,
        model_used=model if source == "llm" else None,
        guardrail=_guardrail(counts, list(dict.fromkeys(invalid_ids))),
    )


_LEAVE_WORDS = ("请假", "休假", "不能来", "来不了", "有事", "生病", "病了")
_PLAIN_GEN_WORDS = ("排班", "排一周", "排下周", "生成", "排个班", "排班表", "重排", "安排")
_PERSON_HINT = ("小", "老", "他", "她", "某人", "那个", "同事")

# 日期前缀：店长写「周四」「星期四」「礼拜四」都是同一天
_DAY_PREFIXES = ("周", "星期", "礼拜")


def _scan_days(index: ConfigIndex, text: str) -> List[str]:
    """从原文里扫出日期 id，按出现顺序去重。

    单字符 id（默认配置的「一」～「日」）必须带前缀才算命中：否则「排一周班」里的「一」
    会被当成周一，把一句普通指令变成一条临时请假。
    """
    hits: List[Tuple[int, str]] = []
    for day in index.day_ids:
        label = index.day_label(day)
        forms = [label] + [p + day for p in _DAY_PREFIXES]
        if len(day) > 1:
            forms.append(day)
        for form in forms:
            pos = text.find(form)
            if pos >= 0:
                hits.append((pos, day))
                break
    return [d for _, d in sorted(hits)]


def _scan_shift(index: ConfigIndex, text: str) -> Optional[str]:
    """原文里出现的第一个班次（按位置，不按配置顺序）。"""
    best: Optional[Tuple[int, str]] = None
    for shift in index.shift_ids:
        for form in (index.shift_name(shift), shift):
            pos = text.find(form)
            if pos >= 0 and (best is None or pos < best[0]):
                best = (pos, shift)
            if pos >= 0:
                break
    return best[1] if best else None


def fallback_parse(text: str, config: ConfigLike = None) -> ScheduleRequest:
    """LLM 不可用时的规则兜底：只认「员工ID + 周几 + 请假」这类高置信模式。

    设计原则：兜底可以「少识别约束」，但不能把一句普通的「排个班」变成澄清追问。
    """
    index = index_of(config)
    raw: Dict[str, Any] = {"action": "generate", "temp_leaves": [], "notes": [], "clarification_needed": []}
    if not text.strip():
        return _sanitize(raw, text, "fallback_rule", config=index)

    emps = sorted(mentioned_employee_ids(text, index))
    days = _scan_days(index, text)
    labels = "、".join(index.day_label(d) for d in days)
    whole_week = any(w in text for w in ("整周", "全周", "这周都", "一周都", "本周都"))
    unavailable = any(w in text for w in ("不排", "不要排", "别排", "不可用", "出差", "培训", "休假", "请假"))
    if emps and whole_week and unavailable:
        raw["action"] = "adjust"
        raw["exclude_employees"] = emps
        raw["notes"] = [f"{'、'.join(emps)} 整周不可排班"]
    elif emps and days and any(w in text for w in _LEAVE_WORDS):
        raw["action"] = "adjust"
        raw["temp_leaves"] = [{"employee_id": emps[0], "days": days, "reason": "临时请假"}]
        raw["notes"] = [f"{emps[0]} {labels}不可排班"]
    elif emps and days and ("必须" in text or "固定" in text or "盯" in text):
        shift = _scan_shift(index, text)
        if shift:
            raw["action"] = "adjust"
            raw["pins"] = [{"employee_id": emps[0], "day": days[0], "shift": shift}]
            raw["notes"] = [f"{emps[0]} 固定在{index.slot_label(days[0], shift)}"]
        else:
            names = "还是".join(index.shift_name(s) for s in index.shift_ids)
            raw["clarification_needed"] = [f"{emps[0]} 要固定到{index.day_label(days[0])}的{names}？"]
    elif not emps and (any(w in text for w in _LEAVE_WORDS) or any(w in text for w in _PERSON_HINT)):
        # 提到了人或请假，却没有可识别的员工 ID —— 这才是真正需要澄清的情况
        sample = index.employee_ids[0] if index.employee_ids else "E06"
        raw["clarification_needed"] = [f"指令里的员工无法唯一确定，请用员工编号（如 {sample}）说明是谁"]
    elif any(w in text for w in _PLAIN_GEN_WORDS):
        n = len({r.id for r in index.rules})
        raw["notes"] = [f"语言模型当前不可用，已按 {n} 条硬规则生成基础排班，未识别指令中的额外约束"]
    else:
        sample = index.employee_ids[0] if index.employee_ids else "E06"
        sample_day = index.day_label(index.day_ids[0]) if index.day_ids else "周一"
        raw["notes"] = [
            f"未识别到额外约束，已生成基础排班；如需特殊要求，请用「{sample} {sample_day}请假」这类明确表述"
        ]
    return _sanitize(raw, text, "fallback_rule", config=index)


async def parse_intent(
    text: str, model: Optional[str] = None, config: ConfigLike = None
) -> ScheduleRequest:
    """model 省略时用模块默认模型，config 省略时用默认配置，行为与改造前完全一致。"""
    if not text.strip():
        return ScheduleRequest(action="generate", raw_text="", parse_source="structured", parse_confidence=1.0)
    index = index_of(config)
    if not llm_enabled():
        return fallback_parse(text, index)
    requested = model or GLM_MODEL
    try:
        content, answered_by = await _chat_text(
            [
                {"role": "system", "content": intent_system_prompt(index)},
                {"role": "user", "content": text},
            ],
            temperature=0.05,
            json_mode=True,
            max_tokens=900,
            model=requested,
        )
        raw = _extract_json(content)
        if raw is None:
            # 这条路径以前完全没有日志：模型明明返回了内容，却因为不是合法 JSON 而降级，
            # 排查时只能看到「降级为规则解析」，看不到模型到底吐了什么。
            logger.warning(
                "parse_intent 无法从模型输出中抽取 JSON，降级为规则解析 model=%s len=%d head=%r",
                answered_by,
                len(content),
                content[:160],
            )
            return fallback_parse(text, index)
        out = _sanitize(raw, text, "llm", model=answered_by, config=index)
        # 换过模型就必须让用户看见：解析质量与他所选不同，隐瞒等于给出一个假的质量承诺
        if answered_by != requested:
            out.model_fallback_from = requested
        return out
    except Exception as exc:
        # 带上异常类型和上游返回码：区分「超时」「限流 429」「参数错」是三种完全不同的处置
        logger.warning(
            "parse_intent 降级为规则解析 model=%s err=%s detail=%s",
            requested,
            type(exc).__name__,
            _err_detail(exc),
        )
        return fallback_parse(text)


# ---------- L4 解释生成 ----------


def explain_system_prompt(config: ConfigLike = None) -> str:
    index = index_of(config)
    # 只列生效规则：把停用的规则也喂给模型，它会在解释里引用一条用户已经关掉的规则
    rule_lines = "\n".join(f"{r.id}：{r.name}" for r in index.active_rules)
    return f"""你是排班助手的解释生成器，读者是门店店长。

硬规则清单（供你引用编号，不要改写含义）：
{rule_lines}

严格要求：
1. 只能使用输入 JSON 里的事实。禁止推断、禁止补充任何 JSON 中没有的数字、人名、日期。
2. 校验结论已由系统给出，你不得改变结论，也不得说"应该没问题""可能有风险"这类含糊话。
3. 输出 3–5 条中文要点，用「- 」开头，每条不超过 45 字，结论先行。
4. 如果 infeasible 为 true，要点必须说明卡在哪条规则、哪个班次，以及给出的解锁选项，不要安慰性表述。
5. 不要输出标题、不要 markdown 加粗、不要重复规则原文。"""


def _facts(
    schedule: Optional[Schedule],
    report: Optional[ValidationReport],
    intent: ScheduleRequest,
    diagnosis: Diagnosis,
    config: ConfigLike = None,
) -> dict:
    index = index_of(config)
    out: Dict[str, Any] = {
        "action": intent.action,
        "user_instruction": intent.raw_text[:300],
        "applied_constraints": {
            "temp_leaves": [t.model_dump() for t in intent.temp_leaves],
            "pins": [p.model_dump() for p in intent.pins],
            "excluded": intent.exclude_employees,
            "min_staff_override": intent.min_staff_override,
        },
        "infeasible": diagnosis.infeasible,
    }
    if diagnosis.infeasible:
        out["diagnosis"] = {
            "kind": diagnosis.kind,
            "bottleneck_rule": diagnosis.bottleneck_rule,
            "bottleneck_slots": diagnosis.bottleneck_slots,
            "evidence": diagnosis.evidence,
            "unlock_options": [o.model_dump() for o in diagnosis.unlock_options],
        }
        return out
    if report:
        out["all_hard_rules_passed"] = report.passed
        out["violations"] = [v.model_dump() for v in report.violations][:10]
        out["soft_metrics"] = report.soft_metrics.model_dump()
        out["shifts_per_employee"] = report.per_employee_shifts
    if schedule:
        # 最稀缺资质的逐格覆盖情况：解释里最常被问的就是「谁在值守」。
        # 按 scarce_attribute 取而不是写死「店长值守」，换成别的资质也不必改这里
        scarce = index.scarce_attribute()
        if scarce:
            attr, value = scarce
            out["attribute_coverage"] = {
                "attribute": value,
                "per_slot": {
                    index.slot_label(s.day, s.shift): [
                        e for e in s.employee_ids if index.has_attribute(e, attr, value)
                    ]
                    for s in schedule.slots
                },
            }
    return out


def template_explanation(
    report: Optional[ValidationReport],
    intent: ScheduleRequest,
    diagnosis: Diagnosis,
    config: ConfigLike = None,
) -> str:
    index = index_of(config)
    lines: List[str] = []
    if diagnosis.infeasible:
        lines.append(
            f"- 当前约束下无可行排班，卡点规则：{diagnosis.bottleneck_rule or '硬约束组合'}"
        )
        for ev in diagnosis.evidence[:2]:
            lines.append(f"- {ev}")
        for o in diagnosis.unlock_options[:2]:
            lines.append(f"- 解锁选项：{o.title}（代价：{o.cost}）")
        return "\n".join(lines)
    if report is None:
        return "- 暂无可解释的排班结果"
    if report.passed:
        lines.append(f"- {len(report.rule_results) or len(index.rules)} 条硬规则全部通过，本方案可直接使用")
    else:
        lines.append(f"- 存在 {len(report.violations)} 处硬规则违规，需修改后才能使用")
        for v in report.violations[:3]:
            lines.append(f"- {v.rule_id}：{v.detail}")
    m = report.soft_metrics
    lines.append(f"- 班次偏好满足 {m.preference_hit}/{m.preference_total}，人均班次 {m.workload_min}–{m.workload_max} 个")
    if intent.temp_leaves:
        who = "、".join(
            f"{t.employee_id} {'、'.join(index.day_label(d) for d in t.days)}"
            for t in intent.temp_leaves[:2]
        )
        lines.append(f"- 已按指令避开：{who}")
    return "\n".join(lines)


_CONTRADICTION_WORDS = (
    "违规", "不满足", "需增加", "应增加", "需要增加", "需补充", "建议增加",
    "不合规", "缺少", "不足", "未满足", "存在风险", "可能有", "应该没问题",
)


def _clean_explanation(
    content: str, report: Optional[ValidationReport], diagnosis: Diagnosis
) -> Optional[str]:
    """确定性后置校验：模型解释不得与校验结论矛盾，矛盾行直接丢弃。

    这是防幻觉的最后一道闸——校验器说 0 违规，解释里就不允许出现「需增加/不满足」。
    """
    lines: List[str] = []
    for raw_line in content.splitlines():
        s = raw_line.strip().lstrip("-•*　 ").strip()
        if not s or len(s) < 4:
            continue
        s = s.replace("**", "")
        if report is not None and report.passed and not diagnosis.infeasible:
            if any(w in s for w in _CONTRADICTION_WORDS):
                continue
        lines.append(f"- {s[:60]}")
        if len(lines) >= 5:
            break
    if len(lines) < 2:
        return None
    return "\n".join(lines)


async def explain(
    schedule: Optional[Schedule],
    report: Optional[ValidationReport],
    intent: ScheduleRequest,
    diagnosis: Diagnosis,
    model: Optional[str] = None,
    config: ConfigLike = None,
) -> tuple[str, str]:
    index = index_of(config)
    fallback = template_explanation(report, intent, diagnosis, index)
    if not llm_enabled():
        return fallback, "template"
    try:
        content, _ = await _chat_text(
            [
                {"role": "system", "content": explain_system_prompt(index)},
                {
                    "role": "user",
                    "content": json.dumps(
                        _facts(schedule, report, intent, diagnosis, index), ensure_ascii=False
                    ),
                },
            ],
            temperature=0.3,
            json_mode=False,
            max_tokens=600,
            model=model or GLM_MODEL,
        )
        cleaned = _clean_explanation(content, report, diagnosis)
        if cleaned is None:
            logger.warning("explain 输出未通过清洗，降级为模板解释 model=%s", model or GLM_MODEL)
            return fallback, "template"
        return cleaned, "llm"
    except Exception as exc:
        logger.warning(
            "explain 降级为模板解释 model=%s err=%s detail=%s",
            model or GLM_MODEL,
            type(exc).__name__,
            _err_detail(exc),
        )
        return fallback, "template"


# ---------- 图片排班表识别（导入链路专用） ----------

# 这段 prompt 是实测调出来的，改动前请重新验一遍：glm-4v-flash 在 1100×620 清晰排班表上
# 做到 14/14 格、64/64 人次、0 误报。三处细节是关键：
# 1. 明确「看不清就填 []，不要凭常识补人」——否则模型会拿常见排班习惯补齐空格；
# 2. 明确格子总数，给模型一个自检锚点（数量由配置推导，不再写死 14）；
# 3. 明确禁 markdown，虽然仍要靠 _extract_json 兜底。
def vision_prompt(config: ConfigLike = None) -> str:
    index = index_of(config)
    day_ids = " ".join(index.day_ids)
    shift_ids = " ".join(index.shift_ids)
    sample_day = index.day_ids[0] if index.day_ids else "一"
    sample_shift = index.shift_ids[0] if index.shift_ids else "早班"
    sample_emp = index.employee_ids[:2] or ["E01"]
    emps = ",".join(f'"{e}"' for e in sample_emp)
    day_labels = "、".join(index.day_label(d) for d in index.day_ids)
    shift_names = "/".join(index.shift_name(s) for s in index.shift_ids)
    return f"""你是排班表识别器。图中是一张门店排班表，行=日期（{day_labels}），列={shift_names}，单元格里是员工工号（形如 {sample_emp[0]}）。

只输出 JSON，不要任何解释、不要 markdown 代码块。格式：
{{"rows":[{{"day":"{sample_day}","shift":"{sample_shift}","employees":[{emps}]}}]}}

要求：
- day 只能是 {day_ids} 之一
- shift 只能是 {shift_ids} 之一
- employees 只填你在图中确实看清的工号，保持与图中一致的格式
- 看不清的格子照样输出该格，employees 填 []，不要凭常识补人
- 一共应该有 {index.slot_count()} 个格子"""


async def read_schedule_image(
    data_url: str, model: Optional[str] = None, config: ConfigLike = None
) -> tuple[List[dict], str]:
    """把排班表图片交给视觉模型读成 rows，返回 (rows, 实际使用的模型)。

    这里只做「取回模型说了什么」，工号归一、非法 token、体检全部由 importer/validator
    接手——视觉模型的输出和 CSV 的原始 token 一样不可信，必须走同一条确定性管道。

    不用 response_format=json_object：视觉模型不保证支持该参数，实测更稳的做法是
    prompt 里禁 markdown + `_extract_json` 兜底。
    """
    used = model or models_registry.DEFAULT_VISION_MODEL
    content = await _chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt(config)},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        temperature=0.0,
        json_mode=False,
        # 夹取在 _chat 里做，这里写理想值：glm-4v-flash 会被压到 1024
        max_tokens=2048,
        model=used,
        timeout=GLM_VISION_TIMEOUT,
    )
    raw = _extract_json(content)
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        raise ValueError("视觉模型未返回可解析的 JSON")
    return [r for r in raw["rows"] if isinstance(r, dict)], used


async def health() -> dict:
    if not llm_enabled():
        return {"configured": False, "reachable": False, "model": GLM_MODEL}
    try:
        await _chat([{"role": "user", "content": "回复 ok"}], temperature=0, json_mode=False, max_tokens=8)
        return {"configured": True, "reachable": True, "model": GLM_MODEL}
    except Exception as exc:
        return {"configured": True, "reachable": False, "model": GLM_MODEL, "error": type(exc).__name__}
