/**
 * 后端 API 契约类型定义。
 * 字段名严格对齐 `GET /api/meta`、`GET /api/models`、`POST /api/generate`、
 * `POST /api/validate`、`POST /api/import`、`GET /api/scenarios`。
 * 请勿重命名字段，前端内部派生状态请使用本文件末尾的「视图层类型」。
 */

export type DayKey = '一' | '二' | '三' | '四' | '五' | '六' | '日';
export type ShiftKey = '早班' | '晚班';

/* ================ 配置化契约（docs/CONFIG_CONTRACT.md 第 4 / 5 节） ================ */

/**
 * 整份排班配置。存 localStorage 并随每次请求传给后端（后端保持无状态，契约 3.3）。
 * `version` 是迁移锚点：将来字段有破坏性变更时靠它决定丢弃还是升级旧数据。
 */
export interface SchedulerConfig {
  version: 1;
  scenario: ScenarioDef;
  /** 技能字典，员工技能只能从这里选（R-09 的配置化形态） */
  skill_pool: string[];
  role_pool: string[];
  employees: EmployeeDef[];
  rules: RuleDef[];
}

export interface ScenarioDef {
  name: string;
  /** 1..14 天 */
  days: DayDef[];
  /** 1..4 班 */
  shifts: ShiftDef[];
}

export interface DayDef {
  /** "d1"，内部标识，不展示给用户 */
  id: string;
  label: string;
  /** 高峰日。原「周末加人」的配置化形态 */
  peak: boolean;
}

export interface ShiftDef {
  id: string;
  name: string;
  start: string;
  /** end <= start 视为跨夜到次日 */
  end: string;
  /** 按 start/end 回算，界面只读展示 */
  hours: number;
}

export interface EmployeeDef {
  id: string;
  name: string;
  role: string;
  skills: string[];
  unavailable: UnavailableSlot[];
  /** null = 用全局规则 */
  max_shifts: number | null;
  /** 软偏好，shift id */
  preferred_shifts: string[];
  /** 停用不删除：保留历史排班可读 */
  active: boolean;
}

export interface UnavailableSlot {
  day: string;
  /** null = 当天整日不可用 */
  shift: string | null;
  /**
   * 契约的数据模型里没有这一位，但后端会回传：空 = 结构性不可用（兼职只做周末），
   * 非空 = 具名原因（请假 / 培训）。前端只读不写，用于换人面板解释「为什么这天不能上」。
   */
  reason?: string | null;
}

export type RuleType =
  | 'min_staff_per_shift'
  | 'require_attribute'
  | 'max_shifts_per_period'
  | 'max_consecutive_days'
  | 'min_rest_hours'
  | 'one_shift_per_day'
  | 'respect_unavailability'
  | 'skill_source_integrity';

export interface MinStaffOverride {
  day: string;
  shift: string;
  min: number;
}

export interface MinStaffParams {
  default: number;
  peak: number;
  overrides: MinStaffOverride[];
}

export interface RequireAttributeParams {
  attr: 'skill' | 'role';
  value: string;
  min: number;
}

export interface MaxShiftsParams {
  max: number;
}

export interface MaxConsecutiveParams {
  max: number;
}

export interface MinRestParams {
  hours: number;
}

/**
 * 规则参数按 type 取并集而不是做泛型联合：联合类型会让「切换 type 时暂时不合法的参数」
 * 在编辑过程中被 TS 拒绝，而参数表单本身就需要经历中间态。运行期合法性由
 * config/checks.ts 与 `/api/config/validate` 保证。
 */
export type RuleParams = Partial<
  MinStaffParams & RequireAttributeParams & MaxShiftsParams & MaxConsecutiveParams & MinRestParams
>;

export interface RuleDef {
  /** "R-01" 保留可读编号，用户自建的用 "C-1" */
  id: string;
  type: RuleType;
  name: string;
  enabled: boolean;
  /** true = 系统内建，不可删除 / 禁用 */
  locked: boolean;
  params: RuleParams;
}

/**
 * 一条自检结论指向哪里。跨格子的问题（如总人次不足）不带 where。
 *
 * 员工与规则各有两种拼法：真实后端发的是 `employee_id` / `rule_id`（见 config_check 的 `_where`），
 * 而契约文档的示例只写了 day/shift。两种都收，读的时候统一走 `whereEmployee` / `whereRule`——
 * 认错一个 key 的后果不是报错，而是「去处理」按钮悄悄消失，最难查。
 */
export interface ConfigIssueWhere {
  day?: string;
  shift?: string;
  employee_id?: string;
  employee?: string;
  rule_id?: string;
  rule?: string;
}

export function whereEmployee(where?: ConfigIssueWhere | null): string | null {
  return where?.employee_id ?? where?.employee ?? null;
}

export function whereRule(where?: ConfigIssueWhere | null): string | null {
  return where?.rule_id ?? where?.rule ?? null;
}

/**
 * errors / warnings 同构。`code` 是契约里的枚举，但前端**不能**依赖它做穷举渲染——
 * 后端随时可能加新 code，界面只用 message + fix 说话，code 只用于图标、定位与埋点。
 */
export interface ConfigIssue {
  code: string;
  message: string;
  where?: ConfigIssueWhere | null;
  fix?: string;
}

