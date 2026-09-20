import type {
  EmployeeDef,
  MinStaffOverride,
  RuleDef,
  RuleParams,
  RuleType,
  SchedulerConfig,
  ShiftDef,
  UnavailableSlot,
} from '../types';
import { dayLabelFor } from './defaults';
import { nextDayId, nextShiftId, shiftHours } from './derive';
import { makeRule } from './ruleTemplates';
import { MAX_DAYS, MAX_SHIFTS } from './checks';

/**
 * 配置的不可变编辑函数集合。
 *
 * 全部写成纯函数而不是塞进组件里，原因是「删掉一天」这类操作要级联清理
 * 员工不可用时段与规则例外——这种逻辑散在三个步骤组件里必然出现漏改，
 * 结果就是配置里留下指向已删除 id 的悬挂引用，后端自检才报出来。
 */

/* ---------------- 场景 ---------------- */

/** 删掉天/班次后清掉所有指向它的引用，避免留下悬挂 id */
function pruneReferences(config: SchedulerConfig): SchedulerConfig {
  const dayIds = new Set(config.scenario.days.map((d) => d.id));
  const shiftIds = new Set(config.scenario.shifts.map((s) => s.id));
  return {
    ...config,
    employees: config.employees.map((e) => ({
      ...e,
      unavailable: e.unavailable.filter(
        (u) => dayIds.has(u.day) && (u.shift === null || shiftIds.has(u.shift)),
      ),
      preferred_shifts: e.preferred_shifts.filter((s) => shiftIds.has(s)),
    })),
    rules: config.rules.map((r) =>
      r.type === 'min_staff_per_shift'
        ? {
            ...r,
            params: {
              ...r.params,
              overrides: (r.params.overrides ?? []).filter(
                (o) => dayIds.has(o.day) && shiftIds.has(o.shift),
              ),
            },
          }
        : r,
    ),
  };
}

export function setDayCount(config: SchedulerConfig, count: number): SchedulerConfig {
  const target = Math.max(1, Math.min(MAX_DAYS, Math.round(count)));
  const days = [...config.scenario.days];
  while (days.length > target) days.pop();
  while (days.length < target) {
    const id = nextDayId(days);
    // 新增的一天默认不是高峰日：加人是加成本，不该由系统替用户决定
    days.push({ id, label: dayLabelFor(days.length), peak: false });
  }
  return pruneReferences({ ...config, scenario: { ...config.scenario, days } });
}

export function updateDay(
  config: SchedulerConfig,
  id: string,
  patch: Partial<{ label: string; peak: boolean }>,
): SchedulerConfig {
  return {
    ...config,
    scenario: {
      ...config.scenario,
      days: config.scenario.days.map((d) => (d.id === id ? { ...d, ...patch } : d)),
    },
  };
}

export function addShift(config: SchedulerConfig): SchedulerConfig {
  const shifts = config.scenario.shifts;
  if (shifts.length >= MAX_SHIFTS) return config;
  const last = shifts[shifts.length - 1];
  // 新班次默认接在上一个班次结束时间之后，比给一个固定的 09:00–17:00 更接近用户意图
  const start = last?.end ?? '09:00';
  const end = shiftEndAfter(start, 8);
  const id = nextShiftId(shifts);
  return {
    ...config,
    scenario: {
      ...config.scenario,
      shifts: [
        ...shifts,
        { id, name: `班次 ${shifts.length + 1}`, start, end, hours: shiftHours(start, end) },
      ],
    },
  };
}

