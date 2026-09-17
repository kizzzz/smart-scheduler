"""GLM 接入层：L1 意图解析 与 L4 解释生成。

两条硬性边界：
1. LLM 只在输入端（理解自然语言）和输出端（解释已校验结果）出现，绝不参与排班正确性判定；
2. 任何一端失败都必须降级，不允许因模型不可用导致整体不可用。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from .data import DAYS, EMPLOYEE_IDS, EMPLOYEES, RULES, SHIFTS
from .models import (
    Diagnosis,
    Schedule,
    ScheduleRequest,
    ValidationReport,
)

GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.getenv("GLM_MODEL", "glm-4-flash")
GLM_TIMEOUT = float(os.getenv("GLM_TIMEOUT", "12"))
# 只认显式配置的代理：不继承环境变量，避免宿主机 NO_PROXY/CIDR 之类的脏配置把出网打挂
GLM_PROXY = os.getenv("GLM_PROXY") or None
GLM_RETRIES = int(os.getenv("GLM_RETRIES", "2"))


def api_key() -> str:
    return os.getenv("GLM_API_KEY", "").strip()


def llm_enabled() -> bool:
    return bool(api_key())


async def _chat(messages: List[Dict[str, str]], *, temperature: float, json_mode: bool, max_tokens: int) -> str:
    payload: Dict[str, Any] = {
        "model": GLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    last: Exception | None = None
    for attempt in range(GLM_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=GLM_TIMEOUT, trust_env=False, proxy=GLM_PROXY) as client:
                r = await client.post(f"{GLM_BASE_URL}/chat/completions", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            return data["choices"][0]["message"]["content"] or ""
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # 5xx 与网络抖动可重试；4xx（鉴权/参数错误）直接失败，重试没有意义
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            last = exc
            if attempt < GLM_RETRIES:
                await asyncio.sleep(0.6 * (attempt + 1))
    raise last if last else RuntimeError("GLM 调用失败")


# ---------- L1 意图解析 ----------

_EMP_LINES = "\n".join(
    f"- {e.id}｜{e.role}｜技能 {'/'.join(sorted(e.skills))}｜可工作 周{'、周'.join(e.available_days)}"
    + (f"｜固有请假 周{'、周'.join(e.leave_days)}" if e.leave_days else "")
    for e in EMPLOYEES.values()
)

_INTENT_SYSTEM = f"""你是排班系统的意图解析器。你的唯一职责是把店长的自然语言指令翻译成 JSON，**不要排班、不要判断规则是否满足、不要给建议**。

可用员工（只能使用这些 ID）：
{_EMP_LINES}

日期只能是：{"、".join(DAYS)}（分别代表周一到周日）
班次只能是：早班、晚班

输出 JSON，字段如下（不确定的字段就省略）：
{{
  "action": "generate | adjust | unknown",
  "temp_leaves": [{{"employee_id": "E06", "days": ["三"], "reason": "临时请假"}}],
  "pins": [{{"employee_id": "E01", "day": "六", "shift": "早班"}}],
  "forbids": [{{"employee_id": "E11", "day": "日", "shift": "晚班"}}],
  "exclude_employees": ["E15"],
  "soft_preferences": [{{"kind": "respect_shift_preference | balance_workload | protect_part_time_weekend", "weight": 1.0}}],
  "min_staff_override": {{"六|晚班": 7}},
  "notes": ["把原话中的关键约束复述成一句话，便于店长确认"],
  "clarification_needed": []
}}

判定规则：
- 只是"排一周班""生成排班"→ action=generate。
- 在已有排班基础上改动（某人请假、某人必须上某班、某班加人）→ action=adjust。
- 出现下列任一情况，必须 action=unknown，并在 clarification_needed 写清要问店长什么：
  指代的人无法唯一对应到上面的员工 ID；日期或班次含糊（如"周末某天""下周"）；指令自相矛盾；要求降低人数下限或放宽硬规则。
- min_staff_override 只能用于**提高**人数要求，绝不能降低。值 = 该班次的目标总人数；基线是工作日每班 4 人、周末每班 6 人，所以"周日晚班多留一个人"应写 {{"日|晚班": 7}}。
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


_VALID_DAYS = set(DAYS)
_VALID_SHIFTS = set(SHIFTS)


