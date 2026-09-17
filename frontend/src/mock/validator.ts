import type { Meta, Slot, Validation, ValidateResponse, RuleResult, Violation } from '../types';
import { slotKey } from '../lib/utils';
import { candidatesForSlot } from '../lib/schedule';

const DAY_ORDER = ['一', '二', '三', '四', '五', '六', '日'];
const SKILL_MANAGER = '店长值守';
const SKILL_DRINK = '饮品制作';
const SKILL_CASHIER = '收银';

function ruleText(meta: Meta, id: string): string {
  return meta.rules.find((r) => r.id === id)?.text ?? id;
}

function dayLabel(day: string): string {
  return `周${day}`;
}

/**
 * mock 模式下的本地校验器：9 条硬规则的前端参考实现。
 * 仅用于 `?mock=1` 自验（让换人 / 应用建议后的实时校验有真实反馈），
 * 线上一律走后端 `POST /api/validate`，前端不做规则裁决。
 */
export function mockValidate(meta: Meta, slots: Slot[]): ValidateResponse {
  const empMap = new Map(meta.employees.map((e) => [e.id, e]));
  const has = (id: string, skill: string) => empMap.get(id)?.skills.includes(skill) ?? false;
  const bySlot = new Map(slots.map((s) => [slotKey(s.day, s.shift), s]));

  const rules: RuleResult[] = [];
  const push = (id: string, violations: Violation[]) => {
    rules.push({ id, text: ruleText(meta, id), passed: violations.length === 0, violations });
  };

  const skillRule = (id: string, skill: string, need: number) => {
    const v: Violation[] = [];
    for (const s of slots) {
      const c = s.employees.filter((e) => has(e, skill)).length;
      if (c < need) {
        v.push({
          day: s.day,
          shift: s.shift,
          employees: [],
          message: `${s.day_label}${s.shift}具备「${skill}」的员工仅 ${c} 名，少于要求的 ${need} 名。`,
          suggestions: candidatesForSlot(meta, slots, s, skill).slice(0, 2).map((cid) => ({
            label: `补入 ${cid} 到${s.day_label}${s.shift}`,
            day: s.day,
            shift: s.shift,
            remove: null,
            add: cid,
          })),
        });
      }
    }
    push(id, v);
  };

  skillRule('R-01', SKILL_MANAGER, 1);
  skillRule('R-02', SKILL_DRINK, 2);
  skillRule('R-03', SKILL_CASHIER, 1);

  // R-04 人数下限
  const r04: Violation[] = [];
  for (const s of slots) {
    if (s.employees.length < s.min_required) {
      r04.push({
        day: s.day,
        shift: s.shift,
        employees: [],
        message: `${s.day_label}${s.shift}实排 ${s.employees.length} 人，低于最低要求 ${s.min_required} 人。`,
        suggestions: candidatesForSlot(meta, slots, s).slice(0, 2).map((cid) => ({
          label: `补入 ${cid} 到${s.day_label}${s.shift}`,
          day: s.day,
          shift: s.shift,
          remove: null,
          add: cid,
        })),
      });
    }
  }
  push('R-04', r04);

  // 每人班次索引
  const byEmp = new Map<string, Slot[]>();
  for (const s of slots) {
    for (const e of s.employees) {
      const list = byEmp.get(e) ?? [];
      list.push(s);
      byEmp.set(e, list);
    }
  }

  // R-05 每周 ≤5 个班
  const r05: Violation[] = [];
  for (const [e, list] of [...byEmp.entries()].sort()) {
    if (list.length > 5) {
      const s = list[list.length - 1];
      r05.push({
        day: s.day,
        shift: s.shift,
        employees: [e],
        message: `${e} 本周被排 ${list.length} 个班，超过每周 5 个班（40 小时）上限。`,
        suggestions: [
          { label: `把 ${e} 移出${s.day_label}${s.shift}`, day: s.day, shift: s.shift, remove: e, add: null },
        ],
      });
    }
  }
  push('R-05', r05);

  // R-06 连续 ≤5 天
  const r06: Violation[] = [];
  for (const [e, list] of [...byEmp.entries()].sort()) {
    const idx = [...new Set(list.map((s) => DAY_ORDER.indexOf(s.day)))].sort((a, b) => a - b);
    let run = 1;
    let best = idx.length ? 1 : 0;
    for (let i = 1; i < idx.length; i += 1) {
      run = idx[i] === idx[i - 1] + 1 ? run + 1 : 1;
      best = Math.max(best, run);
    }
    if (best > 5) {
      const s = list[list.length - 1];
      r06.push({
        day: s.day,
        shift: s.shift,
        employees: [e],
        message: `${e} 连续工作 ${best} 天，超过连续 5 天上限。`,
        suggestions: [
          { label: `把 ${e} 移出${s.day_label}${s.shift}`, day: s.day, shift: s.shift, remove: e, add: null },
        ],
      });
    }
  }
  push('R-06', r06);

  // R-07 晚班后不接次日早班
  const r07: Violation[] = [];
  for (let i = 0; i < DAY_ORDER.length - 1; i += 1) {
    const night = bySlot.get(slotKey(DAY_ORDER[i], '晚班'));
    const morning = bySlot.get(slotKey(DAY_ORDER[i + 1], '早班'));
    if (!night || !morning) continue;
    for (const e of morning.employees.filter((x) => night.employees.includes(x))) {
      const alt = candidatesForSlot(meta, slots, morning).filter((c) => c !== e).slice(0, 1);
      r07.push({
        day: morning.day,
        shift: morning.shift,
        employees: [e],
        message: `${e} ${dayLabel(DAY_ORDER[i])}晚班 21:00 结束，${morning.day_label}早班 09:00 开始，间隔不足 12 小时。`,
        suggestions: [
          ...alt.map((c) => ({
            label: `改排 ${c} 到${morning.day_label}早班`,
            day: morning.day,
            shift: morning.shift,
            remove: e,
            add: c,
          })),
          {
            label: `把 ${e} 移出${morning.day_label}早班`,
            day: morning.day,
            shift: morning.shift,
            remove: e,
            add: null,
          },
        ],
      });
    }
  }
  push('R-07', r07);

  // R-08 请假 / 不可用
  const r08: Violation[] = [];
  for (const s of slots) {
    for (const e of s.employees) {
      const emp = empMap.get(e);
      if (!emp) continue;
      const onLeave = emp.leave_days.includes(s.day);
      const unavailable = !emp.available_days.includes(s.day);
      if (onLeave || unavailable) {
        r08.push({
          day: s.day,
          shift: s.shift,
          employees: [e],
          message: onLeave
            ? `${e} 已请假（${s.day_label}），不可排班。`
            : `${e} ${s.day_label}不可工作（不在可用日期内）。`,
          suggestions: [
            { label: `把 ${e} 移出${s.day_label}${s.shift}`, day: s.day, shift: s.shift, remove: e, add: null },
          ],
        });
      }
    }
  }
  push('R-08', r08);

  // R-09 技能不得凭空补充：校验被排员工是否都在员工档案内
  const r09: Violation[] = [];
  for (const s of slots) {
    for (const e of s.employees) {
      if (!empMap.has(e)) {
        r09.push({
          day: s.day,
          shift: s.shift,
          employees: [e],
          message: `${e} 不在员工档案中，技能无法核验。`,
          suggestions: [],
        });
      }
    }
  }
  push('R-09', r09);

  const validation: Validation = {
    passed: rules.every((r) => r.passed),
    violation_count: rules.reduce((n, r) => n + r.violations.length, 0),
    rules,
  };

  return { validation, soft_metrics: mockSoftMetrics(meta, slots) };
}

export function mockSoftMetrics(meta: Meta, slots: Slot[]) {
  const empMap = new Map(meta.employees.map((e) => [e.id, e]));
  let total = 0;
  let matched = 0;
  const counts = new Map<string, number>();
  let redundancy = 0;
  for (const s of slots) {
    for (const e of s.employees) {
      total += 1;
      if (empMap.get(e)?.preference === s.shift) matched += 1;
      counts.set(e, (counts.get(e) ?? 0) + 1);
    }
    redundancy += s.employees.filter((e) => empMap.get(e)?.skills.includes(SKILL_DRINK)).length / 2;
  }
  const values = meta.employees.map((e) => counts.get(e.id) ?? 0);
  const mean = values.reduce((a, b) => a + b, 0) / Math.max(1, values.length);
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, values.length);
  const balance = mean > 0 ? Math.max(0, 1 - Math.sqrt(variance) / (mean + 1)) : 0;
  return {
    preference_rate: total ? Number((matched / total).toFixed(2)) : 0,
    balance_score: Number(balance.toFixed(2)),
    skill_redundancy: Number((redundancy / Math.max(1, slots.length)).toFixed(1)),
  };
}
