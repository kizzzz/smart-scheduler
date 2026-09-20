import type {
  Capacity,
  DayDef,
  EmployeeDef,
  Meta,
  RuleDef,
  RuleType,
  SchedulerConfig,
  ScenarioEcho,
  ShiftDef,
  Slot,
} from '../types';
import { slotKey } from '../lib/utils';

/**
 * 配置 → 视图 / 求解输入的派生层。
 *
 * 这一层存在的理由：看板、换人面板、校验面板都是按老的 `Meta`（来自 `/api/meta`）写的。
 * 与其把 5 个组件全部改成认识 `SchedulerConfig`，不如把配置投影成同一个 `Meta` 形状，
 * 组件只需要去掉「7 天 2 班」这类硬编码即可。配置维度改变时，投影结果随之改变，
 * 组件天然就是动态的。
 */

/* ---------------- 时间 ---------------- */

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/;

/** 返回分钟数；格式非法返回 null，由调用方决定报错还是兜底 */
export function parseTime(value: string): number | null {
  const m = TIME_RE.exec((value ?? '').trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/**
 * 工时。`end <= start` 视为跨夜到次日（契约 4 节），所以 22:00–06:00 是 8 小时而不是 -16。
 * 结果保留 2 位小数：半小时班次（如 09:00–17:30）必须能表达。
 */
export function shiftHours(start: string, end: string): number {
  const a = parseTime(start);
  const b = parseTime(end);
  if (a === null || b === null) return 0;
  const span = b > a ? b - a : b + 24 * 60 - a;
  return Math.round((span / 60) * 100) / 100;
}

export function timeLabel(shift: Pick<ShiftDef, 'start' | 'end'>): string {
  // 连接符沿用后端的 en dash，避免同一份数据在两处显示成不同符号
  return `${shift.start}–${shift.end}`;
}

export function isOvernight(shift: Pick<ShiftDef, 'start' | 'end'>): boolean {
  const a = parseTime(shift.start);
  const b = parseTime(shift.end);
  if (a === null || b === null) return false;
  return b <= a;
}

/** 班次在整个周期里的绝对起止分钟数，用于 min_rest_hours 这类跨天计算 */
export function absoluteSpan(dayIndex: number, shift: ShiftDef): { start: number; end: number } {
  const start = dayIndex * 24 * 60 + (parseTime(shift.start) ?? 0);
  return { start, end: start + Math.round(shiftHours(shift.start, shift.end) * 60) };
}

/* ---------------- 规则读取 ---------------- */

export function enabledRules(config: SchedulerConfig, type: RuleType): RuleDef[] {
  // locked 规则按契约恒为启用，但 UI 上仍可能存在 enabled=false 的脏数据（导入的旧 JSON），
  // 所以这里按 locked || enabled 判断，避免一份手改过的 JSON 把内建约束绕过去。
  return config.rules.filter((r) => r.type === type && (r.enabled || r.locked));
}

export function hasRule(config: SchedulerConfig, type: RuleType): boolean {
  return enabledRules(config, type).length > 0;
}

/**
 * 某一格的人数下限。
 *
 * 契约允许存在多条 `min_staff_per_shift`（模板库不限制实例数），这里取各条结论的**最大值**：
 * 硬约束叠加的语义是「都要满足」，取最大才是等价的合取。
 */
export function minRequiredFor(config: SchedulerConfig, dayId: string, shiftId: string): number {
  const day = config.scenario.days.find((d) => d.id === dayId);
  let min = 0;
  for (const rule of enabledRules(config, 'min_staff_per_shift')) {
    const p = rule.params;
    const hit = (p.overrides ?? []).find((o) => o.day === dayId && o.shift === shiftId);
    const value = hit ? hit.min : day?.peak ? (p.peak ?? p.default ?? 0) : (p.default ?? 0);
    min = Math.max(min, Number(value) || 0);
  }
  return min;
}

/** 全局班次上限（无该规则 = 不限，返回 null） */
export function globalMaxShifts(config: SchedulerConfig): number | null {
  const rules = enabledRules(config, 'max_shifts_per_period');
  if (rules.length === 0) return null;
  return Math.min(...rules.map((r) => Number(r.params.max ?? 0) || 0));
}

/** 个人上限优先于全局（契约：max_shifts=null 表示跟随全局规则） */
export function employeeCap(config: SchedulerConfig, employee: EmployeeDef): number | null {
  if (employee.max_shifts !== null && employee.max_shifts !== undefined) return employee.max_shifts;
  return globalMaxShifts(config);
}

/* ---------------- 可用性与供给 ---------------- */

export function isUnavailable(employee: EmployeeDef, dayId: string, shiftId: string): boolean {
  return employee.unavailable.some((u) => u.day === dayId && (u.shift === null || u.shift === shiftId));
}

export function activeEmployees(config: SchedulerConfig): EmployeeDef[] {
  return config.employees.filter((e) => e.active);
}

/** 某格「可排班的人」。不可用时段是 locked 规则，所以这里无条件过滤 */
export function eligibleFor(config: SchedulerConfig, dayId: string, shiftId: string): EmployeeDef[] {
  return activeEmployees(config).filter((e) => !isUnavailable(e, dayId, shiftId));
}

/**
 * 某一天「能排班的人」——只要当天还有任何一个班能上就算。
 *
 * 与 `eligibleFor` 的区别正是按天判定存在的意义：只有整日不可用才把人排除掉，
 * 这样「每人每天最多 N 个班」这类按天的上界才算得准（与后端 `_eligible_day` 同口径）。
 */
export function dayEligible(config: SchedulerConfig, dayId: string): EmployeeDef[] {
  const { shifts } = config.scenario;
  return activeEmployees(config).filter((e) => shifts.some((s) => !isUnavailable(e, dayId, s.id)));
}

export function hasAttribute(employee: EmployeeDef, attr: 'skill' | 'role', value: string): boolean {
  return attr === 'role' ? employee.role === value : employee.skills.includes(value);
}

/** 一天的供需小结。`cover` = 一个人一天最多能合法覆盖几个班 */
export interface DayCapacity {
  day: string;
  /** 当天各班人数下限之和（人次） */
  demand: number;
  /** 当天至少能上一个「有需求的班」的启用员工数（人头） */
  available: number;
  /** 一人一天最多几个班；受 one_shift_per_day、班次重叠、最小休息间隔共同限制 */
  cover: number;
  /** 当天能排出的人次上界 = available × cover */
  capacity: number;
}

/**
 * 供需总量。
 *
 * supply 不是简单的「人数 × 上限」：一个整周都不可用的员工上限再高也贡献不了人次，
 * 所以每个人的可贡献量要被他自己的可用格子数夹一次；同一天内还要再被
 * `maxShiftsPerDay`（一人一天最多几个班）夹一次，否则两班制会把「早晚连上」也算进供给。
 */
export function computeCapacity(config: SchedulerConfig): Capacity {
  const { days, shifts } = config.scenario;
  const cover = maxShiftsPerDay(config);

  const per_slot = days.flatMap((d) =>
    shifts.map((s) => ({
      day: d.id,
      shift: s.id,
      min_required: minRequiredFor(config, d.id, s.id),
      eligible: eligibleFor(config, d.id, s.id).length,
    })),
  );

  const demand = per_slot.reduce((n, s) => n + s.min_required, 0);

  let supply = 0;
  for (const e of activeEmployees(config)) {
    const reach = days.reduce((n, d) => {
      const openShifts = shifts.filter((s) => !isUnavailable(e, d.id, s.id)).length;
      return n + Math.min(cover, openShifts);
    }, 0);
    const cap = employeeCap(config, e);
    supply += cap === null ? reach : Math.min(cap, reach);
  }

  return {
    demand_person_shifts: demand,
    supply_person_shifts: supply,
    headroom_pct: demand === 0 ? 0 : Math.round(((supply - demand) / demand) * 1000) / 10,
    per_slot,
  };
}

/** 生效的最小休息间隔（多条取最严的那个）；没有该规则返回 null */
export function minRestHours(config: SchedulerConfig): number | null {
  const rules = enabledRules(config, 'min_rest_hours');
  if (rules.length === 0) return null;
  return Math.max(...rules.map((r) => Number(r.params.hours ?? 0) || 0));
}

/** 同一天两个班之间的休息间隔（小时）。时间重叠时返回负数 */
export function restGapHours(a: ShiftDef, b: ShiftDef): number {
  const spanA = absoluteSpan(0, a);
  const spanB = absoluteSpan(0, b);
  const gap = spanB.start >= spanA.end ? spanB.start - spanA.end : spanA.start - spanB.end;
  return Math.round((gap / 60) * 100) / 100;
}

/**
 * 一个人一天最多能合法覆盖几个班。
 *
 * 门槛取「数学上做得到」，而不是「`one_shift_per_day` 有没有开」：默认的 09:00–17:00 与
 * 13:00–21:00 本来就重叠，规则关着也不可能一人上两个班；反之三班制关掉规则后一天确实能上
 * 两个班。按规则开关判会既误拦又漏判，所以这里穷举班次子集（最多 4 个班，16 种），
 * 用与 R-07 同一个比较符（恰好等于阈值合规）筛出最大的相容集合。
 */
export function maxShiftsPerDay(config: SchedulerConfig): number {
  const { shifts, days } = config.scenario;
  if (hasRule(config, 'one_shift_per_day') || shifts.length === 0 || days.length === 0) return 1;
  const rest = minRestHours(config);
  let best = 1;
  for (let mask = 1; mask < 1 << shifts.length; mask += 1) {
    const chosen = shifts.filter((_s, i) => mask & (1 << i));
    let ok = true;
    for (let i = 0; ok && i < chosen.length; i += 1) {
      for (let j = i + 1; j < chosen.length; j += 1) {
        const gap = restGapHours(chosen[i], chosen[j]);
        if (gap < 0 || (rest !== null && gap < rest)) {
          ok = false;
          break;
        }
      }
    }
    if (ok) best = Math.max(best, chosen.length);
  }
  return best;
}

/**
 * 逐天的供需。
 *
 * 为什么单独算一层：「一天」是一个独立的瓶颈单位——每格都够人、总量也够，但某一天
 * 三个班加起来要 9 人次而当天只有 8 个人能来、每人一天又只能顶 1 个班，依然铁定无解。
 * 这类无解在单格视角和总量视角里都看不见，必须按天算一次，对应 error code
 * `daily_capacity_lt_demand`（刚好卡平时是 warning `no_daily_headroom`）。
 *
 * 只有当天「有需求的班」才计入可用人数：某人只能上一个下限为 0 的班时，他顶不了任何需求。
 */
export function perDayCapacity(config: SchedulerConfig): DayCapacity[] {
  const { days, shifts } = config.scenario;
  const cover = maxShiftsPerDay(config);
  return days.map((d) => {
    const demanded = shifts.filter((s) => minRequiredFor(config, d.id, s.id) > 0);
    const demand = demanded.reduce((n, s) => n + minRequiredFor(config, d.id, s.id), 0);
    const available = activeEmployees(config).filter((e) => {
      const cap = employeeCap(config, e);
      if (cap !== null && cap <= 0) return false;
      return demanded.some((s) => !isUnavailable(e, d.id, s.id));
    }).length;
    return { day: d.id, demand, available, cover, capacity: available * cover };
  });
}

/* ---------------- Meta 投影 ---------------- */

/** 规则卡片 → 校验面板里那一行人话。后端也会给 text，这里是本地兜底与 mock 用 */
export function ruleSummary(rule: RuleDef): string {
  const p = rule.params;
  switch (rule.type) {
    case 'min_staff_per_shift': {
      const base = `每班至少 ${p.default ?? 0} 人`;
      const peak = (p.peak ?? p.default) !== p.default ? `，高峰日 ${p.peak} 人` : '';
      const ov = (p.overrides ?? []).length ? `，另有 ${(p.overrides ?? []).length} 条单格例外` : '';
      return `${base}${peak}${ov}`;
    }
    case 'require_attribute':
      return `每班至少 ${p.min ?? 0} 名${p.attr === 'role' ? '岗位为' : '具备'}「${p.value ?? '—'}」${
        p.attr === 'role' ? '' : '技能'
      }的员工`;
    case 'max_shifts_per_period':
      return `每人一个周期内最多 ${p.max ?? 0} 个班`;
    case 'max_consecutive_days':
      return `任何人不得连续工作超过 ${p.max ?? 0} 天`;
    case 'min_rest_hours':
      return `相邻两个班之间至少休息 ${p.hours ?? 0} 小时`;
    case 'one_shift_per_day':
      return '每人每天最多一个班';
    case 'respect_unavailability':
      return '员工不可用时段绝不排班';
    case 'skill_source_integrity':
      return '技能仅取自员工档案，不得自行补充';
    default:
      return rule.name;
  }
}

/**
 * 配置 → Meta。两个转换要留意：
 * - `available_days` 存的是**标签**而不是 day id：它只用于 tooltip 展示，
 *   精确到班次的过滤走新增的 `unavailable` 字段。
 * - `leave_days` 恒为空：契约把「请假」与「不可用」合并成了 unavailable 一个维度。
 */
export function metaFromConfig(config: SchedulerConfig): Meta {
  const { days, shifts } = config.scenario;
  const shiftName = new Map(shifts.map((s) => [s.id, s.name]));
  return {
    employees: config.employees.map((e) => ({
      id: e.id,
      name: e.name,
      role: e.role,
      skills: [...e.skills],
      available_days: days
        .filter((d) => shifts.some((s) => !isUnavailable(e, d.id, s.id)))
        .map((d) => d.label),
      leave_days: [],
      preference: e.preferred_shifts.map((id) => shiftName.get(id) ?? id).join(' / '),
      unavailable: e.unavailable.map((u) => ({ ...u })),
      max_shifts: e.max_shifts,
      active: e.active,
    })),
    // 只投影启用的规则：停用规则在校验面板里显示成「通过」会让人以为它还在生效
    rules: config.rules
      .filter((r) => r.enabled || r.locked)
      .map((r) => ({ id: r.id, text: ruleSummary(r) })),
    days: days.map((d) => ({
      key: d.id,
      label: d.label,
      is_weekend: d.peak,
      // 一天内不同班次可以有不同下限，这里给最小值仅作概览；每格的真值一律读 slot.min_required
      min_required: shifts.length
        ? Math.min(...shifts.map((s) => minRequiredFor(config, d.id, s.id)))
        : 0,
    })),
    shifts: shifts.map((s) => ({ key: s.id, label: s.name, time: timeLabel(s) })),
  };
}

/**
 * 用 `/api/generate` 回显的 scenario 投影 Meta。
 *
 * 为什么不直接用本地配置：契约 2.2 要求「改了配置不清空已有排班」，
 * 于是看板上那张表可能是**旧维度**产出的。按回显渲染，旧表才读得懂；
 * 差异由「配置已变更」横幅说明，而不是把格子显示成一片「缺失」。
 */
export function metaFromEcho(echo: ScenarioEcho, config: SchedulerConfig): Meta {
  const base = metaFromConfig(config);
  return {
    ...base,
    days: echo.days.map((d) => ({
      key: d.id,
      label: d.label,
      is_weekend: d.peak,
      min_required: 0,
    })),
    shifts: echo.shifts.map((s) => ({ key: s.id, label: s.name, time: s.time_label })),
  };
}

export function scenarioEchoFromConfig(config: SchedulerConfig): ScenarioEcho {
  return {
    days: config.scenario.days.map((d) => ({ id: d.id, label: d.label, peak: d.peak })),
    shifts: config.scenario.shifts.map((s) => ({
      id: s.id,
      name: s.name,
      time_label: timeLabel(s),
    })),
  };
}

/**
 * 把响应里的 slots 对齐到当前维度。
 *
 * 为什么需要它：配置化前后 `slot.day` 的取值空间变了（"一" → "d1"）。联调窗口期内
 * 后端可能仍返回旧键，或前端仍持有一份旧键的排班。这里按 id / 标签 / 去掉「周」的标签
 * 三种别名做一次对齐，让看板不至于整片显示「缺失」。对不上的格子原样保留，便于排查。
 */
export function alignSlotsToMeta(meta: Meta, slots: Slot[]): Slot[] {
  const dayAlias = new Map<string, string>();
  for (const d of meta.days) {
    dayAlias.set(d.key, d.key);
    dayAlias.set(d.label, d.key);
    dayAlias.set(d.label.replace(/^周/, ''), d.key);
  }
  const shiftAlias = new Map<string, string>();
  for (const s of meta.shifts) {
    shiftAlias.set(s.key, s.key);
    shiftAlias.set(s.label, s.key);
  }
  const dayMeta = new Map(meta.days.map((d) => [d.key, d]));
  const shiftMeta = new Map(meta.shifts.map((s) => [s.key, s]));

  return slots.map((s) => {
    const day = dayAlias.get(s.day) ?? s.day;
    const shift = shiftAlias.get(s.shift) ?? s.shift;
    return {
      ...s,
      day,
      shift,
      day_label: dayMeta.get(day)?.label ?? s.day_label,
      shift_time: shiftMeta.get(shift)?.time ?? s.shift_time,
    };
  });
}

/** 看板/预览按 (day, shift) 取格，统一在这里建索引，避免各处重复写 slotKey */
export function indexSlots(slots: Slot[]): Map<string, Slot> {
  return new Map(slots.map((s) => [slotKey(s.day, s.shift), s]));
}

/* ---------------- id 生成 ---------------- */

/** 天/班/规则的 id 只保证唯一，不保证连续：删掉中间一天后重编号会让 overrides 指错格子 */
export function nextId(prefix: string, existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; i < 1000; i += 1) {
    const candidate = `${prefix}${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${prefix}${Date.now()}`;
}

export function nextDayId(days: DayDef[]): string {
  return nextId('d', days.map((d) => d.id));
}

export function nextShiftId(shifts: ShiftDef[]): string {
  return nextId('s', shifts.map((s) => s.id));
}

/** 用户自建规则用 C-n 编号（契约 4 节），与内建 R-xx 区分开，一眼能看出来源 */
export function nextRuleId(rules: RuleDef[]): string {
  const used = new Set(rules.map((r) => r.id));
  for (let i = 1; i < 1000; i += 1) {
    const candidate = `C-${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `C-${Date.now()}`;
}

/* ---------------- 指纹 ---------------- */

/**
 * 配置指纹：用来判断「看板上这张排班是不是基于当前配置产出的」。
 * 不做深比较是因为每次渲染都要比一次，而字符串哈希可以缓存进 state 直接比对。
 */
export function configFingerprint(config: SchedulerConfig): string {
  const json = stableStringify(config);
  let hash = 5381;
  for (let i = 0; i < json.length; i += 1) {
    hash = ((hash << 5) + hash + json.charCodeAt(i)) | 0;
  }
  return (hash >>> 0).toString(36);
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
    a < b ? -1 : a > b ? 1 : 0,
  );
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableStringify(v)}`).join(',')}}`;
}

/**
 * 空状态里的示例指令。
 *
 * 原来这里写死「E05 周六请假」。配置化之后这句话随时可能指向一个被停用的工号、
 * 或者一个只有周一到周三的场景里根本不存在的「周六」—— 示例指令一旦点了就填进输入框，
 * 说假话的代价是用户直接生成失败。所以宁可少给一条，也不给一条对不上的。
 */
export function exampleInstructions(config: SchedulerConfig): string[] {
  const days = config.scenario.days;
  const emps = activeEmployees(config);
  if (days.length === 0 || emps.length === 0) return ['正常排班，尽量满足大家的班次偏好'];

  const id = (i: number) => emps[i]?.id ?? null;
  const label = (i: number) => days[Math.min(i, days.length - 1)]?.label ?? null;
  const peak = (days.find((d) => d.peak) ?? days[days.length - 1]).label;
  const firstShift = config.scenario.shifts[0]?.name ?? null;
  const skill = config.skill_pool[config.skill_pool.length - 1] ?? null;

  const out = ['正常排班，尽量满足大家的班次偏好'];
  if (id(4) && label(1) && firstShift && skill) {
    out.push(`正常排班，${id(4)} ${label(1)}请假，${peak}${firstShift}多留一个会${skill}的人`);
  }
  if (id(0) && id(1) && id(3) && label(1) && label(2)) {
    out.push(`正常排班，注意 ${id(0)} ${label(1)}培训、${id(1)} 和 ${id(3)} ${label(2)}都请假`);
  }
  return out;
}
