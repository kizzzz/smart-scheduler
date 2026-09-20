import type {
  RuleResult,
  SchedulerConfig,
  Slot,
  SoftMetrics,
  Suggestion,
  ValidateResponse,
  Validation,
  Violation,
} from '../types';
import {
  absoluteSpan,
  employeeCap,
  enabledRules,
  hasAttribute,
  hasRule,
  isUnavailable,
  minRequiredFor,
  ruleSummary,
} from '../config/derive';

/**
 * mock 模式下的「按配置校验一张排班表」——即 `POST /api/validate` 的前端参考实现。
 *
 * 它替换掉了原来那份写死 9 条规则的 mock 校验器：规则一旦可配置，
 * 校验器就必须从配置读规则，否则用户改完规则做手工微调时，反馈的还是旧规则的结论。
 *
 * 线上一律走后端，前端不做规则裁决；这份实现只服务 `?mock=1` 的可视化自验，
 * 但违规文案与定位口径刻意与后端保持一致，方便对照。
 */

interface Ctx {
  config: SchedulerConfig;
  slots: Slot[];
  dayIndex: Map<string, number>;
  byEmployee: Map<string, Slot[]>;
}

function buildCtx(config: SchedulerConfig, slots: Slot[]): Ctx {
  const dayIndex = new Map(config.scenario.days.map((d, i) => [d.id, i]));
  const byEmployee = new Map<string, Slot[]>();
  for (const s of slots) {
    for (const e of s.employees) {
      const list = byEmployee.get(e) ?? [];
      list.push(s);
      byEmployee.set(e, list);
    }
  }
  return { config, slots, dayIndex, byEmployee };
}

function slotLabel(config: SchedulerConfig, slot: Slot): string {
  const day = config.scenario.days.find((d) => d.id === slot.day)?.label ?? slot.day;
  const shift = config.scenario.shifts.find((s) => s.id === slot.shift)?.name ?? slot.shift;
  return `${day}${shift}`;
}

/**
 * 该格的候选人：启用、当格可用、不超个人上限，并按「每天一班」规则排除同日已排的人。
 * 与 lib/schedule.candidatesForSlot 的区别是这里能读配置里的规则开关。
 */
export function configCandidates(
  config: SchedulerConfig,
  slots: Slot[],
  slot: Slot,
  filter?: (id: string) => boolean,
): string[] {
  const oneShift = hasRule(config, 'one_shift_per_day');
  const sameDay = new Set(
    slots.filter((s) => s.day === slot.day && s.shift !== slot.shift).flatMap((s) => s.employees),
  );
  const load = new Map<string, number>();
  for (const s of slots) for (const e of s.employees) load.set(e, (load.get(e) ?? 0) + 1);

  return config.employees
    .filter((e) => e.active)
    .filter((e) => !slot.employees.includes(e.id))
    .filter((e) => !isUnavailable(e, slot.day, slot.shift))
    .filter((e) => !(oneShift && sameDay.has(e.id)))
    .filter((e) => {
      const cap = employeeCap(config, e);
      return cap === null || (load.get(e.id) ?? 0) < cap;
    })
    .filter((e) => (filter ? filter(e.id) : true))
    .sort((a, b) => (load.get(a.id) ?? 0) - (load.get(b.id) ?? 0))
    .map((e) => e.id);
}

function addSuggestions(
  config: SchedulerConfig,
  slots: Slot[],
  slot: Slot,
  filter?: (id: string) => boolean,
): Suggestion[] {
  return configCandidates(config, slots, slot, filter)
    .slice(0, 2)
    .map((id) => ({
      label: `补入 ${id} 到${slotLabel(config, slot)}`,
      day: slot.day,
      shift: slot.shift,
      remove: null,
      add: id,
    }));
}

function removeSuggestion(config: SchedulerConfig, slot: Slot, employee: string): Suggestion {
  return {
    label: `把 ${employee} 移出${slotLabel(config, slot)}`,
    day: slot.day,
    shift: slot.shift,
    remove: employee,
    add: null,
  };
}

export function validateSlotsWithConfig(config: SchedulerConfig, slots: Slot[]): ValidateResponse {
  const ctx = buildCtx(config, slots);
  const rules: RuleResult[] = [];

  for (const rule of config.rules) {
    if (!rule.enabled && !rule.locked) continue;
    const violations = violationsOf(ctx, rule.type, rule.params);
    rules.push({
      id: rule.id,
      text: ruleSummary(rule),
      passed: violations.length === 0,
      violations,
    });
  }

  const validation: Validation = {
    passed: rules.every((r) => r.passed),
    violation_count: rules.reduce((n, r) => n + r.violations.length, 0),
    rules,
  };
  return { validation, soft_metrics: softMetricsWithConfig(config, slots) };
}