def _sanitize(raw: dict, text: str, source: str) -> ScheduleRequest:
    """对模型输出做确定性清洗：非法员工/日期/班次一律丢弃并转为澄清项。"""
    problems: List[str] = []

    def ok_emp(eid: Any) -> bool:
        if isinstance(eid, str) and eid in EMPLOYEE_IDS:
            return True
        problems.append(f"指令中的「{eid}」无法对应到员工数据中的任何 ID，请确认具体是谁")
        return False

    def ok_day(d: Any) -> bool:
        return isinstance(d, str) and d in _VALID_DAYS

    temp_leaves = []
    for tl in raw.get("temp_leaves") or []:
        if not isinstance(tl, dict) or not ok_emp(tl.get("employee_id")):
            continue
        days = [d for d in (tl.get("days") or []) if ok_day(d)]
        if not days:
            problems.append(f"{tl.get('employee_id')} 的请假日期不明确，请说明是周几")
            continue
        temp_leaves.append({"employee_id": tl["employee_id"], "days": days, "reason": tl.get("reason")})

    pins = []
    for p in raw.get("pins") or []:
        if not isinstance(p, dict) or not ok_emp(p.get("employee_id")):
            continue
        if ok_day(p.get("day")) and p.get("shift") in _VALID_SHIFTS:
            pins.append({"employee_id": p["employee_id"], "day": p["day"], "shift": p["shift"]})
        else:
            problems.append(f"{p.get('employee_id')} 要固定到哪一天的哪个班次不明确")

    forbids = []
    for f in raw.get("forbids") or []:
        if not isinstance(f, dict) or not ok_emp(f.get("employee_id")):
            continue
        if ok_day(f.get("day")):
            sh = f.get("shift") if f.get("shift") in _VALID_SHIFTS else None
            forbids.append({"employee_id": f["employee_id"], "day": f["day"], "shift": sh})

    excludes = [e for e in (raw.get("exclude_employees") or []) if isinstance(e, str) and e in EMPLOYEE_IDS]

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
        if iv > 0 and (k in _VALID_DAYS or k == "all" or ("|" in k and k.split("|")[0] in _VALID_DAYS)):
            overrides[k] = iv

    clar = [c for c in (raw.get("clarification_needed") or []) if isinstance(c, str) and c.strip()]
    clar.extend(problems)
    action = raw.get("action") if raw.get("action") in {"generate", "adjust", "unknown"} else "generate"
    if clar:
        action = "unknown"

    return ScheduleRequest(
        action=action,  # type: ignore[arg-type]
        temp_leaves=temp_leaves,  # type: ignore[arg-type]
        pins=pins,  # type: ignore[arg-type]
        forbids=forbids,  # type: ignore[arg-type]
        exclude_employees=excludes,
        soft_preferences=prefs,  # type: ignore[arg-type]
        min_staff_override=overrides,
        notes=[n for n in (raw.get("notes") or []) if isinstance(n, str)][:4],
        clarification_needed=list(dict.fromkeys(clar))[:4],
        raw_text=text,
        parse_source=source,  # type: ignore[arg-type]
        parse_confidence=0.9 if source == "llm" else 0.5,
    )


_LEAVE_WORDS = ("请假", "休假", "不能来", "来不了", "有事", "生病", "病了")
_PLAIN_GEN_WORDS = ("排班", "排一周", "排下周", "生成", "排个班", "排班表", "重排", "安排")
_PERSON_HINT = ("小", "老", "他", "她", "某人", "那个", "同事")


