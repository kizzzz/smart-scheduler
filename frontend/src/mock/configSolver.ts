import type {
  Diff,
  EmployeeDef,
  GenerateResponse,
  SchedulerConfig,
  Slot,
} from '../types';
import {
  absoluteSpan,
  employeeCap,
  enabledRules,
  hasAttribute,
  hasRule,
  isUnavailable,
  minRequiredFor,
  scenarioEchoFromConfig,
  timeLabel,
} from '../config/derive';
import { slotKey } from '../lib/utils';
import { validateSlotsWithConfig } from './configValidator';

/**
 * 按配置现场排一张班表的 mock 求解器。
 *
 * 为什么需要它：原来的 mock 是 5 份写死 7 天 × 2 班的 JSON fixture。用户一旦把场景改成
 * 3 天 × 3 班，fixture 就完全对不上，「看板按配置动态渲染」这件事也就无从验证。
 * 所以自定义配置走这个贪心求解器，默认配置仍走 fixture（保留原有演示剧情）。
 *
 * 它是**贪心**的，不是最优解，也不保证可行：填不满的格子会被校验器如实报成违规，
 * 这比悄悄补人更有价值——mock 的目的是验证界面表现，不是替代后端求解器。
 */

interface Assignment {
  start: number;
  end: number;
  dayIndex: number;
}

interface SolveCtx {
  config: SchedulerConfig;
  /** 每人已排班次的时间区间，用于休息间隔与连续天数判断 */
  byEmployee: Map<string, Assignment[]>;
}

function consecutiveRun(days: number[], candidate: number): number {
  const set = new Set([...days, candidate]);
  let run = 1;
  let i = candidate;
  while (set.has(i - 1)) {
    run += 1;
    i -= 1;
  }
  i = candidate;
  while (set.has(i + 1)) {
    run += 1;
    i += 1;
  }
  return run;
}

function canAssign(
  ctx: SolveCtx,
  employee: EmployeeDef,
  dayIndex: number,
  dayId: string,
  shiftId: string,
): boolean {
  const { config } = ctx;
  if (!employee.active) return false;
  if (isUnavailable(employee, dayId, shiftId)) return false;

  const mine = ctx.byEmployee.get(employee.id) ?? [];
  const cap = employeeCap(config, employee);
  if (cap !== null && mine.length >= cap) return false;

  if (hasRule(config, 'one_shift_per_day') && mine.some((a) => a.dayIndex === dayIndex)) return false;

  const shift = config.scenario.shifts.find((s) => s.id === shiftId);
  if (!shift) return false;
  const span = absoluteSpan(dayIndex, shift);

  for (const rule of enabledRules(config, 'min_rest_hours')) {
    const needed = (rule.params.hours ?? 0) * 60;
    for (const a of mine) {
      const gap = span.start >= a.end ? span.start - a.end : a.start - span.end;
      if (gap < needed) return false;
    }
  }

  for (const rule of enabledRules(config, 'max_consecutive_days')) {
    const max = rule.params.max ?? 0;
    if (consecutiveRun(mine.map((a) => a.dayIndex), dayIndex) > max) return false;
  }

  return true;
}

function commit(ctx: SolveCtx, employee: EmployeeDef, dayIndex: number, shiftId: string): void {
  const shift = ctx.config.scenario.shifts.find((s) => s.id === shiftId)!;
  const list = ctx.byEmployee.get(employee.id) ?? [];
  list.push({ ...absoluteSpan(dayIndex, shift), dayIndex });
  ctx.byEmployee.set(employee.id, list);
}

/** 贪心排班：先满足属性类要求，再补齐人数下限。同分时优先班次偏好匹配、已排班次更少的人 */
export function solveWithConfig(config: SchedulerConfig): Slot[] {
  const ctx: SolveCtx = { config, byEmployee: new Map() };
  const { days, shifts } = config.scenario;
  const attrRules = enabledRules(config, 'require_attribute');
  const slots: Slot[] = [];

  days.forEach((day, dayIndex) => {
    for (const shift of shifts) {
      const chosen: string[] = [];
      const take = (e: EmployeeDef) => {
        chosen.push(e.id);
        commit(ctx, e, dayIndex, shift.id);
      };
      const pool = () =>
        config.employees
          .filter((e) => !chosen.includes(e.id))
          .filter((e) => canAssign(ctx, e, dayIndex, day.id, shift.id));

      const rank = (a: EmployeeDef, b: EmployeeDef) => {
        const load = (e: EmployeeDef) => (ctx.byEmployee.get(e.id) ?? []).length;
        const pref = (e: EmployeeDef) => (e.preferred_shifts.includes(shift.id) ? 0 : 1);
        return load(a) - load(b) || pref(a) - pref(b) || (a.id < b.id ? -1 : 1);
      };

      for (const rule of attrRules) {
        const attr = rule.params.attr ?? 'skill';
        const value = rule.params.value ?? '';
        const min = rule.params.min ?? 0;
        while (
          chosen.filter((id) => {
            const e = config.employees.find((x) => x.id === id);
            return e ? hasAttribute(e, attr, value) : false;
          }).length < min
        ) {
          const next = pool()
            .filter((e) => hasAttribute(e, attr, value))
            .sort(rank)[0];
          if (!next) break; // 供给不足：交给校验器报违规，不硬塞
          take(next);
        }
      }

      const min = minRequiredFor(config, day.id, shift.id);
      while (chosen.length < min) {
        const next = pool().sort(rank)[0];
        if (!next) break;
        take(next);
      }

      slots.push({
        day: day.id,
        day_label: day.label,
        shift: shift.id,
        shift_time: timeLabel(shift),
        min_required: min,
        employees: [...chosen].sort(),
      });
    }
  });

  return slots;
}

