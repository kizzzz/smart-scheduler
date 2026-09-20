import type {
  Capacity,
  ConfigIssue,
  ConfigValidateResponse,
  RuleDef,
  SchedulerConfig,
} from '../types';
import { defaultConfig } from '../config/defaults';
import {
  computeCapacity,
  dayEligible,
  eligibleFor,
  enabledRules,
  hasAttribute,
  hasRule,
  isOvernight,
  minRequiredFor,
  activeEmployees,
  perDayCapacity,
} from '../config/derive';
import { paramLabel } from '../config/ruleTemplates';

/**
 * `GET /api/config/default` 与 `POST /api/config/validate` 的 mock 实现。
 *
 * 为什么要在前端写一份自检：后端接口还没上线，而「在配置页就知道能不能排出来」
 * 是这次改造的关键体验点（契约 2 节第 3 条），不能等后端好了才开始验证交互。
 * 这份实现严格照契约 5.2 的 code 与字段名产出结论，接口通了直接换数据源即可，
 * 界面不用动。
 *
 * 注意：它**不是**排班可行性的权威判定，只覆盖契约列出的「一定导致无解」的那几类。
 * 真正的求解仍在后端。
 */

export const mockDefaultConfig = defaultConfig;

/**
 * 同类问题最多逐条列几条。与后端 `config_check.MAX_PER_CODE` 保持一致：
 * 一个「全员周一不可用」的误操作能刷出几十条同质 error，把真正要先处理的结构性问题挤出视野。
 */
const MAX_PER_CODE = 4;

function truncate(list: ConfigIssue[], code: string, total: number): void {
  if (total > MAX_PER_CODE) {
    list.push({
      code,
      message: `另有 ${total - MAX_PER_CODE} 处同类问题未逐条列出（共 ${total} 处）`,
      fix: '先处理上面列出的几处，保存后会重新自检',
    });
  }
}

const ceilDiv = (a: number, b: number) => Math.ceil(a / Math.max(1, b));

/**
 * 规则参数的合法性（后端 `invalid_rule_params`）。
 *
 * 和表单的即时校验重叠是故意的：配置可以从「导入 JSON」进来，那条路径绕过了所有控件，
 * 所以这一层必须独立存在。也正因为来源是别人给的文件，文案必须定位到**哪条规则的哪个参数、
 * 现在填的是什么**——一句「配置非法」只会让用户去问同事。
 *
 * 不看 `enabled`：停用规则的非法参数同样会被后端拦下，这里放过它等于制造一次注定的 400。
 */
function ruleParamIssues(config: SchedulerConfig): ConfigIssue[] {
  const out: ConfigIssue[] = [];

  const bad = (rule: RuleDef, key: string, shown: string, expect: string) => {
    const live = rule.enabled || rule.locked;
    out.push({
      code: 'invalid_rule_params',
      message:
        `规则 ${rule.id}「${rule.name}」的「${paramLabel(rule.type, key)}」是 ${shown}，${expect}` +
        (live ? '' : '（这条规则已停用，但非法参数同样会被拦下）'),
      where: { rule_id: rule.id },
      fix: `在「规则」里把这条规则的「${paramLabel(rule.type, key)}」改成合法值，或直接删掉这条规则`,
    });
  };
  const shownOf = (v: unknown) =>
    v === undefined || v === null || v === '' ? '空的' : String(v);

  for (const rule of config.rules) {
    const p = rule.params;
    switch (rule.type) {
      case 'min_staff_per_shift': {
        for (const key of ['default', 'peak'] as const) {
          const v = p[key];
          if (v !== undefined && (!Number.isInteger(v) || v < 0)) {
            bad(rule, key, shownOf(v), '应为 ≥0 的整数');
          }
        }
        (p.overrides ?? []).forEach((o, i) => {
          if (!Number.isInteger(o.min) || o.min < 0) {
            bad(rule, 'overrides', `第 ${i + 1} 条例外的 ${shownOf(o.min)}`, '应为 ≥0 的整数');
          }
        });
        break;
      }
      case 'require_attribute': {
        if (!String(p.value ?? '').trim()) bad(rule, 'value', '空的', '必须指明要求哪项技能或角色');
        if (!Number.isInteger(p.min) || (p.min ?? 0) < 1) {
          bad(rule, 'min', shownOf(p.min), '应为 ≥1 的整数（0 等于没有要求）');
        }
        break;
      }
      case 'max_shifts_per_period': {
        if (!Number.isInteger(p.max) || (p.max ?? 0) < 1) {
          bad(rule, 'max', shownOf(p.max), '应为 ≥1 的整数，否则谁都不能排班');
        }
        break;
      }
      case 'max_consecutive_days': {
        if (!Number.isInteger(p.max) || (p.max ?? 0) < 1) {
          bad(rule, 'max', shownOf(p.max), '应为 ≥1 的整数，否则谁都不能排班');
        }
        break;
      }
      case 'min_rest_hours': {
        const h = p.hours;
        if (typeof h !== 'number' || Number.isNaN(h) || h < 0) {
          bad(rule, 'hours', shownOf(h), '应为 ≥0 的小时数');
        }
        break;
      }
      default:
        break;
    }
  }

  return out;
}

