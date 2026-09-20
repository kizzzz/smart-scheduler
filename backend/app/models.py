"""Pydantic 数据模型：意图、排班表、校验报告、诊断、API 出入参。"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------- L1 意图解析产物 ----------


class TempLeave(BaseModel):
    """临时请假：某员工某天不可排班（叠加在题面固有请假之上）。"""
    employee_id: str
    days: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class Pin(BaseModel):
    """锁定：要求某员工某天某班必须在岗。"""
    employee_id: str
    day: str
    shift: str


class Forbid(BaseModel):
    """禁止：要求某员工某天某班不得在岗。"""
    employee_id: str
    day: str
    shift: Optional[str] = None


class SoftPreference(BaseModel):
    kind: Literal["respect_shift_preference", "balance_workload", "protect_part_time_weekend"]
    weight: float = 1.0


class GuardrailStats(BaseModel):
    """反幻觉护栏的拦截统计。

    存在的意义不是做报表，而是让架构价值可见：实测 glm-4-flash 在
    「E05 周六请假，周末早班多留一个收银」上会额外编一条 E06 的固定排班，
    在「帮我排下周的班」上会编出 E001/E002 这类非法工号的 pins。
    这些约束会悄悄改变排班结果，店长几乎不可能自己发现，所以拦截数必须能被看到。
    """
    triggered: bool = False
    dropped_pins: int = 0
    dropped_forbids: int = 0
    dropped_excludes: int = 0
    dropped_min_staff: int = 0
    # 幻觉请假很罕见（说请假总会带上人），契约里没有对应计数位，只并入 summary 与 triggered
    dropped_leaves: int = 0
    invalid_employee_ids: List[str] = Field(default_factory=list)
    summary: str = ""


class ScheduleRequest(BaseModel):
    """LLM 意图解析的结构化输出，也是求解器的唯一输入。"""
    action: Literal["generate", "adjust", "unknown"] = "generate"
    temp_leaves: List[TempLeave] = Field(default_factory=list)
    pins: List[Pin] = Field(default_factory=list)
    forbids: List[Forbid] = Field(default_factory=list)
    exclude_employees: List[str] = Field(default_factory=list)
    soft_preferences: List[SoftPreference] = Field(default_factory=list)
    min_staff_override: Dict[str, int] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    clarification_needed: List[str] = Field(default_factory=list)
    raw_text: str = ""
    parse_source: Literal["llm", "fallback_rule", "structured"] = "llm"
    parse_confidence: float = 1.0
    # 降级到规则解析时为 None：那条路径根本没有模型参与，报一个模型名是误导
    model_used: Optional[str] = None
    # 所选模型超时后自动换用了更快的模型时，记下原本要用的那个。
    # 不记的话用户会以为自己选的模型一直在生效，而实际解析质量已经变了。
    model_fallback_from: Optional[str] = None
    guardrail: GuardrailStats = Field(default_factory=GuardrailStats)


# ---------- 排班表 ----------


class Slot(BaseModel):
    day: str
    shift: str
    employee_ids: List[str] = Field(default_factory=list)


class Schedule(BaseModel):
    slots: List[Slot] = Field(default_factory=list)

    def as_map(self) -> Dict[str, List[str]]:
        return {f"{s.day}|{s.shift}": list(s.employee_ids) for s in self.slots}


# ---------- L3 校验报告 ----------


class Violation(BaseModel):
    rule_id: str
    rule_text: str
    day: Optional[str] = None
    shift: Optional[str] = None
    employee_id: Optional[str] = None
    detail: str


class RuleResult(BaseModel):
    rule_id: str
    rule_text: str
    passed: bool
    violation_count: int = 0


class SoftMetrics(BaseModel):
    preference_hit_rate: float = 0.0
    preference_hit: int = 0
    preference_total: int = 0
    workload_min: int = 0
    workload_max: int = 0
    workload_stdev: float = 0.0
    part_time_weekend_ratio: float = 0.0
    total_assignments: int = 0


class ValidationReport(BaseModel):
    passed: bool
    violations: List[Violation] = Field(default_factory=list)
    rule_results: List[RuleResult] = Field(default_factory=list)
    per_employee_shifts: Dict[str, int] = Field(default_factory=dict)
    soft_metrics: SoftMetrics = SoftMetrics()


# ---------- 无解诊断 ----------


class UnlockOption(BaseModel):
    title: str
    detail: str
    cost: str


class Diagnosis(BaseModel):
    infeasible: bool = False
    kind: Literal["proven_infeasible", "search_timeout", "none"] = "none"
    bottleneck_rule: Optional[str] = None
    bottleneck_slots: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    unlock_options: List[UnlockOption] = Field(default_factory=list)


# ---------- 排班表导入 ----------


class UnresolvedToken(BaseModel):
    """无法归一为合法工号的原始 token。

    只记录、不猜测：员工档案里没有姓名字段，把「小王」映射成某个工号就是编造数据。
    """
    raw: str
    where: str
    reason: str


class ImportExtraction(BaseModel):
    """导入解析的中间产物：只负责「读出了什么」，合规与否交给 validator。"""
    ok: bool = False
    # .tsv 也归到 csv：契约给出的取值只有 csv / excel / image，多造一个值会让前端的
    # 文案映射表落空，而「分隔符是什么」对使用者没有任何意义
    source: Literal["csv", "excel", "image"]
    extractor: Literal["deterministic", "vision_llm"]
    layout: Literal["long", "matrix", "image", "unknown"] = "unknown"
    model_used: Optional[str] = None
    slots: List[Slot] = Field(default_factory=list)
    assignments: int = 0
    resolved: int = 0
    unresolved: List[UnresolvedToken] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_confirmation: bool = True


# ---------- API ----------


class GenerateRequest(BaseModel):
    text: str = ""
    base_schedule: Optional[Schedule] = None
    intent_override: Optional[ScheduleRequest] = None
    explain: bool = True


class GenerateResponse(BaseModel):
    status: Literal["ok", "infeasible", "needs_clarification"]
    intent: ScheduleRequest
    schedule: Optional[Schedule] = None
    validation: Optional[ValidationReport] = None
    diagnosis: Diagnosis = Diagnosis()
    explanation: str = ""
    explanation_source: Literal["llm", "template", "none"] = "none"
    solver_trace: List[str] = Field(default_factory=list)
    changed_slots: List[str] = Field(default_factory=list)
    timings_ms: Dict[str, int] = Field(default_factory=dict)


class ValidateRequest(BaseModel):
    schedule: Schedule
    intent: Optional[ScheduleRequest] = None


class ValidateResponse(BaseModel):
    validation: ValidationReport