export interface CapacitySlot {
  day: string;
  shift: string;
  min_required: number;
  /** 该格「可排班人数」：启用且当格未被标为不可用的员工数 */
  eligible: number;
}

export interface Capacity {
  demand_person_shifts: number;
  supply_person_shifts: number;
  headroom_pct: number;
  per_slot: CapacitySlot[];
}

/** POST /api/config/validate 的响应；`/api/generate` 返回 400 时 body 与此同构 */
export interface ConfigValidateResponse {
  ok: boolean;
  errors: ConfigIssue[];
  warnings: ConfigIssue[];
  capacity: Capacity | null;
}

/** GET /api/config/default */
export interface DefaultConfigResponse {
  config: SchedulerConfig;
}

/**
 * `/api/generate` 的场景回显，让看板按实际维度渲染而不必自己推。
 * 注意 shift 这里给的是 `time_label`（展示用），不是 start/end。
 */
export interface ScenarioEcho {
  days: Array<{ id: string; label: string; peak: boolean }>;
  shifts: Array<{ id: string; name: string; time_label: string }>;
}

/* ---------------- GET /api/meta ---------------- */

/**
 * 看板 / 换人面板消费的员工视图。
 *
 * 配置化之后这个结构有两个来源：老的 `GET /api/meta` 和由 `SchedulerConfig.employees`
 * 投影出来的 meta（见 config/derive.ts）。新增字段一律可选，这样老后端的响应
 * 仍然能直接喂给同一批组件，不必为「有配置 / 没配置」各写一套渲染。
 */
export interface Employee {
  id: string;
  role: string;
  skills: string[];
  available_days: string[];
  leave_days: string[];
  preference: string;
  /** 配置化后新增的姓名；老 `/api/meta` 没有这个字段，所以只在 tooltip 里补充展示 */
  name?: string;
  /** 精确到班次的不可用（老 meta 只能表达到「整天」），换人候选过滤优先用它 */
  unavailable?: UnavailableSlot[];
  /** 个人班次上限，null = 跟随全局规则 */
  max_shifts?: number | null;
  /** 停用员工不参与排班，但保留在档案里，历史排班才读得懂 */
  active?: boolean;
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
  /** 单次模型调用的探测均值 */
  avg_latency_s?: number;
  /** 线上一整次 /api/generate 的实测耗时。一次生成跑两趟 LLM，所以接近单次的两倍 */
  e2e_latency_s?: number;
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
  /**
   * 所选模型本次超时、后端自动改用更快模型时，这里是原本要用的那个模型 id。
   * 有值说明「你选的模型没生效」——必须显式告知，否则用户会按所选模型的质量预期去信任结果。
   */
  model_fallback_from?: string | null;
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
  /** 省略 → 后端用默认配置，行为与配置化改造前一致（契约 5.3 的向后兼容口子） */
  config?: SchedulerConfig | null;
}

export interface GenerateResponse {
  ok: boolean;
  mode: 'generate' | 'adjust' | 'clarify' | string;
  intent: Intent;
  solution: Solution | null;
  /**
   * `solution === null`（无解态 / 澄清态）时后端不跑校验器，这里回 `null`。
   * 不要把它当成「0 条通过」——那会在界面上把一次「没排出来」渲染成一片红叉，
   * 让店长以为是自己的排班违规。UI 一律走中性的「待排班」态（见 ValidationPanel）。
   */
  validation: Validation | null;
  explanation: Explanation;
  infeasible: Infeasible | null;
  clarification: Clarification | null;
  diff: Diff | null;
  timing: Timing;
  /**
   * 本次求解实际使用的维度回显。可选是为了兼容尚未上线配置化的后端——
   * 缺失时看板退回按本地配置渲染（见 config/derive.ts 的 metaFromConfig）。
   */
  scenario?: ScenarioEcho | null;
}

/* ---------------- POST /api/validate ---------------- */

export interface ValidateRequest {
  slots: Slot[];
  config?: SchedulerConfig | null;
}

export interface ValidateResponse {
  validation: Validation;
  soft_metrics: SoftMetrics;
}

/* ---------------- POST /api/candidates（契约 5.5） ---------------- */

export interface CandidatesRequest {
  day: string;
  shift: string;
  /** 该格已排的人，后端从候选里剔除 */
  taken: string[];
  /** 省略 → 后端用默认配置，结果与 `GET /api/candidates` 一致 */
  config?: SchedulerConfig | null;
}

/**
 * 候选员工 = `EmployeeDef` 全字段 + 三个派生的兼容字段。
 *
 * 契约明确「不存在两种员工形状」：新界面一律读 EmployeeDef 那一半（`unavailable` /
 * `preferred_shifts` / `max_shifts` 都是 id 与数字，可直接参与判断），
 * `available_days` / `leave_days` / `preference` 只是给老前端看的折算视图——
 * 它们是标签串、会丢失「精确到班次」的信息，新代码里不要再依赖。
 */
export interface CandidateEmployee extends EmployeeDef {
  available_days: string[];
  leave_days: string[];
  preference: string | null;
}

export interface CandidatesResponse {
  day: string;
  shift: string;
  candidates: CandidateEmployee[];
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