export function mockValidateConfig(config: SchedulerConfig): ConfigValidateResponse {
  const errors: ConfigIssue[] = [];
  const warnings: ConfigIssue[] = [];
  const { days, shifts } = config.scenario;

  const capacity = computeCapacity(config);

  if (days.length === 0 || shifts.length === 0) {
    errors.push({
      code: 'empty_scenario',
      message: days.length === 0 ? '周期天数为 0，没有可排班的日期' : '没有配置任何班次',
      fix: '回到「排班场景」，至少配置 1 天和 1 个班次',
    });
    // 维度为 0 时后面的供需判断全是 0 比 0，继续跑只会产出一堆没意义的错误
    return { ok: false, errors, warnings, capacity };
  }

  const active = activeEmployees(config);

  if (active.length === 0) {
    errors.push({
      code: 'no_employees',
      message: config.employees.length ? '所有员工都处于停用状态，没有人可排班' : '员工名单是空的',
      fix: config.employees.length ? '在「员工」里启用至少一名员工' : '在「员工」里添加至少一名员工',
    });
  }

  /* ---------- 员工档案本身的问题 ---------- */

  const seen = new Map<string, number>();
  for (const e of config.employees) seen.set(e.id, (seen.get(e.id) ?? 0) + 1);
  for (const [id, count] of seen) {
    if (count > 1) {
      errors.push({
        code: 'duplicate_employee_id',
        message: `工号 ${id} 出现了 ${count} 次，排班结果无法定位到具体的人`,
        where: { employee_id: id },
        fix: '把重复的工号改成唯一值，或删掉多余的那一行',
      });
    }
  }

  for (const e of config.employees) {
    for (const skill of e.skills) {
      if (!config.skill_pool.includes(skill)) {
        errors.push({
          code: 'unknown_skill',
          message: `${e.id}${e.name ? `（${e.name}）` : ''} 的技能「${skill}」不在技能字典里`,
          where: { employee_id: e.id },
          fix: `把「${skill}」加进技能字典，或从该员工身上移除`,
        });
      }
    }
  }

  /* ---------- 规则本身的问题 ---------- */

  errors.push(...ruleParamIssues(config));

  const ruleSeen = new Map<string, number>();
  for (const r of config.rules) ruleSeen.set(r.id, (ruleSeen.get(r.id) ?? 0) + 1);
  for (const [id, count] of ruleSeen) {
    if (count > 1) {
      warnings.push({
        code: 'duplicate_rule_id',
        message: `规则编号 ${id} 出现了 ${count} 次，校验报告里同一编号只会显示一条`,
        where: { rule_id: id },
        fix: '改成唯一编号（用户自建规则建议用 C-1、C-2…）',
      });
    }
  }

  for (const r of config.rules) {
    if (r.locked && !r.enabled) {
      warnings.push({
        code: 'locked_rule_forced',
        message: `规则 ${r.id}「${r.name}」是系统内建的数据完整性规则，即使停用也会继续执行`,
        where: { rule_id: r.id },
        fix: '无需处理；这条规则保证不会给不可用时段或档案外的员工排班',
      });
    }
  }

  /* ---------- 容量三层：单格 → 单日 → 全周期 ---------- */

  /**
   * 结构性问题（没人、工号重复、参数非法）没清完就不算供需。
   *
   * 不是为了少写几条，而是那时候的数字根本不可信：一条 `max: 0` 的班次上限会让供给算成 0，
   * 于是「总量不足」「每天排不开」全都跟着报一遍，把真正要先改的那条挤出视野。
   * 后端也是同一顺序（先结构、再容量）。
   */
  if (errors.length === 0) checkCapacity(config, capacity, errors, warnings);

  /* ---------- 与供需无关的提醒 ---------- */

  if (!hasRule(config, 'min_staff_per_shift')) {
    warnings.push({
      code: 'no_min_staff_rule',
      message: '没有启用「每班总人数下限」，求解器只会满足技能类要求，可能给某些班只排 1 个人',
      fix: '建议启用人数下限规则',
    });
  }

  const inactive = config.employees.length - active.length;
  if (inactive > 0) {
    warnings.push({
      code: 'inactive_employees',
      message: `有 ${inactive} 名员工处于停用状态，不参与本次排班（档案与历史排班仍保留）`,
    });
  }

  for (const skill of config.skill_pool) {
    if (!active.some((e) => e.skills.includes(skill))) {
      warnings.push({
        code: 'unused_skill',
        message: `技能「${skill}」目前没有任何启用员工具备`,
        fix: '确认是否漏填了员工技能，或把它从技能字典里删掉',
      });
    }
  }

  for (const shift of shifts) {
    if (isOvernight(shift)) {
      warnings.push({
        code: 'overnight_shift',
        message: `「${shift.name}」${shift.start}–${shift.end} 跨夜，休息间隔会按到次日计算（共 ${shift.hours} 小时）`,
      });
    }
  }

  // 高峰日配置得比普通日还低，大概率是填反了——不阻塞，但值得问一句
  for (const rule of enabledRules(config, 'min_staff_per_shift')) {
    const base = rule.params.default ?? 0;
    const peak = rule.params.peak ?? 0;
    if (days.some((d) => d.peak) && peak < base) {
      warnings.push({
        code: 'peak_lt_default',
        message: `高峰日下限（${peak} 人）低于普通日（${base} 人），确认不是填反了？`,
        where: { rule_id: rule.id },
      });
    }
  }

  return { ok: errors.length === 0, errors, warnings, capacity };
}