def fallback_parse(text: str) -> ScheduleRequest:
    """LLM 不可用时的规则兜底：只认「员工ID + 周几 + 请假」这类高置信模式。

    设计原则：兜底可以「少识别约束」，但不能把一句普通的「排个班」变成澄清追问。
    """
    raw: Dict[str, Any] = {"action": "generate", "temp_leaves": [], "notes": [], "clarification_needed": []}
    if not text.strip():
        return _sanitize(raw, text, "fallback_rule")

    emps = re.findall(r"E\d{2}", text.upper())
    days = re.findall(r"周([一二三四五六日])", text)
    whole_week = any(w in text for w in ("整周", "全周", "这周都", "一周都", "本周都"))
    unavailable = any(w in text for w in ("不排", "不要排", "别排", "不可用", "出差", "培训", "休假", "请假"))
    if emps and whole_week and unavailable:
        raw["action"] = "adjust"
        raw["exclude_employees"] = list(dict.fromkeys(emps))
        raw["notes"] = [f"{'、'.join(dict.fromkeys(emps))} 整周不可排班"]
    elif emps and days and any(w in text for w in _LEAVE_WORDS):
        raw["action"] = "adjust"
        raw["temp_leaves"] = [{"employee_id": emps[0], "days": list(dict.fromkeys(days)), "reason": "临时请假"}]
        raw["notes"] = [f"{emps[0]} 周{'、周'.join(dict.fromkeys(days))}不可排班"]
    elif emps and days and ("必须" in text or "固定" in text or "盯" in text):
        shift = "早班" if "早班" in text else ("晚班" if "晚班" in text else None)
        if shift:
            raw["action"] = "adjust"
            raw["pins"] = [{"employee_id": emps[0], "day": days[0], "shift": shift}]
            raw["notes"] = [f"{emps[0]} 固定在周{days[0]}{shift}"]
        else:
            raw["clarification_needed"] = [f"{emps[0]} 要固定到周{days[0]}的早班还是晚班？"]
    elif not emps and (any(w in text for w in _LEAVE_WORDS) or any(w in text for w in _PERSON_HINT)):
        # 提到了人或请假，却没有可识别的员工 ID —— 这才是真正需要澄清的情况
        raw["clarification_needed"] = ["指令里的员工无法唯一确定，请用员工编号（如 E06）说明是谁"]
    elif any(w in text for w in _PLAIN_GEN_WORDS):
        raw["notes"] = ["语言模型当前不可用，已按 9 条硬规则生成基础排班，未识别指令中的额外约束"]
    else:
        raw["notes"] = ["未识别到额外约束，已生成基础排班；如需特殊要求，请用「E06 周四请假」这类明确表述"]
    return _sanitize(raw, text, "fallback_rule")


async def parse_intent(text: str) -> ScheduleRequest:
    if not text.strip():
        return ScheduleRequest(action="generate", raw_text="", parse_source="structured", parse_confidence=1.0)
    if not llm_enabled():
        return fallback_parse(text)
    try:
        content = await _chat(
            [{"role": "system", "content": _INTENT_SYSTEM}, {"role": "user", "content": text}],
            temperature=0.05,
            json_mode=True,
            max_tokens=900,
        )
        raw = _extract_json(content)
        if raw is None:
            return fallback_parse(text)
        return _sanitize(raw, text, "llm")
    except Exception:
        return fallback_parse(text)


# ---------- L4 解释生成 ----------

_RULE_LINES = "\n".join(f"{r['id']}：{r['text']}" for r in RULES)

_EXPLAIN_SYSTEM = f"""你是排班助手的解释生成器，读者是门店店长。

硬规则清单（供你引用编号，不要改写含义）：
{_RULE_LINES}

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
) -> dict:
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
        out["keeper_coverage"] = {
            f"周{s.day}{s.shift}": [e for e in s.employee_ids if "店长值守" in EMPLOYEES[e].skills]
            for s in schedule.slots
        }
    return out


def template_explanation(
    report: Optional[ValidationReport], intent: ScheduleRequest, diagnosis: Diagnosis
) -> str:
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
        lines.append("- 9 条硬规则全部通过，本方案可直接使用")
    else:
        lines.append(f"- 存在 {len(report.violations)} 处硬规则违规，需修改后才能使用")
        for v in report.violations[:3]:
            lines.append(f"- {v.rule_id}：{v.detail}")
    m = report.soft_metrics
    lines.append(f"- 班次偏好满足 {m.preference_hit}/{m.preference_total}，人均班次 {m.workload_min}–{m.workload_max} 个")
    if intent.temp_leaves:
        who = "、".join(f"{t.employee_id} 周{'、周'.join(t.days)}" for t in intent.temp_leaves[:2])
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
) -> tuple[str, str]:
    fallback = template_explanation(report, intent, diagnosis)
    if not llm_enabled():
        return fallback, "template"
    try:
        content = await _chat(
            [
                {"role": "system", "content": _EXPLAIN_SYSTEM},
                {"role": "user", "content": json.dumps(_facts(schedule, report, intent, diagnosis), ensure_ascii=False)},
            ],
            temperature=0.3,
            json_mode=False,
            max_tokens=600,
        )
        cleaned = _clean_explanation(content, report, diagnosis)
        if cleaned is None:
            return fallback, "template"
        return cleaned, "llm"
    except Exception:
        return fallback, "template"


async def health() -> dict:
    if not llm_enabled():
        return {"configured": False, "reachable": False, "model": GLM_MODEL}
    try:
        await _chat([{"role": "user", "content": "回复 ok"}], temperature=0, json_mode=False, max_tokens=8)
        return {"configured": True, "reachable": True, "model": GLM_MODEL}
    except Exception as exc:
        return {"configured": True, "reachable": False, "model": GLM_MODEL, "error": type(exc).__name__}
