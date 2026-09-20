import type { SchedulerConfig } from '../types';
import { RULE_TEMPLATES, paramLabel } from './ruleTemplates';
import { activeEmployees, isOvernight, parseTime, shiftHours } from './derive';

/**
 * 表单级即时校验。
 *
 * 与 `POST /api/config/validate` 的分工：
 * - 这里只管**结构与字面合法性**（空值、重复工号、时间格式、参数越界），纯本地、零延迟，
 *   打字时就要能看到红字；
 * - 供需是否排得出来（`supply_lt_demand` 等）由后端自检负责，因为那是排班语义，
 *   必须与求解器同源，不能让前端再写一份口径。
 *
 * 两者都不能少：只有后端自检的话，改一个字要等 400ms 往返才知道工号重复了。
 */

export const MAX_DAYS = 14;
export const MAX_SHIFTS = 4;

export type FormScope = 'scenario' | 'employees' | 'rules';

export interface FormIssue {
  scope: FormScope;
  /** 控件定位串，形如 `day:d1.label` / `shift:s2.end` / `emp:E01.id` / `rule:R-04.default` */
  target: string;
  message: string;
  /**
   * `error` 阻塞生成，`warn` 只提示。
   *
   * 这条分级是被真实操作打脸后补的：把周期从 7 天改成 3 天后，「最多连上 5 天」这类参数
   * 自然就大于周期长度，如果按错误处理，用户会发现自己什么都没填错却被禁止生成。
   * 判断标准只有一条 —— **这份配置到底还能不能排出班**：排不出来才是 error，
   * 「填了等于没限制」「数值可疑」只是 warn。
   */
  level: 'error' | 'warn';
}

/** 按控件定位串索引，供表单就地显示。保留整条 FormIssue 是为了让控件知道该显示红还是黄。 */
export function issuesByTarget(issues: FormIssue[]): Map<string, FormIssue[]> {
  const map = new Map<string, FormIssue[]>();
  for (const i of issues) {
    const list = map.get(i.target) ?? [];
    list.push(i);
    map.set(i.target, list);
  }
  return map;
}

export function blockingIssues(issues: FormIssue[]): FormIssue[] {
  return issues.filter((i) => i.level === 'error');
}

export function advisoryIssues(issues: FormIssue[]): FormIssue[] {
  return issues.filter((i) => i.level === 'warn');
}