/**
 * 容量三层：单格 → 单日 → 全周期。
 *
 * 层次顺序不是排版口味：单格不足最可操作（改这一格的下限或不可用设置），全周期总量不足
 * 只能靠加人。同一份配置常常三层同时命中，先报最具体的那层，用户第一步才会改对地方。
 * 同一天被格子级命中过就不再报按天那条 —— 两条 error 指着同一天，只会让人以为有两个问题。
 */
function checkCapacity(
  config: SchedulerConfig,
  capacity: Capacity,
  errors: ConfigIssue[],
  warnings: ConfigIssue[],
): void {
  const { days, shifts } = config.scenario;
  const dayLabel = (id: string) => days.find((d) => d.id === id)?.label ?? id;
  const shiftName = (id: string) => shifts.find((s) => s.id === id)?.name ?? id;
  const at = (day: string, shift: string) => `${dayLabel(day)}${shiftName(shift)}`;
  const active = activeEmployees(config);
  const hitDays = new Set<string>();

  /* ---------- 单格 ---------- */

  const slotShort = capacity.per_slot.filter((s) => s.min_required > 0 && s.eligible < s.min_required);
  for (const slot of slotShort.slice(0, MAX_PER_CODE)) {
    errors.push({
      code: 'supply_lt_demand',
      message: `${at(slot.day, slot.shift)}需要 ${slot.min_required} 人，但当天可排班的员工只有 ${slot.eligible} 人`,
      where: { day: slot.day, shift: slot.shift },
      fix: '降低该班人数下限，或减少当天的不可用设置',
    });
  }
  truncate(errors, 'supply_lt_demand', slotShort.length);
  for (const slot of slotShort) hitDays.add(slot.day);

  /* ---------- 资质：先看单格，再看按天 ---------- */

  /** 参数非法的资质规则不参与容量判定：它已经被 invalid_rule_params 拦下了 */
  const attrRules = enabledRules(config, 'require_attribute')
    .map((rule) => ({
      rule,
      attr: rule.params.attr ?? ('skill' as const),
      value: rule.params.value ?? '',
      min: rule.params.min ?? 0,
    }))
    .filter((x) => x.value && x.min > 0);

  const attrNoun = (attr: 'skill' | 'role', value: string) =>
    attr === 'role' ? `担任角色「${value}」` : `具备技能「${value}」`;

  for (const { rule, attr, value, min } of attrRules) {
    const owners = active.filter((e) => hasAttribute(e, attr, value));
    if (owners.length === 0) {
      errors.push({
        code: 'attribute_absent',
        message: `规则 ${rule.id}「${rule.name}」要求每班 ≥${min} 名${attrNoun(attr, value)}的员工，但没有任何启用员工${attr === 'role' ? '担任' : '具备'}它`,
        where: { rule_id: rule.id },
        fix: `给至少 ${min} 名员工补上这一项，或停用规则 ${rule.id}`,
      });
      continue;
    }

    const lacking = days.flatMap((day) =>
      shifts
        .map((shift) => ({
          day: day.id,
          shift: shift.id,
          have: eligibleFor(config, day.id, shift.id).filter((e) => hasAttribute(e, attr, value)).length,
        }))
        .filter((row) => row.have < min),
    );
    for (const row of lacking.slice(0, MAX_PER_CODE)) {
      errors.push({
        code: 'attribute_supply_lt_demand',
        message: `${at(row.day, row.shift)}要求 ${min} 名${attrNoun(attr, value)}的员工，但该班只有 ${row.have} 名可排`,
        where: { day: row.day, shift: row.shift, rule_id: rule.id },
        fix: `调低规则 ${rule.id} 的人数要求，或让${attrNoun(attr, value)}的员工在${dayLabel(row.day)}可排班`,
      });
    }
    truncate(errors, 'attribute_supply_lt_demand', lacking.length);
    for (const row of lacking) hitDays.add(row.day);
  }

  /* ---------- 单日 ---------- */

  /**
   * 每格都够人、总量也够，某一天却依然排不开：一人一天最多顶 `cover` 个班，
   * 当天可排人次的上界就是「人头数 × cover」。判定门槛取「数学上可证明」而不是
   * 「`one_shift_per_day` 开没开」——默认的 09–17 / 13–21 本身重叠，规则关着也是一天一个班。
   */
  const perDay = perDayCapacity(config);
  const cover = perDay[0]?.cover ?? 1;
  const oneShiftRule = enabledRules(config, 'one_shift_per_day')[0];
  const coverNote = oneShiftRule
    ? `规则 ${oneShiftRule.id} 限制每人每天最多 1 个班`
    : cover === 1
      ? '班次时间重叠或最小休息间隔限制，一人一天最多 1 个班'
      : `一人一天最多 ${cover} 个班`;
  const coverFix = `降低当天的人数下限${
    oneShiftRule ? `、停用规则 ${oneShiftRule.id}（允许一人一天上多个班）` : ''
  }，或给这天补上可排班的员工`;

  /**
   * 资质的按天下界。
   *
   * 典型场景：两个班各要 1 名收银，全店只有 1 个收银员。**单格永远够**（那一个人哪个班都能上），
   * 但一个人分不成两半 —— 两个班需要两个不同的人。所以要求：
   * 当天持证人数 ≥ ⌈每班要求 × 班次数 / 一人一天最多几个班⌉。
   */
  const attrShortDays: Array<{
    day: string;
    have: number;
    required: number;
    rule: RuleDef;
    noun: string;
    value: string;
  }> = [];
  for (const d of days) {
    if (hitDays.has(d.id)) continue;
    const pool = dayEligible(config, d.id);
    for (const { rule, attr, value, min } of attrRules) {
      const required = ceilDiv(min * shifts.length, cover);
      const have = pool.filter((e) => hasAttribute(e, attr, value)).length;
      if (have >= required) continue;
      attrShortDays.push({ day: d.id, have, required, rule, noun: attrNoun(attr, value), value });
      // 一天只报一条：同一天多项资质都不够时，先解决最先命中的那条
      break;
    }
  }
  for (const row of attrShortDays.slice(0, MAX_PER_CODE)) {
    errors.push({
      code: 'daily_attribute_capacity_lt_demand',
      message:
        `${dayLabel(row.day)}：全天只有 ${row.have} 名${row.noun}的员工可排班，` +
        `而当天 ${shifts.length} 个班合起来需要 ${row.required} 名不同的人 —— 一个人分不成两半，` +
        `同一时段只能待在一个班上（${coverNote}）`,
      where: { day: row.day, rule_id: row.rule.id },
      fix: `给更多员工补上「${row.value}」、减少当天的不可用设置，或调低规则 ${row.rule.id} 的人数要求${
        oneShiftRule ? `；也可以停用规则 ${oneShiftRule.id}，让一个人一天顶两个班` : ''
      }`,
    });
  }
  truncate(errors, 'daily_attribute_capacity_lt_demand', attrShortDays.length);
  for (const row of attrShortDays) hitDays.add(row.day);

  const dayShort = perDay.filter((d) => d.demand > 0 && !hitDays.has(d.day) && d.capacity < d.demand);
  for (const d of dayShort.slice(0, MAX_PER_CODE)) {
    errors.push({
      code: 'daily_capacity_lt_demand',
      message: `${dayLabel(d.day)}：全天可排班 ${d.available} 人，当天各班共需 ${d.demand} 人次（${coverNote}）`,
      where: { day: d.day, rule_id: oneShiftRule?.id },
      fix: coverFix,
    });
  }
  truncate(errors, 'daily_capacity_lt_demand', dayShort.length);

  const dayTight = perDay.filter(
    (d) => d.demand > 0 && !hitDays.has(d.day) && d.capacity === d.demand,
  );
  for (const d of dayTight.slice(0, MAX_PER_CODE)) {
    // 刚好够仍然排得出来，报 error 会拦住一份能用的配置，所以只是 warning。
    // cover > 1 时「人数」和「人次」不是同一个量，文案必须把折算写出来，否则看着像笔误
    const headcount =
      d.capacity === d.available
        ? `全天可排班 ${d.available} 人`
        : `全天可排班 ${d.available} 人最多承担 ${d.capacity} 人次`;
    warnings.push({
      code: 'no_daily_headroom',
      message: `${dayLabel(d.day)}：${headcount}，恰好等于当天需求 ${d.demand} 人次（${coverNote}），这天一个人都不能请假`,
      where: { day: d.day },
      fix: '给这天多留 1～2 名可排班的员工，或降低当天的人数下限',
    });
  }
  truncate(warnings, 'no_daily_headroom', dayTight.length);

  /* ---------- 全周期 ---------- */

  if (capacity.demand_person_shifts > capacity.supply_person_shifts) {
    errors.push({
      code: 'capacity_lt_total_demand',
      message: `总需求 ${capacity.demand_person_shifts} 人次，但全员上限加起来只有 ${capacity.supply_person_shifts} 人次`,
      fix: '放宽「每人最多几个班」、增加员工，或降低人数下限',
    });
  } else if (capacity.demand_person_shifts > 0) {
    if (capacity.supply_person_shifts === capacity.demand_person_shifts) {
      warnings.push({
        code: 'no_headroom',
        message: '总供给刚好等于总需求，任何一人请假都会导致无解',
        fix: '建议至少留 10% 冗余',
      });
    } else if (capacity.headroom_pct < 10) {
      warnings.push({
        code: 'no_headroom',
        message: `总供给只比总需求多 ${capacity.headroom_pct}%，一两个人请假就可能排不出来`,
        fix: '建议至少留 10% 冗余',
      });
    }
  }

  // 单格零冗余：总量够但某一格刚好卡死，是实际排班里最常见的「看起来没问题但一动就崩」
  const tight = capacity.per_slot.filter((s) => s.min_required > 0 && s.eligible === s.min_required);
  if (tight.length > 0) {
    warnings.push({
      code: 'slot_no_headroom',
      message: `${tight
        .slice(0, 3)
        .map((s) => at(s.day, s.shift))
        .join('、')}${tight.length > 3 ? ` 等 ${tight.length} 格` : ''}的可排人数刚好等于下限，没有任何替换余地`,
      where: tight[0] ? { day: tight[0].day, shift: tight[0].shift } : null,
      fix: '给这些格子多留 1–2 名可排班的员工',
    });
  }
}