function violationsOf(
  ctx: Ctx,
  type: string,
  params: SchedulerConfig['rules'][number]['params'],
): Violation[] {
  const { config, slots } = ctx;
  const out: Violation[] = [];

  switch (type) {
    case 'min_staff_per_shift': {
      for (const s of slots) {
        const min = minRequiredFor(config, s.day, s.shift);
        if (s.employees.length < min) {
          out.push({
            day: s.day,
            shift: s.shift,
            employees: [],
            message: `${slotLabel(config, s)}实排 ${s.employees.length} 人，低于最低要求 ${min} 人。`,
            suggestions: addSuggestions(config, slots, s),
          });
        }
      }
      break;
    }
    case 'require_attribute': {
      const attr = params.attr ?? 'skill';
      const value = params.value ?? '';
      const min = params.min ?? 0;
      const owns = (id: string) => {
        const e = config.employees.find((x) => x.id === id);
        return e ? hasAttribute(e, attr, value) : false;
      };
      for (const s of slots) {
        const count = s.employees.filter(owns).length;
        if (count < min) {
          out.push({
            day: s.day,
            shift: s.shift,
            employees: [],
            message: `${slotLabel(config, s)}具备「${value}」的员工仅 ${count} 名，少于要求的 ${min} 名。`,
            suggestions: addSuggestions(config, slots, s, owns),
          });
        }
      }
      break;
    }
    case 'max_shifts_per_period': {
      for (const [id, list] of sorted(ctx.byEmployee)) {
        const employee = config.employees.find((e) => e.id === id);
        // 个人上限优先：全局规则是兜底，不是叠加
        const cap = employee ? employeeCap(config, employee) : (params.max ?? null);
        if (cap === null || list.length <= cap) continue;
        const last = list[list.length - 1];
        out.push({
          day: last.day,
          shift: last.shift,
          employees: [id],
          message: `${id} 被排了 ${list.length} 个班，超过上限 ${cap} 个班。`,
          suggestions: [removeSuggestion(config, last, id)],
        });
      }
      break;
    }
    case 'max_consecutive_days': {
      const max = params.max ?? 0;
      for (const [id, list] of sorted(ctx.byEmployee)) {
        const idx = [...new Set(list.map((s) => ctx.dayIndex.get(s.day) ?? -1))]
          .filter((i) => i >= 0)
          .sort((a, b) => a - b);
        let run = idx.length ? 1 : 0;
        let best = run;
        for (let i = 1; i < idx.length; i += 1) {
          run = idx[i] === idx[i - 1] + 1 ? run + 1 : 1;
          best = Math.max(best, run);
        }
        if (best > max) {
          const last = list[list.length - 1];
          out.push({
            day: last.day,
            shift: last.shift,
            employees: [id],
            message: `${id} 连续工作 ${best} 天，超过连续 ${max} 天上限。`,
            suggestions: [removeSuggestion(config, last, id)],
          });
        }
      }
      break;
    }
    case 'min_rest_hours': {
      const hours = params.hours ?? 0;
      const shiftDef = new Map(config.scenario.shifts.map((s) => [s.id, s]));
      for (const [id, list] of sorted(ctx.byEmployee)) {
        const spans = list
          .map((s) => {
            const def = shiftDef.get(s.shift);
            const di = ctx.dayIndex.get(s.day);
            if (!def || di === undefined) return null;
            return { slot: s, ...absoluteSpan(di, def) };
          })
          .filter((x): x is { slot: Slot; start: number; end: number } => x !== null)
          .sort((a, b) => a.start - b.start);
        for (let i = 1; i < spans.length; i += 1) {
          const gap = (spans[i].start - spans[i - 1].end) / 60;
          if (gap < hours) {
            out.push({
              day: spans[i].slot.day,
              shift: spans[i].slot.shift,
              employees: [id],
              message: `${id} ${slotLabel(config, spans[i - 1].slot)}结束到${slotLabel(
                config,
                spans[i].slot,
              )}开始只隔 ${Math.max(0, Math.round(gap * 10) / 10)} 小时，少于要求的 ${hours} 小时。`,
              suggestions: [removeSuggestion(config, spans[i].slot, id)],
            });
          }
        }
      }
      break;
    }
    case 'one_shift_per_day': {
      for (const [id, list] of sorted(ctx.byEmployee)) {
        const byDay = new Map<string, Slot[]>();
        for (const s of list) byDay.set(s.day, [...(byDay.get(s.day) ?? []), s]);
        for (const [, sameDay] of byDay) {
          if (sameDay.length <= 1) continue;
          const last = sameDay[sameDay.length - 1];
          out.push({
            day: last.day,
            shift: last.shift,
            employees: [id],
            message: `${id} 同一天被排了 ${sameDay.length} 个班（${sameDay
              .map((s) => slotLabel(config, s))
              .join('、')}）。`,
            suggestions: [removeSuggestion(config, last, id)],
          });
        }
      }
      break;
    }
    case 'respect_unavailability': {
      for (const s of slots) {
        for (const id of s.employees) {
          const e = config.employees.find((x) => x.id === id);
          if (!e) continue;
          if (!e.active) {
            out.push({
              day: s.day,
              shift: s.shift,
              employees: [id],
              message: `${id} 已停用，不应出现在排班里。`,
              suggestions: [removeSuggestion(config, s, id)],
            });
          } else if (isUnavailable(e, s.day, s.shift)) {
            out.push({
              day: s.day,
              shift: s.shift,
              employees: [id],
              message: `${id} 在${slotLabel(config, s)}被标记为不可排班。`,
              suggestions: [removeSuggestion(config, s, id)],
            });
          }
        }
      }
      break;
    }
    case 'skill_source_integrity': {
      for (const s of slots) {
        for (const id of s.employees) {
          if (!config.employees.some((e) => e.id === id)) {
            out.push({
              day: s.day,
              shift: s.shift,
              employees: [id],
              message: `${id} 不在员工档案中，技能无法核验。`,
              suggestions: [removeSuggestion(config, s, id)],
            });
          }
        }
      }
      break;
    }
    default:
      break;
  }
  return out;
}

