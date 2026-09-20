import type { RuleDef, RuleParams, RuleType, SchedulerConfig } from '../types';
import { nextRuleId } from './derive';

/**
 * 规则模板库（契约 3.1 / 4 节的 8 类）。
 *
 * 这里是「规则是参数化模板、不是自由文本」这个取舍的落点：界面只提供从模板新建，
 * 不提供自然语言输入框。代价是模板外的诉求表达不了，收益是校验器仍然是唯一真相源，
 * 每条违规都能定位到具体格子。
 */
export interface RuleTemplate {
  type: RuleType;
  /** 模板库里的名字 */
  label: string;
  /** 一句话语义，给「新增规则」选择器用 */
  summary: string;
  /** 为什么会有这条规则 / 为什么它被锁定，展示在卡片上，避免用户把它当 bug */
  why: string;
  /** 对应的原规则编号，方便老用户对齐 */
  origin: string;
  locked: boolean;
  /** 是否允许存在多个实例：「每班至少 N 人具备某属性」天然需要多条，其余只需一条 */
  multiple: boolean;
  defaultName: string;
  defaultParams: (config: SchedulerConfig) => RuleParams;
}

export const RULE_TEMPLATES: RuleTemplate[] = [
  {
    type: 'min_staff_per_shift',
    label: '每班总人数下限',
    summary: '规定每个班至少要有几个人，高峰日可以单独加人，个别格子还能开例外',
    why: '原「工作日 4 人、周末 6 人」的配置化形态。高峰日不再等于周末，由你在第一步勾选。',
    origin: 'R-04',
    locked: false,
    multiple: false,
    defaultName: '每班总人数下限',
    defaultParams: () => ({ default: 3, peak: 4, overrides: [] }),
  },
  {
    type: 'require_attribute',
    label: '每班至少 N 人具备某属性',
    summary: '按技能或角色要求配置，例如「每班至少 1 名店长值守」',
    why: '原 R-01/02/03 三条技能要求的统一形态，所以这类规则可以建多条。',
    origin: 'R-01 / R-02 / R-03',
    locked: false,
    multiple: true,
    defaultName: '每班至少 1 人具备某技能',
    defaultParams: (config) => ({ attr: 'skill', value: config.skill_pool[0] ?? '', min: 1 }),
  },
  {
    type: 'max_shifts_per_period',
    label: '每人周期内最多几个班',
    summary: '控制单人总工作量，个别员工可在第二步单独覆盖',
    why: '原「每周 ≤40 小时（≤5 个班）」。班次时长可配后，工时上限按班数表达更直观。',
    origin: 'R-05',
    locked: false,
    multiple: false,
    defaultName: '每人周期内最多 5 个班',
    defaultParams: () => ({ max: 5 }),
  },
  {
    type: 'max_consecutive_days',
    label: '最多连续工作天数',
    summary: '防止有人被连排一整个周期',
    why: '原 R-06，语义不变。',
    origin: 'R-06',
    locked: false,
    multiple: false,
    defaultName: '不得连续工作超过 5 天',
    defaultParams: () => ({ max: 5 }),
  },
  {
    type: 'min_rest_hours',
    label: '相邻班次最小休息间隔',
    summary: '间隔不足设定小时数视为违规，恰好等于视为合规；跨夜班次也算得准',
    why: '原实现是字符串比较「晚班接次日早班」，只在两班制下成立。改成按小时计算后，三班制、跨夜班也适用。默认 13 小时：原 9-17/13-21 两班制下晚班 21:00 接次日早班 09:00 正好 12 小时，低于 13 仍算违规，与旧行为等价；取 12 反而会因「恰好等于视为合规」放过这种接班。',
    origin: 'R-07',
    locked: false,
    multiple: false,
    defaultName: '相邻班次至少休息 13 小时',
    defaultParams: () => ({ hours: 13 }),
  },
  {
    type: 'one_shift_per_day',
    label: '每人每天最多一个班',
    summary: '关掉它就允许一天上两班（例如三班制门店的连班）',
    why: '旧求解器把这条当隐式假设，从未暴露成规则。三班制门店可能允许一天两班，所以必须可关。',
    origin: '原隐式假设',
    locked: false,
    multiple: false,
    defaultName: '每人每天最多一个班',
    defaultParams: () => ({}),
  },
  {
    type: 'respect_unavailability',
    label: '不可用时段不得排班',
    summary: '员工在第二步标记的不可排班时段，求解器绝不会碰',
    why: '系统内建，不可关闭：关掉它等于允许给请假的人排班，排班结果会直接失去可执行性，也没有任何合法的业务场景需要这么做。要临时放开，请去第二步改那个人的不可用时段。',
    origin: 'R-08',
    locked: true,
    multiple: false,
    defaultName: '不可用时段绝不排班',
    defaultParams: () => ({}),
  },
  {
    type: 'skill_source_integrity',
    label: '技能只能来自员工档案',
    summary: '模型不得给员工凭空添加技能',
    why: '系统内建，不可关闭：这是反幻觉护栏的地基。一旦允许技能来自模型输出，「每班至少 1 名店长值守」这类约束就变成了模型的自我声明，校验结论不再可信。',
    origin: 'R-09',
    locked: true,
    multiple: false,
    defaultName: '技能只能来自员工档案',
    defaultParams: () => ({}),
  },
];

export function templateOf(type: RuleType): RuleTemplate | undefined {
  return RULE_TEMPLATES.find((t) => t.type === type);
}

/**
 * 参数的人话名字。
 *
 * 存在的理由是「导入 JSON」这条入口：表单里参数非法会就地显示红字，用户看得见是哪个框；
 * 但从同事手里拿到的一份 JSON 绕过了所有控件，自检只能用文字说话。这时候
 * 「配置非法」是没用的，「规则 R-05 的『每人一个周期最多几个班』填了 0」才是可执行的。
 */
const PARAM_LABEL: Record<string, string> = {
  'min_staff_per_shift.default': '普通日每班至少几人',
  'min_staff_per_shift.peak': '高峰日每班至少几人',
  'min_staff_per_shift.overrides': '单格例外的人数',
  'require_attribute.attr': '按技能还是角色要求',
  'require_attribute.value': '要求的技能 / 角色',
  'require_attribute.min': '每班至少几人具备',
  'max_shifts_per_period.max': '每人一个周期最多几个班',
  'max_consecutive_days.max': '最多连续工作几天',
  'min_rest_hours.hours': '相邻班次最少休息几小时',
};

export function paramLabel(type: RuleType, key: string): string {
  return PARAM_LABEL[`${type}.${key}`] ?? key;
}

/** 新增规则时的可用性：单实例模板已存在就不给再加，否则用户会得到两条互相矛盾的同类规则 */
export function addability(
  config: SchedulerConfig,
  tpl: RuleTemplate,
): { ok: boolean; reason?: string } {
  if (tpl.multiple) return { ok: true };
  const exists = config.rules.some((r) => r.type === tpl.type);
  if (exists) return { ok: false, reason: '这类规则只需要一条，已存在（可直接改它的参数）' };
  return { ok: true };
}

export function makeRule(config: SchedulerConfig, type: RuleType): RuleDef {
  const tpl = templateOf(type)!;
  return {
    id: nextRuleId(config.rules),
    type,
    name: tpl.defaultName,
    enabled: true,
    locked: false, // 用户自建的实例永远可删可停；locked 只属于系统内建那两条
    params: tpl.defaultParams(config),
  };
}
