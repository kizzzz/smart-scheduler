/**
 * 后端 API 契约类型定义。
 * 字段名严格对齐 `GET /api/meta`、`POST /api/generate`、`POST /api/validate`、`GET /api/scenarios`。
 * 请勿重命名字段，前端内部派生状态请使用本文件末尾的「视图层类型」。
 */

export type DayKey = '一' | '二' | '三' | '四' | '五' | '六' | '日';
export type ShiftKey = '早班' | '晚班';

/* ---------------- GET /api/meta ---------------- */

export interface Employee {
  id: string;
  role: string;
  skills: string[];
  available_days: string[];
  leave_days: string[];
  preference: string;
}

export interface RuleMeta {
  id: string;
  text: string;
}

export interface DayMeta {
  key: string;
  label: string;
  is_weekend: boolean;
  min_required: number;
}

export interface ShiftMeta {
  key: string;
  label: string;
  time: string;
}

export interface Meta {
  employees: Employee[];
  rules: RuleMeta[];
  days: DayMeta[];
  shifts: ShiftMeta[];
}

/* ---------------- GET /api/scenarios ---------------- */

export interface Scenario {
  id: string;
  title: string;
  description: string;
  instruction: string;
  base_required: boolean;
}

/* ---------------- POST /api/generate ---------------- */

export interface IntentChip {
  key: string;
  label: string;
  value: string;
  editable: boolean;
}

export interface TempConstraint {
  employee_id: string;
  days: string[];
  type: string;
  raw: string;
}

export interface Intent {
  operation: string;
  period_label: string;
  objective_label: string;
  chips: IntentChip[];
  temp_constraints: TempConstraint[];
  degraded: boolean;
  degrade_reason: string | null;
}

export interface Slot {
  day: string;
  day_label: string;
  shift: string;
  shift_time: string;
  min_required: number;
  employees: string[];
}

export interface SoftMetrics {
  preference_rate: number;
  balance_score: number;
  skill_redundancy: number;
}

export interface Solution {
  found: boolean;
  slots: Slot[];
  soft_metrics: SoftMetrics;
  restarts_used: number;
}

export interface Suggestion {
  label: string;
  day: string;
  shift: string;
  remove: string | null;
  add: string | null;
}

export interface Violation {
  day: string;
  shift: string;
  employees: string[];
  message: string;
  suggestions: Suggestion[];
}

export interface RuleResult {
  id: string;
  text: string;
  passed: boolean;
  violations: Violation[];
}

export interface Validation {
  passed: boolean;
  violation_count: number;
  rules: RuleResult[];
}

export interface Explanation {
  bullets: string[];
  unmet_preferences: string[];
  degraded: boolean;
}

export interface UnlockPath {
  title: string;
  detail: string;
  extra_cost: string | null;
  needs_approval: boolean;
}

export interface Infeasible {
  proven: boolean;
  summary: string;
  min_conflict_set: string[];
  conflict_slot: string;
  unlock_paths: UnlockPath[];
}

export interface DiffChangedSlot {
  day: string;
  shift: string;
  added: string[];
  removed: string[];
}

export interface Diff {
  changed_count: number;
  changed_slots: DiffChangedSlot[];
  new_violations: number;
}

/** mode === 'clarify'：指令无法唯一确定时后端反问，不猜 */
export interface Clarification {
  questions: string[];
  raw: string;
}

export interface Timing {
  parse_ms: number;
  solve_ms: number;
  validate_ms: number;
  explain_ms: number;
  total_ms: number;
}

export interface GenerateRequest {
  instruction: string;
  base_slots: Slot[] | null;
}

export interface GenerateResponse {
  ok: boolean;
  mode: 'generate' | 'adjust' | 'clarify' | string;
  intent: Intent;
  solution: Solution | null;
  validation: Validation;
  explanation: Explanation;
  infeasible: Infeasible | null;
  clarification: Clarification | null;
  diff: Diff | null;
  timing: Timing;
}

/* ---------------- POST /api/validate ---------------- */

export interface ValidateRequest {
  slots: Slot[];
}

export interface ValidateResponse {
  validation: Validation;
  soft_metrics: SoftMetrics;
}

/* ---------------- 视图层类型 ---------------- */

/** 求解进度阶段，用于加载态轮换文案 */
export type SolvePhase = 'parse' | 'solve' | 'validate' | 'explain';

/** 统一错误信息：网络失败 / 5xx / 后端 message */
export interface ApiFailure {
  message: string;
  status?: number;
  detail?: string;
}

/** GET /api/health：ok 之外还会带 LLM 配置状态 */
export interface Health {
  ok: boolean;
  llm_configured?: boolean;
  model?: string;
}