export function checkConfig(config: SchedulerConfig): FormIssue[] {
  const out: FormIssue[] = [];
  const push = (scope: FormScope, target: string, message: string) =>
    out.push({ scope, target, message, level: 'error' });
  const warn = (scope: FormScope, target: string, message: string) =>
    out.push({ scope, target, message, level: 'warn' });

  const { days, shifts } = config.scenario;
  const dayIds = new Set(days.map((d) => d.id));
  const shiftIds = new Set(shifts.map((s) => s.id));

  /* ---------- 场景 ---------- */

  if (!config.scenario.name.trim()) push('scenario', 'scenario.name', '场景名称不能为空');
  if (days.length === 0) push('scenario', 'scenario.days', '至少要有 1 天');
  if (days.length > MAX_DAYS) push('scenario', 'scenario.days', `最多 ${MAX_DAYS} 天`);
  if (shifts.length === 0) push('scenario', 'scenario.shifts', '至少要有 1 个班次');
  if (shifts.length > MAX_SHIFTS) push('scenario', 'scenario.shifts', `最多 ${MAX_SHIFTS} 个班次`);

  const dayLabels = new Map<string, number>();
  for (const d of days) {
    if (!d.label.trim()) push('scenario', `day:${d.id}.label`, '显示名不能为空');
    dayLabels.set(d.label.trim(), (dayLabels.get(d.label.trim()) ?? 0) + 1);
  }
  for (const d of days) {
    if ((dayLabels.get(d.label.trim()) ?? 0) > 1 && d.label.trim()) {
      push('scenario', `day:${d.id}.label`, `与其他天重名（${d.label}），看板上会分不清`);
    }
  }

  const shiftNames = new Map<string, number>();
  for (const s of shifts) {
    if (!s.name.trim()) push('scenario', `shift:${s.id}.name`, '班次名称不能为空');
    shiftNames.set(s.name.trim(), (shiftNames.get(s.name.trim()) ?? 0) + 1);
    if (parseTime(s.start) === null) push('scenario', `shift:${s.id}.start`, '时间格式应为 HH:MM');
    if (parseTime(s.end) === null) push('scenario', `shift:${s.id}.end`, '时间格式应为 HH:MM');
    if (parseTime(s.start) !== null && parseTime(s.end) !== null) {
      if (s.start === s.end) {
        push('scenario', `shift:${s.id}.end`, '起止时间相同，无法判断是 0 小时还是 24 小时');
      } else if (shiftHours(s.start, s.end) > 16) {
        warn('scenario', `shift:${s.id}.end`, `单班 ${shiftHours(s.start, s.end)} 小时，请确认是否写错跨夜时间`);
      }
    }
  }
  for (const s of shifts) {
    if ((shiftNames.get(s.name.trim()) ?? 0) > 1 && s.name.trim()) {
      push('scenario', `shift:${s.id}.name`, `班次名称重复（${s.name}）`);
    }
  }

  /* ---------- 员工 ---------- */

  const idCount = new Map<string, number>();
  for (const e of config.employees) idCount.set(e.id.trim(), (idCount.get(e.id.trim()) ?? 0) + 1);

  const slotTotal = days.length * shifts.length;
  for (const e of config.employees) {
    const id = e.id.trim();
    if (!id) push('employees', `emp:${e.id}.id`, '工号不能为空');
    if (id && (idCount.get(id) ?? 0) > 1) {
      push('employees', `emp:${e.id}.id`, `工号 ${id} 重复，排班结果无法定位到人`);
    }
    if (!e.name.trim()) push('employees', `emp:${e.id}.name`, '姓名不能为空');
    if (e.role && !config.role_pool.includes(e.role)) {
      push('employees', `emp:${e.id}.role`, `角色「${e.role}」不在角色字典里`);
    }
    if (!e.role) push('employees', `emp:${e.id}.role`, '请选择角色');
    for (const skill of e.skills) {
      if (!config.skill_pool.includes(skill)) {
        // 对应契约的 unknown_skill：技能字典是 R-09「技能只能来自档案」的配置化形态，
        // 字典外的技能会让求解器凭空获得能力，必须拦在配置层
        push('employees', `emp:${e.id}.skills`, `技能「${skill}」不在技能字典里`);
      }
    }
    if (e.max_shifts !== null && e.max_shifts !== undefined) {
      if (!Number.isInteger(e.max_shifts) || e.max_shifts < 0) {
        push('employees', `emp:${e.id}.max_shifts`, '个人上限应为 ≥0 的整数，留空表示用全局规则');
      } else if (e.max_shifts > slotTotal) {
        warn('employees', `emp:${e.id}.max_shifts`, `本周期总共只有 ${slotTotal} 个班，填大于它等于不限制`);
      }
    }
    for (const u of e.unavailable) {
      if (!dayIds.has(u.day) || (u.shift !== null && !shiftIds.has(u.shift))) {
        push('employees', `emp:${e.id}.unavailable`, '存在指向已删除天/班次的不可用设置，请重新勾选');
        break;
      }
    }
  }

  /* ---------- 规则 ---------- */

  const activeCount = activeEmployees(config).length;
  const skillOwners = new Map<string, number>();
  const roleOwners = new Map<string, number>();
  for (const e of activeEmployees(config)) {
    for (const s of e.skills) skillOwners.set(s, (skillOwners.get(s) ?? 0) + 1);
    roleOwners.set(e.role, (roleOwners.get(e.role) ?? 0) + 1);
  }

  for (const rule of config.rules) {
    if (!rule.name.trim()) push('rules', `rule:${rule.id}.name`, '规则显示名不能为空');
    const p = rule.params;
    const tpl = RULE_TEMPLATES.find((t) => t.type === rule.type);
    if (!tpl) {
      push('rules', `rule:${rule.id}.name`, `未知规则类型 ${rule.type}，请删除后重新添加`);
      continue;
    }

    /**
     * 参数合法性（类型 / 取值范围）对停用的规则也要查。
     *
     * 原来这里是「停用就跳过」，理由是停用的规则不影响求解。但后端的配置自检按
     * `invalid_rule_params` 拦下这类参数且不看 enabled，于是会出现最糟的组合：
     * 本地一片绿、点生成拿一个 400。而这种配置几乎只有一个来源 —— 导入别人给的 JSON，
     * 用户自己根本没填过这个框。所以本地也拦，并在文案里说清「规则停用了但参数仍非法」。
     *
     * 分界仍然清楚：**能不能填**（下面这些）永远查；**填了会不会排不出来**
     * （下限超过总人数之类）只在规则生效时才算，否则一条停用规则会凭空挡住生成。
     */
    const live = rule.enabled || rule.locked;
    const badParam = (key: string, message: string) =>
      push(
        'rules',
        `rule:${rule.id}.${key}`,
        live ? message : `${message}（规则已停用，但非法参数仍会被后端自检拦下）`,
      );

    switch (rule.type) {
      case 'min_staff_per_shift': {
        for (const key of ['default', 'peak'] as const) {
          const v = p[key];
          if (!Number.isInteger(v) || (v ?? -1) < 0) {
            badParam(key, '应为 ≥0 的整数');
          } else if (live && (v ?? 0) > activeCount) {
            push(
              'rules',
              `rule:${rule.id}.${key}`,
              `下限 ${v} 人已超过启用员工总数 ${activeCount} 人，必定无解`,
            );
          }
        }
        (p.overrides ?? []).forEach((o, i) => {
          if (!dayIds.has(o.day) || !shiftIds.has(o.shift)) {
            badParam(`overrides.${i}`, '这条例外指向了已删除的天或班次');
          } else if (!Number.isInteger(o.min) || o.min < 0) {
            badParam(`overrides.${i}`, '例外人数应为 ≥0 的整数');
          } else if (live && o.min > activeCount) {
            push('rules', `rule:${rule.id}.overrides.${i}`, `超过启用员工总数 ${activeCount} 人`);
          }
        });
        break;
      }
      case 'require_attribute': {
        const pool = p.attr === 'role' ? config.role_pool : config.skill_pool;
        if (!p.value) badParam('value', '请选择要求的属性值');
        else if (!pool.includes(p.value)) {
          badParam('value', `「${p.value}」不在${p.attr === 'role' ? '角色' : '技能'}字典里`);
        } else if (live) {
          const owners = (p.attr === 'role' ? roleOwners : skillOwners).get(p.value) ?? 0;
          if (owners === 0) {
            push('rules', `rule:${rule.id}.value`, `没有任何启用员工${p.attr === 'role' ? '担任' : '具备'}「${p.value}」`);
          } else if ((p.min ?? 0) > owners) {
            push('rules', `rule:${rule.id}.min`, `全店只有 ${owners} 人满足，要求 ${p.min} 人排不出来`);
          }
        }
        if (!Number.isInteger(p.min) || (p.min ?? 0) < 1) {
          badParam('min', '应为 ≥1 的整数');
        }
        break;
      }
      case 'max_shifts_per_period': {
        if (!Number.isInteger(p.max) || (p.max ?? 0) < 1) {
          badParam('max', '应为 ≥1 的整数');
        } else if (live && (p.max ?? 0) > slotTotal) {
          warn('rules', `rule:${rule.id}.max`, `本周期只有 ${slotTotal} 个班，填大于它等于不限制`);
        }
        break;
      }
      case 'max_consecutive_days': {
        if (!Number.isInteger(p.max) || (p.max ?? 0) < 1) {
          badParam('max', '应为 ≥1 的整数');
        } else if (live && (p.max ?? 0) >= days.length) {
          warn('rules', `rule:${rule.id}.max`, `周期只有 ${days.length} 天，填 ≥${days.length} 等于不限制`);
        }
        break;
      }
      case 'min_rest_hours': {
        const h = p.hours;
        if (typeof h !== 'number' || Number.isNaN(h) || h < 0) {
          badParam('hours', '应为 ≥0 的小时数');
        } else if (!live) {
          break;
        } else if (h > 24) {
          warn('rules', `rule:${rule.id}.hours`, '超过 24 小时意味着任何人最多隔天上班，请确认');
        } else if (shifts.length > 0 && shifts.some((s) => isOvernight(s)) && h === 0) {
          warn('rules', `rule:${rule.id}.hours`, '存在跨夜班次时建议保留一个正数间隔');
        }
        break;
      }
      default:
        break;
    }
  }

  return out;
}

