import type { IntentChip, Meta, Slot, Validation } from '../types';
import { slotKey } from './utils';

export const SKILL_MANAGER = '店长值守';

/** 该班次的候选人员：当日可工作、未请假、未在当天另一班次、且不在本班次内 */
export function candidatesForSlot(
  meta: Meta,
  slots: Slot[],
  target: Slot,
  needSkill?: string,
): string[] {
  const other = slots.find((s) => s.day === target.day && s.shift !== target.shift);
  const busy = new Set([...(other?.employees ?? []), ...target.employees]);
  return meta.employees
    .filter((e) => !busy.has(e.id))
    .filter((e) => e.available_days.includes(target.day) && !e.leave_days.includes(target.day))
    .filter((e) => (needSkill ? e.skills.includes(needSkill) : true))
    .map((e) => e.id);
}

/** 每位员工本周班次数（用于换人面板提示工时余量） */
export function weeklyLoad(slots: Slot[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const s of slots) for (const e of s.employees) m.set(e, (m.get(e) ?? 0) + 1);
  return m;
}

/** 在指定班次上执行一次「移除 / 加入」，返回新的 slots（不可变更新） */
export function applySlotChange(
  slots: Slot[],
  day: string,
  shift: string,
  remove: string | null,
  add: string | null,
): Slot[] {
  return slots.map((s) => {
    if (s.day !== day || s.shift !== shift) return s;
    let next = s.employees;
    if (remove) next = next.filter((e) => e !== remove);
    if (add && !next.includes(add)) next = [...next, add];
    next = [...next].sort();
    return { ...s, employees: next };
  });
}

export interface SlotIssueIndex {
  ruleIds: string[];
  employees: Set<string>;
  messages: string[];
}

/** 把 validation 展开成 slotKey → 违规信息，供看板定位红框 */
export function buildIssueIndex(validation: Validation | null): Map<string, SlotIssueIndex> {
  const map = new Map<string, SlotIssueIndex>();
  if (!validation) return map;
  for (const rule of validation.rules) {
    if (rule.passed) continue;
    for (const v of rule.violations) {
      const key = slotKey(v.day, v.shift);
      const cur = map.get(key) ?? { ruleIds: [], employees: new Set<string>(), messages: [] };
      if (!cur.ruleIds.includes(rule.id)) cur.ruleIds.push(rule.id);
      v.employees.forEach((e) => cur.employees.add(e));
      cur.messages.push(v.message);
      map.set(key, cur);
    }
  }
  return map;
}

/** 店长修正意图后拼装新的 instruction */
export function buildCorrectedInstruction(original: string, chips: IntentChip[]): string {
  const parts = chips.map((c) => `${c.label}：${c.value}`).join('；');
  const base = original.trim();
  return `${base ? `${base}\n` : ''}[店长修正后的理解] ${parts}。请严格按修正后的理解重新排班。`;
}

export function isManager(meta: Meta | null, id: string): boolean {
  return meta?.employees.find((e) => e.id === id)?.skills.includes(SKILL_MANAGER) ?? false;
}