function sorted(map: Map<string, Slot[]>): Array<[string, Slot[]]> {
  return [...map.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

/**
 * 软指标。口径与后端 validator.soft_metrics 保持一致（偏好命中率只统计有偏好的人、
 * 均衡度用标准差、技能冗余按每班 8 人次记满分），否则 mock 演示站与真实环境
 * 会对同一张表给出不同数字，比对时无法互信。
 *
 * 唯一的配置化改动：冗余度的「超额」不再写死三种技能，而是按 require_attribute 规则算。
 */
export function softMetricsWithConfig(config: SchedulerConfig, slots: Slot[]): SoftMetrics {
  const empMap = new Map(config.employees.map((e) => [e.id, e]));
  const attrRules = enabledRules(config, 'require_attribute');
  let hit = 0;
  let prefTotal = 0;
  let redundancy = 0;
  const counts = new Map<string, number>();

  for (const s of slots) {
    const ids = [...new Set(s.employees)].filter((id) => empMap.has(id));
    for (const id of ids) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
      const pref = empMap.get(id)?.preferred_shifts ?? [];
      if (pref.length > 0) {
        prefTotal += 1;
        if (pref.includes(s.shift)) hit += 1;
      }
    }
    for (const rule of attrRules) {
      const attr = rule.params.attr ?? 'skill';
      const value = rule.params.value ?? '';
      const min = rule.params.min ?? 0;
      const count = ids.filter((id) => {
        const e = empMap.get(id);
        return e ? hasAttribute(e, attr, value) : false;
      }).length;
      redundancy += Math.max(0, count - min);
    }
  }

  const working = [...counts.values()].filter((v) => v > 0);
  const mean = working.reduce((a, b) => a + b, 0) / Math.max(1, working.length);
  const stdev =
    working.length > 1
      ? Math.sqrt(working.reduce((a, b) => a + (b - mean) ** 2, 0) / working.length)
      : 0;
  const balance = Math.max(0, Math.min(1, 1 - stdev / 1.5));
  const avgRedundancy = redundancy / Math.max(1, slots.length);

  return {
    preference_rate: prefTotal ? Number((hit / prefTotal).toFixed(4)) : 0,
    balance_score: Number(balance.toFixed(4)),
    skill_redundancy: Number(Math.min(1, avgRedundancy / 8).toFixed(3)),
  };
}