/* ---------------- 定位串 → 人话 ---------------- */

/**
 * 把控件定位串翻译成用户能对上的位置，例如
 * `rule:R-05.max` → 「规则 R-05「每人周期内最多 5 个班」· 每人一个周期最多几个班」。
 *
 * 表单里的错误就地显示在控件旁边，不需要这个；但自检面板会把同一批错误汇总成列表，
 * 那时候只剩一句「应为 ≥1 的整数」——用户不知道说的是哪条规则的哪个参数。
 * 导入一份别人给的 JSON 时尤其如此：他没填过任何框。
 */
export function targetLabel(config: SchedulerConfig, target: string): string | null {
  const dot = target.indexOf('.');
  const head = dot < 0 ? target : target.slice(0, dot);
  const field = dot < 0 ? '' : target.slice(dot + 1);

  if (head.startsWith('rule:')) {
    const id = head.slice(5);
    const rule = config.rules.find((r) => r.id === id);
    const name = rule?.name.trim() ? `规则 ${id}「${rule.name.trim()}」` : `规则 ${id}`;
    if (!field || field === 'name') return `${name} · 显示名`;
    const ov = /^overrides\.(\d+)$/.exec(field);
    if (ov && rule) {
      return `${name} · ${paramLabel(rule.type, 'overrides')}（第 ${Number(ov[1]) + 1} 条）`;
    }
    return rule ? `${name} · ${paramLabel(rule.type, field)}` : `${name} · ${field}`;
  }

  if (head.startsWith('emp:')) {
    const id = head.slice(4);
    const emp = config.employees.find((e) => e.id === id);
    const who = emp?.name.trim() ? `员工 ${id}（${emp.name.trim()}）` : `员工 ${id}`;
    return `${who} · ${EMP_FIELD[field] ?? field}`;
  }

  if (head.startsWith('day:')) {
    const id = head.slice(4);
    const day = config.scenario.days.find((d) => d.id === id);
    return `第 ${config.scenario.days.findIndex((d) => d.id === id) + 1} 天${day ? `（${day.label}）` : ''}`;
  }

  if (head.startsWith('shift:')) {
    const id = head.slice(6);
    const shift = config.scenario.shifts.find((s) => s.id === id);
    return `班次${shift ? `「${shift.name}」` : ` ${id}`} · ${SHIFT_FIELD[field] ?? field}`;
  }

  return SCENARIO_FIELD[target] ?? null;
}