function shiftEndAfter(start: string, hours: number): string {
  const [h, m] = start.split(':').map((x) => Number(x) || 0);
  const total = (h * 60 + m + hours * 60) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

export function removeShift(config: SchedulerConfig, id: string): SchedulerConfig {
  if (config.scenario.shifts.length <= 1) return config;
  return pruneReferences({
    ...config,
    scenario: { ...config.scenario, shifts: config.scenario.shifts.filter((s) => s.id !== id) },
  });
}

export function updateShift(
  config: SchedulerConfig,
  id: string,
  patch: Partial<Pick<ShiftDef, 'name' | 'start' | 'end'>>,
): SchedulerConfig {
  return {
    ...config,
    scenario: {
      ...config.scenario,
      shifts: config.scenario.shifts.map((s) => {
        if (s.id !== id) return s;
        const next = { ...s, ...patch };
        // hours 永远由 start/end 回算，界面上只读展示，避免两者打架
        return { ...next, hours: shiftHours(next.start, next.end) };
      }),
    },
  };
}

/* ---------------- 员工 ---------------- */

/** 新工号沿用 Exx 序列并补零，保持与看板 chip 的等宽观感一致 */
export function nextEmployeeId(employees: EmployeeDef[]): string {
  const used = new Set(employees.map((e) => e.id));
  for (let i = 1; i < 1000; i += 1) {
    const candidate = `E${String(i).padStart(2, '0')}`;
    if (!used.has(candidate)) return candidate;
  }
  return `E${Date.now()}`;
}

export function addEmployee(config: SchedulerConfig): { config: SchedulerConfig; id: string } {
  const id = nextEmployeeId(config.employees);
  const employee: EmployeeDef = {
    id,
    name: '',
    role: config.role_pool[config.role_pool.length - 1] ?? '',
    skills: [],
    unavailable: [],
    max_shifts: null,
    preferred_shifts: [],
    active: true,
  };
  return { config: { ...config, employees: [...config.employees, employee] }, id };
}

export function updateEmployee(
  config: SchedulerConfig,
  id: string,
  patch: Partial<EmployeeDef>,
): SchedulerConfig {
  return {
    ...config,
    employees: config.employees.map((e) => (e.id === id ? { ...e, ...patch } : e)),
  };
}

export function removeEmployees(config: SchedulerConfig, ids: string[]): SchedulerConfig {
  const drop = new Set(ids);
  return { ...config, employees: config.employees.filter((e) => !drop.has(e.id)) };
}

export function setEmployeesActive(
  config: SchedulerConfig,
  ids: string[],
  active: boolean,
): SchedulerConfig {
  const hit = new Set(ids);
  return {
    ...config,
    employees: config.employees.map((e) => (hit.has(e.id) ? { ...e, active } : e)),
  };
}

/* ---------------- 不可排班时段 ---------------- */

function cellKey(day: string, shift: string): string {
  return `${day}|${shift}`;
}

/** 把 unavailable 展开成「格子集合」，整日不可用会展开成当天所有班次 */
export function unavailableCells(
  employee: EmployeeDef,
  days: string[],
  shifts: string[],
): Set<string> {
  const set = new Set<string>();
  for (const u of employee.unavailable) {
    if (!days.includes(u.day)) continue;
    if (u.shift === null) shifts.forEach((s) => set.add(cellKey(u.day, s)));
    else if (shifts.includes(u.shift)) set.add(cellKey(u.day, u.shift));
  }
  return set;
}

/**
 * 从格子集合收敛回 unavailable 列表：一整天都不可用就收敛成 `{day, shift: null}`。
 * 收敛不是为了省字节，而是为了让导出的 JSON 人能读——「周三整天不可用」比
 * 三条分散的班次记录更接近用户脑子里的模型。
 */
function cellsToUnavailable(cells: Set<string>, days: string[], shifts: string[]): UnavailableSlot[] {
  const out: UnavailableSlot[] = [];
  for (const day of days) {
    const hit = shifts.filter((s) => cells.has(cellKey(day, s)));
    if (hit.length === 0) continue;
    if (hit.length === shifts.length) out.push({ day, shift: null });
    else hit.forEach((s) => out.push({ day, shift: s }));
  }
  return out;
}

export function toggleUnavailableCell(
  config: SchedulerConfig,
  employeeId: string,
  day: string,
  shift: string,
): SchedulerConfig {
  const days = config.scenario.days.map((d) => d.id);
  const shifts = config.scenario.shifts.map((s) => s.id);
  return {
    ...config,
    employees: config.employees.map((e) => {
      if (e.id !== employeeId) return e;
      const cells = unavailableCells(e, days, shifts);
      const key = cellKey(day, shift);
      if (cells.has(key)) cells.delete(key);
      else cells.add(key);
      return { ...e, unavailable: cellsToUnavailable(cells, days, shifts) };
    }),
  };
}

export function toggleUnavailableDay(
  config: SchedulerConfig,
  employeeId: string,
  day: string,
): SchedulerConfig {
  const days = config.scenario.days.map((d) => d.id);
  const shifts = config.scenario.shifts.map((s) => s.id);
  return {
    ...config,
    employees: config.employees.map((e) => {
      if (e.id !== employeeId) return e;
      const cells = unavailableCells(e, days, shifts);
      const whole = shifts.every((s) => cells.has(cellKey(day, s)));
      shifts.forEach((s) => (whole ? cells.delete(cellKey(day, s)) : cells.add(cellKey(day, s))));
      return { ...e, unavailable: cellsToUnavailable(cells, days, shifts) };
    }),
  };
}

export function clearUnavailable(config: SchedulerConfig, employeeId: string): SchedulerConfig {
  return updateEmployee(config, employeeId, { unavailable: [] });
}

/* ---------------- 字典 ---------------- */

export function addSkill(config: SchedulerConfig, skill: string): SchedulerConfig {
  const value = skill.trim();
  if (!value || config.skill_pool.includes(value)) return config;
  return { ...config, skill_pool: [...config.skill_pool, value] };
}

export function addRole(config: SchedulerConfig, role: string): SchedulerConfig {
  const value = role.trim();
  if (!value || config.role_pool.includes(value)) return config;
  return { ...config, role_pool: [...config.role_pool, value] };
}

/** 技能字典里某项是否还在被使用（员工档案或规则参数），决定它能不能删 */
export function skillUsage(config: SchedulerConfig, skill: string): { employees: number; rules: number } {
  return {
    employees: config.employees.filter((e) => e.skills.includes(skill)).length,
    rules: config.rules.filter((r) => r.type === 'require_attribute' && r.params.value === skill).length,
  };
}

export function removeSkill(config: SchedulerConfig, skill: string): SchedulerConfig {
  const usage = skillUsage(config, skill);
  // 还在被引用时拒绝删除：级联删掉员工技能会静默改变排班能力，代价远大于让用户先手工解绑
  if (usage.employees > 0 || usage.rules > 0) return config;
  return { ...config, skill_pool: config.skill_pool.filter((s) => s !== skill) };
}

/* ---------------- 规则 ---------------- */

export function addRule(config: SchedulerConfig, type: RuleType): SchedulerConfig {
  return { ...config, rules: [...config.rules, makeRule(config, type)] };
}

export function updateRule(
  config: SchedulerConfig,
  id: string,
  patch: Partial<Pick<RuleDef, 'name' | 'enabled'>>,
): SchedulerConfig {
  return {
    ...config,
    rules: config.rules.map((r) => {
      if (r.id !== id) return r;
      // locked 规则的 enabled 不接受修改：开关在 UI 上是禁用态，这里再兜一层，
      // 防止将来某处代码绕过 UI 直接改到内建约束
      if (r.locked && patch.enabled !== undefined) {
        const { enabled: _ignored, ...rest } = patch;
        return { ...r, ...rest };
      }
      return { ...r, ...patch };
    }),
  };
}

export function updateRuleParams(
  config: SchedulerConfig,
  id: string,
  patch: RuleParams,
): SchedulerConfig {
  return {
    ...config,
    rules: config.rules.map((r) => (r.id === id ? { ...r, params: { ...r.params, ...patch } } : r)),
  };
}

export function removeRule(config: SchedulerConfig, id: string): SchedulerConfig {
  return { ...config, rules: config.rules.filter((r) => r.id !== id || r.locked) };
}

export function setMinStaffOverride(
  config: SchedulerConfig,
  ruleId: string,
  override: MinStaffOverride,
): SchedulerConfig {
  const rule = config.rules.find((r) => r.id === ruleId);
  const list = (rule?.params.overrides ?? []).filter(
    (o) => !(o.day === override.day && o.shift === override.shift),
  );
  return updateRuleParams(config, ruleId, { overrides: [...list, override] });
}

export function removeMinStaffOverride(
  config: SchedulerConfig,
  ruleId: string,
  day: string,
  shift: string,
): SchedulerConfig {
  const rule = config.rules.find((r) => r.id === ruleId);
  return updateRuleParams(config, ruleId, {
    overrides: (rule?.params.overrides ?? []).filter((o) => !(o.day === day && o.shift === shift)),
  });
}
