/**
 * 后端 API 契约类型定义。
 * 字段名严格对齐 `GET /api/meta`、`GET /api/models`、`POST /api/generate`、
 * `POST /api/validate`、`POST /api/import`、`GET /api/scenarios`。
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

/* ---------------- GET /api/models ---------------- */

/**
 * 实测数据。后端只在真的探测过时才给这个字段，所以每一项都是可选的：
 * 文本模型给 `hallucinated_cases`，视觉模型给 `cell_accuracy`。
 */
export interface ModelMeasured {
  avg_latency_s?: number;
  hallucinated_cases?: string;
  cell_accuracy?: string;
  probed_at?: string;
}

/** `latency_hint` / `accuracy_hint` / `note` 由后端负责措辞，前端只展示不拼装 */
export interface ModelOption {
  id: string;
  label: string;
  tier: 'free' | 'paid' | string;
  is_default: boolean;
  latency_hint: string;
  accuracy_hint: string;
  note: string;
  max_output_tokens?: number;
  measured?: ModelMeasured;
}

/** 探测到但当前 Key 不可用的模型：灰显 + 给出 reason，比直接隐藏更能解释「为什么不能选」 */
export interface LockedModel {
  id: string;
  reason: string;
}

export interface ModelCatalog {
  default_text: string;
  default_vision: string;
  text: ModelOption[];
  vision: ModelOption[];
  locked: LockedModel[];
  boundary_note: string;
  unlock_note: string;
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

/**
 * 反幻觉护栏的拦截结果。`triggered=false` 时各计数为 0、`summary` 为空串。
 * summary 由后端生成中文，前端直接显示——这是「LLM 会编造但被确定性护栏拦住」的可见证据。
 */
export interface Guardrail {
  triggered: boolean;
  dropped_pins: number;
  dropped_forbids: number;
  dropped_excludes: number;
  dropped_min_staff: number;
  invalid_employee_ids: string[];
  summary: string;
}

export interface Intent {
  operation: string;
  period_label: string;
  objective_label: string;
  chips: IntentChip[];
  temp_constraints: TempConstraint[];
  degraded: boolean;
  degrade_reason: string | null;
  /**
   * 本次实际使用的文本模型；降级到规则解析时为 `null`。
   * 声明为可选是为了兼容尚未升级到 v1.1 的线上后端，避免旧响应把界面打空。
   */
  model_used?: string | null;
  guardrail?: Guardrail | null;
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
  /** 导入链路耗时，generate 恒为 0；可选是为兼容未升级到 v1.1 的线上后端 */
  import_ms?: number;
  total_ms: number;
}

export interface GenerateRequest {
  instruction: string;
  base_slots: Slot[] | null;
  /** 省略或 null 时后端使用 default_text；不在白名单会返回 400 */
  model?: string | null;
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

/* ---------------- POST /api/import ---------------- */

/**
 * 导入解析出的格子。契约只保证 day / shift / employees 三个字段，
 * 看板需要的 day_label / shift_time / min_required 由前端按 `/api/meta` 补全
 * （见 lib/schedule.ts 的 normalizeImportedSlots），所以这里声明为可选。
 */
export interface ImportedSlot {
  day: string;
  shift: string;
  employees: string[];
  day_label?: string;
  shift_time?: string;
  min_required?: number;
}

export interface ImportStats {
  slots_found: number;
  slots_expected: number;
  assignments: number;
  resolved: number;
  unresolved: number;
}

/** 无法归一为合法工号的 token：只报不猜（员工档案没有姓名字段） */
export interface UnresolvedItem {
  raw: string;
  where: string;
  reason: string;
}

export interface ImportTiming {
  extract_ms: number;
  validate_ms: number;
  total_ms: number;
}

export interface ImportResponse {
  /** 完全读不出班次时后端仍返回 HTTP 200，用 ok=false + warnings 说明原因 */
  ok: boolean;
  source: 'csv' | 'excel' | 'image' | string;
  /** deterministic：CSV/Excel 不过 LLM；vision_llm：图片识别，结果需人工核对 */
  extractor: 'deterministic' | 'vision_llm' | string;
  model_used: string | null;
  layout: 'long' | 'matrix' | 'image' | string;
  slots: ImportedSlot[];
  /** 解析失败（ok=false）时后端可能不给统计与体检结果，故为可选 */
  stats?: ImportStats;
  unresolved?: UnresolvedItem[];
  warnings?: string[];
  confidence: number;
  requires_confirmation: boolean;
  validation?: Validation | null;
  soft_metrics?: SoftMetrics | null;
  timing_ms?: ImportTiming;
}

/* ---------------- 视图层类型 ---------------- */

/** 求解进度阶段，用于加载态轮换文案 */
export type SolvePhase = 'parse' | 'solve' | 'validate' | 'explain';

/**
 * 导入文件的体感分类：表格是毫秒级、图片要走视觉模型约 10 秒，
 * 两者的等待体验差一个数量级，loading 文案必须区分。
 */
export type ImportKind = 'sheet' | 'image';

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