const EMP_FIELD: Record<string, string> = {
  id: '工号',
  name: '姓名',
  role: '角色',
  skills: '技能',
  max_shifts: '个人班次上限',
  unavailable: '不可排班时段',
};

const SHIFT_FIELD: Record<string, string> = {
  name: '班次名称',
  start: '开始时间',
  end: '结束时间',
};

const SCENARIO_FIELD: Record<string, string> = {
  'scenario.name': '场景名称',
  'scenario.days': '排几天',
  'scenario.shifts': '每天有哪些班',
};

/* ---------------- 导入 JSON ---------------- */

export interface ParseResult {
  config?: SchedulerConfig;
  error?: string;
}

/**
 * 导入备份 JSON。刻意做成「宁可拒绝也不修补」：
 * 半自动修补一份结构不对的配置，只会让用户拿到一份他没预期的排班场景。
 * 但缺省的可选字段会补全 —— 那是版本演进的正常情况，不是脏数据。
 */
export function parseConfigJson(text: string): ParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { error: '不是合法的 JSON 文件' };
  }
  // 兼容直接保存 `GET /api/config/default` 响应体（外层包了一层 config）的情况
  const body = (raw as { config?: unknown })?.config ?? raw;
  const c = body as Partial<SchedulerConfig>;
  if (!c || typeof c !== 'object') return { error: 'JSON 顶层不是对象' };
  if (c.version !== 1) {
    return { error: `不支持的配置版本：${String(c.version)}（当前只认 version: 1）` };
  }
  if (!c.scenario || !Array.isArray(c.scenario.days) || !Array.isArray(c.scenario.shifts)) {
    return { error: '缺少 scenario.days / scenario.shifts' };
  }
  if (!Array.isArray(c.employees)) return { error: '缺少 employees 数组' };
  if (!Array.isArray(c.rules)) return { error: '缺少 rules 数组' };

  const config: SchedulerConfig = {
    version: 1,
    scenario: {
      name: c.scenario.name ?? '导入的场景',
      days: c.scenario.days.map((d, i) => ({
        id: d.id ?? `d${i + 1}`,
        label: d.label ?? `第 ${i + 1} 天`,
        peak: Boolean(d.peak),
      })),
      shifts: c.scenario.shifts.map((s, i) => ({
        id: s.id ?? `s${i + 1}`,
        name: s.name ?? `班次 ${i + 1}`,
        start: s.start ?? '09:00',
        end: s.end ?? '17:00',
        // hours 由 start/end 回算而不是信任文件里的值：手改过的备份很容易两者不一致
        hours: shiftHours(s.start ?? '09:00', s.end ?? '17:00'),
      })),
    },
    skill_pool: Array.isArray(c.skill_pool) ? c.skill_pool : [],
    role_pool: Array.isArray(c.role_pool) ? c.role_pool : [],
    employees: c.employees.map((e, i) => ({
      id: e.id ?? `E${String(i + 1).padStart(2, '0')}`,
      name: e.name ?? '',
      role: e.role ?? '',
      skills: Array.isArray(e.skills) ? e.skills : [],
      unavailable: Array.isArray(e.unavailable)
        ? e.unavailable.map((u) => ({ day: u.day, shift: u.shift ?? null }))
        : [],
      max_shifts: e.max_shifts ?? null,
      preferred_shifts: Array.isArray(e.preferred_shifts) ? e.preferred_shifts : [],
      active: e.active !== false,
    })),
    rules: c.rules.map((r, i) => ({
      id: r.id ?? `C-${i + 1}`,
      type: r.type,
      name: r.name ?? r.type,
      enabled: r.enabled !== false,
      locked: Boolean(r.locked),
      params: r.params ?? {},
    })),
  };

  const unknown = config.rules.find((r) => !RULE_TEMPLATES.some((t) => t.type === r.type));
  if (unknown) return { error: `包含未知规则类型：${String(unknown.type)}` };
  if (config.scenario.days.length === 0 || config.scenario.shifts.length === 0) {
    return { error: '天数或班次为 0，这份配置无法排班' };
  }
  return { config };
}