/**
 * `POST /api/generate` 在求解前自检失败时的 400 body（mock 工具栏的「生成前被自检拦下」）。
 *
 * 为什么不直接复用 `mockValidateConfig`：页面上的自检和它同源，配置真有问题时「生成」按钮
 * 早就被拦住了，这条分支永远走不到。但真后端算得比前端细（它有完整的求解预检），
 * 完全可能在前端一片绿的情况下返回 400 —— 那一刻页面绝不能退化成一句「请求失败」。
 *
 * 所以这里故意造一条**前端认不出 code** 的结论：它只带 `where.rule_id`，
 * 用来验证兜底定位仍然有效（能跳到「规则」），文案仍然只展示 message + fix。
 */
export function mockPrecheckBlocked(config: SchedulerConfig): ConfigValidateResponse {
  const base = mockValidateConfig(config);
  if (base.errors.length > 0) return base;

  const day = config.scenario.days[1] ?? config.scenario.days[0];
  const rest = config.rules.find((r) => r.type === 'min_rest_hours' && (r.enabled || r.locked));
  const staff = config.rules.find((r) => r.type === 'min_staff_per_shift' && (r.enabled || r.locked));
  const where = rest ? { day: day?.id, rule_id: rest.id } : { day: day?.id };
  return {
    ok: false,
    errors: [
      {
        code: 'precheck_infeasible',
        message:
          `求解器预检：${day?.label ?? '某一天'}无法同时满足` +
          `${staff ? `「${staff.name}」与` : ''}${rest ? `「${rest.name}」` : '现有硬规则'}` +
          '，继续求解只会在几十秒后返回一句「无解」',
        where,
        fix: rest
          ? `把这天的人数下限降 1 人，或把规则 ${rest.id} 的休息间隔调小 1 小时`
          : '把这天的人数下限降 1 人',
      },
    ],
    warnings: base.warnings,
    capacity: base.capacity,
  };
}

/** 供页面在真实接口 400 时复用同一套渲染：把后端 body 归一成 ConfigValidateResponse */
export function normalizeConfigValidation(body: unknown): ConfigValidateResponse | null {
  const rec = body as Partial<ConfigValidateResponse> | null;
  if (!rec || typeof rec !== 'object' || !Array.isArray(rec.errors)) return null;
  return {
    ok: Boolean(rec.ok),
    errors: rec.errors ?? [],
    warnings: Array.isArray(rec.warnings) ? rec.warnings : [],
    capacity: rec.capacity ?? null,
  };
}

/** 未使用但保持导出：外部想快速判断某份配置能否进生成时不必自己数 errors */
export function configBlocked(result: ConfigValidateResponse | null): boolean {
  return Boolean(result && result.errors.length > 0);
}

export function minRequiredResolver(config: SchedulerConfig) {
  return (day: string, shift: string) => minRequiredFor(config, day, shift);
}
