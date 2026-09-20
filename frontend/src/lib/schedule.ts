import type { ImportedSlot, IntentChip, Meta, Slot, Validation } from '../types';
import { slotKey } from './utils';

export const SKILL_MANAGER = '店长值守';

/**
 * 把 `/api/import` 的精简格子补成看板 / base_slots 需要的完整 Slot。
 *
 * day_label、shift_time 一律取自当前维度（meta），不从导入文件里猜；
 * min_required 由调用方给的解析器决定——配置化之后同一天的不同班次可以有不同下限，
 * 不能再按「天」取一个值。
 *
 * 键的匹配是**别名容错**的：导入文件里写的是人话（「周一」「早班」），而维度键是 d1/s1。
 * 这里按 id / 标签 / 去掉「周」的标签三种写法去认，认不出的格子直接丢弃，
 * 避免脏数据把看板撑歪。
 */
export function normalizeImportedSlots(
  meta: Meta,
  imported: ImportedSlot[],
  minRequired?: (day: string, shift: string) => number,
): Slot[] {
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

  const bySlot = new Map<string, ImportedSlot>();
  for (const s of imported) {
    const day = dayAlias.get(s.day);
    const shift = shiftAlias.get(s.shift);
    if (!day || !shift) continue;
    bySlot.set(slotKey(day, shift), s);
  }

  const out: Slot[] = [];
  for (const day of meta.days) {
    for (const shift of meta.shifts) {
      const hit = bySlot.get(slotKey(day.key, shift.key));
      if (!hit) continue;
      out.push({
        day: day.key,
        day_label: day.label,
        shift: shift.key,
        shift_time: shift.time,
        min_required: minRequired ? minRequired(day.key, shift.key) : day.min_required,
        employees: [...hit.employees].sort(),
      });
    }
  }
  return out;
}

/**
 * 换人候选的前端实现已删除。
 *
 * 候选名单一律走 `POST /api/candidates`（契约 5.5）：过滤条件与求解器共用
 * `solver.eligible`，前端再留一份「差不多的过滤」只会和后端悄悄漂移——
 * 而店长正是照着这份名单做决定的。mock 模式下的参考实现见 mock/candidates.ts。
 */

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

/**
 * 违规 / 格子的定位文案。
 *
 * 配置化之后 `day`、`shift` 是内部 id（d1 / s1），直接拼进文案会显示成「周d1 s1」。
 * 所以任何要展示位置的地方都必须过这个翻译，翻译不出来时原样回显 id 便于排查。
 * 跨格子的规则（如周工时）后端不给 day/shift，此时说「本周期整体」。
 */
export function formatScope(meta: Meta, day: string, shift: string): string {
  const dayLabel = meta.days.find((d) => d.key === day)?.label ?? day;
  const shiftLabel = meta.shifts.find((s) => s.key === shift)?.label ?? shift;
  if (day && shift) return `${dayLabel} ${shiftLabel}`;
  if (day) return `${dayLabel} 全天`;
  return '本周期整体';
}