function buildDiff(base: Slot[], next: Slot[]): Diff {
  const baseMap = new Map(base.map((s) => [slotKey(s.day, s.shift), s.employees]));
  const changed = next
    .map((s) => {
      const before = baseMap.get(slotKey(s.day, s.shift)) ?? [];
      const added = s.employees.filter((e) => !before.includes(e));
      const removed = before.filter((e) => !s.employees.includes(e));
      return { day: s.day, shift: s.shift, added, removed };
    })
    .filter((d) => d.added.length > 0 || d.removed.length > 0);
  return { changed_count: changed.length, changed_slots: changed, new_violations: 0 };
}

/**
 * 按配置合成一整个 `/api/generate` 响应。
 * intent 走「规则解析」口径（degraded=true）而不是假装调了模型——mock 里没有真的 LLM，
 * 谎报 model_used 会让人以为模型链路也验过了。
 */
export function mockGenerateWithConfig(
  config: SchedulerConfig,
  instruction: string,
  baseSlots: Slot[] | null,
  model?: string | null,
): GenerateResponse {
  const slots = solveWithConfig(config);
  const { validation, soft_metrics } = validateSlotsWithConfig(config, slots);
  const { days, shifts } = config.scenario;
  const headcount = slots.reduce((n, s) => n + s.employees.length, 0);
  const activeCount = config.employees.filter((e) => e.active).length;
  // 无解与「有解但有违规」是两件事：没方案时校验结论一律为 null（契约 solution=null → validation=null）
  const solved = activeCount > 0;

  const periodLabel = `${days[0]?.label ?? ''} – ${days[days.length - 1]?.label ?? ''}（${
    days.length
  } 天 × ${shifts.length} 班）`;

  const base: GenerateResponse = {
    ok: true,
    mode: baseSlots ? 'adjust' : 'generate',
    intent: {
      operation: baseSlots ? 'adjust' : 'generate',
      period_label: periodLabel,
      objective_label: '偏好满足优先',
      chips: [
        { key: 'period', label: '周期', value: periodLabel, editable: true },
        {
          key: 'constraints',
          label: '临时约束',
          value: instruction.trim() ? instruction.trim().slice(0, 40) : '无额外临时约束',
          editable: true,
        },
        { key: 'objective', label: '优化目标', value: '偏好满足优先', editable: true },
      ],
      temp_constraints: [],
      degraded: true,
      degrade_reason: 'mock 模式不调用模型，本次按自定义配置做确定性贪心排班',
      model_used: null,
      model_fallback_from: model ?? null,
      guardrail: null,
    },
    solution: solved ? { found: true, slots, soft_metrics, restarts_used: 0 } : null,
    validation: solved ? validation : null,
    explanation: {
      bullets: solved
        ? [
            `按你的配置排了 ${days.length} 天 × ${shifts.length} 班，共 ${slots.length} 格、${headcount} 人次。`,
            `参与排班的启用员工 ${activeCount} 人，生效规则 ${
              config.rules.filter((r) => r.enabled || r.locked).length
            } 条。`,
            validation.passed
              ? '硬规则全部通过。'
              : `仍有 ${validation.violation_count} 处违规，多数来自可排人手不足——先去配置页看供需自检。`,
          ]
        : [
            '没有任何启用员工，本次没有产出排班。',
            '因为没排出方案，硬规则这一轮没有参与校验（不是「全部通过」，也不是「全部违规」）。',
          ],
      unmet_preferences: [],
      degraded: true,
    },
    infeasible: solved
      ? null
      : {
          proven: true,
          summary: '没有任何启用员工，无法产出排班。',
          min_conflict_set: [],
          conflict_slot: '',
          unlock_paths: [
            { title: '启用员工', detail: '在配置第二步启用至少一名员工', extra_cost: null, needs_approval: false },
          ],
        },
    clarification: null,
    diff: baseSlots ? buildDiff(baseSlots, slots) : null,
    timing: { parse_ms: 6, solve_ms: 18 + slots.length, validate_ms: 9, explain_ms: 4, total_ms: 40 + slots.length },
    scenario: scenarioEchoFromConfig(config),
  };

  return base;
}
