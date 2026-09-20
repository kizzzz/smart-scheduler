import type { EmployeeDef, RuleDef, SchedulerConfig, UnavailableSlot } from '../types';
import { shiftHours } from './derive';

/**
 * 示例门店配置（= 考题原始数据的配置化形态）。
 *
 * 它有两个用途：
 * 1. `?mock=1` 下作为 `GET /api/config/default` 的响应体；
 * 2. 真实模式下 `GET /api/config/default` 失败时的本地兜底 —— 配置页是入口页面，
 *    一个接口抖动不该让用户连界面都进不去。所以这份数据必须与后端默认配置**语义等价**，
 *    数值改动要同步 backend/app/data.py。
 *
 * 与 backend/app/data.py 的唯一有意差异是 `name`：考题数据没有姓名字段，
 * 而契约给 EmployeeDef 加了 name，这里补的是示例姓名，只影响展示。
 */

export const SKILL_KEEPER = '店长值守';
export const SKILL_DRINK = '饮品制作';
export const SKILL_CASHIER = '收银';
export const SKILL_STOCK = '库存管理';

export const DEFAULT_SKILL_POOL = [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK];
export const DEFAULT_ROLE_POOL = ['店长', '副店长', '值班主管', '高级店员', '店员', '兼职'];

/** 一周七天的中文标签，天数增删时用它给新的一天起默认名（超过 7 天用「第 N 天」） */
export const WEEK_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export function dayLabelFor(index: number): string {
  return WEEK_LABELS[index] ?? `第 ${index + 1} 天`;
}

/** 一周的 7 个 day id，用来把「可工作日期」写成 unavailable 列表 */
const DAY_IDS = ['d1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7'];

/**
 * (工号, 姓名, 岗位, 技能, 可工作日期下标, 请假日下标, 偏好班次)
 * 可工作日期用下标而不是标签，是因为 unavailable 存的是 day id，
 * 用下标能一眼看出「取反」的范围，避免写错成标签字符串。
 */
const RAW: Array<[string, string, string, string[], number[], number[], string | null]> = [
  ['E01', '陈慧', '店长', [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], [0, 1, 2, 3, 4, 5, 6], [2], 's1'],
  ['E02', '李文博', '副店长', [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], [0, 1, 2, 3, 4, 5, 6], [], 's2'],
  ['E03', '王晓琳', '值班主管', [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], [0, 1, 2, 3, 4], [], null],
  ['E04', '张衡', '值班主管', [SKILL_KEEPER, SKILL_DRINK, SKILL_STOCK], [2, 3, 4, 5, 6], [], 's1'],
  ['E05', '刘倩', '高级店员', [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], [4, 5, 6], [], 's2'],
  ['E06', '赵子墨', '店员', [SKILL_DRINK, SKILL_CASHIER], [0, 1, 2, 3, 4, 5, 6], [1], 's1'],
  ['E07', '孙悦', '店员', [SKILL_DRINK, SKILL_CASHIER], [0, 1, 2, 3, 4, 5, 6], [], 's2'],
  ['E08', '周航', '店员', [SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], [0, 1, 2, 3, 4, 5], [], null],
  ['E09', '吴敏', '店员', [SKILL_DRINK], [0, 2, 4, 5, 6], [], 's1'],
  ['E10', '郑凯', '店员', [SKILL_DRINK, SKILL_CASHIER], [1, 2, 3, 4, 5, 6], [], null],
  ['E11', '冯雪', '店员', [SKILL_CASHIER, SKILL_STOCK], [0, 1, 2, 3, 4, 5, 6], [], 's2'],
  ['E12', '许嘉', '店员', [SKILL_DRINK, SKILL_CASHIER], [0, 1, 2, 3, 4], [], 's1'],
  ['E13', '何欣', '兼职', [SKILL_DRINK], [5, 6], [], 's1'],
  ['E14', '曾亮', '兼职', [SKILL_DRINK, SKILL_CASHIER], [5, 6], [], 's2'],
  ['E15', '罗艺', '兼职', [SKILL_CASHIER], [4, 5, 6], [], null],
  ['E16', '高琪', '兼职', [SKILL_DRINK], [2, 3, 5, 6], [], null],
  ['E17', '林川', '店员', [SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], [0, 1, 2, 3, 4, 5, 6], [0], null],
  ['E18', '徐念', '店员', [SKILL_DRINK, SKILL_CASHIER], [0, 1, 2, 3, 4], [], 's2'],
  ['E19', '马蕊', '兼职', [SKILL_DRINK, SKILL_CASHIER], [5, 6], [], null],
  ['E20', '谢桐', '店员', [SKILL_DRINK, SKILL_STOCK], [1, 2, 3, 4, 5, 6], [3], 's1'],
];

/** 「可工作日期 + 请假」两种表达合并成一份整日不可用清单 —— 契约只保留 unavailable 一个维度 */
function unavailableOf(availableIdx: number[], leaveIdx: number[]): UnavailableSlot[] {
  const blocked = new Set<number>(leaveIdx);
  DAY_IDS.forEach((_, i) => {
    if (!availableIdx.includes(i)) blocked.add(i);
  });
  return [...blocked].sort((a, b) => a - b).map((i) => ({ day: DAY_IDS[i], shift: null }));
}

function defaultEmployees(): EmployeeDef[] {
  return RAW.map(([id, name, role, skills, avail, leave, pref]) => ({
    id,
    name,
    role,
    skills: [...skills],
    unavailable: unavailableOf(avail, leave),
    max_shifts: null,
    preferred_shifts: pref ? [pref] : [],
    active: true,
  }));
}

/**
 * 默认规则：原 R-01～R-09 的模板化映射，编号刻意保留成 R-xx，
 * 这样校验面板里的违规定位与老版本、与后端日志能对上。
 *
 * R-10 是原来隐式的「每人每天最多一个班」：旧 solver 内置了这条假设但从未暴露成规则，
 * 三班制门店可能允许一天两班，所以必须能被看见、能被关掉（契约 4 节末尾）。
 */
function defaultRules(): RuleDef[] {
  return [
    {
      id: 'R-01',
      type: 'require_attribute',
      name: '每班至少 1 名店长值守',
      enabled: true,
      locked: false,
      params: { attr: 'skill', value: SKILL_KEEPER, min: 1 },
    },
    {
      id: 'R-02',
      type: 'require_attribute',
      name: '每班至少 2 名会饮品制作',
      enabled: true,
      locked: false,
      params: { attr: 'skill', value: SKILL_DRINK, min: 2 },
    },
    {
      id: 'R-03',
      type: 'require_attribute',
      name: '每班至少 1 名会收银',
      enabled: true,
      locked: false,
      params: { attr: 'skill', value: SKILL_CASHIER, min: 1 },
    },
    {
      id: 'R-04',
      type: 'min_staff_per_shift',
      name: '每班总人数下限',
      enabled: true,
      locked: false,
      params: { default: 4, peak: 6, overrides: [] },
    },
    {
      id: 'R-05',
      type: 'max_shifts_per_period',
      name: '每人每周最多 5 个班',
      enabled: true,
      locked: false,
      params: { max: 5 },
    },
    {
      id: 'R-06',
      type: 'max_consecutive_days',
      name: '不得连续工作超过 5 天',
      enabled: true,
      locked: false,
      params: { max: 5 },
    },
    {
      id: 'R-07',
      type: 'min_rest_hours',
      name: '相邻班次至少休息 13 小时',
      enabled: true,
      locked: false,
      // 13 而不是 12：契约定为「间隔严格小于 hours 才违规」，而旧 R-07 要拦的
      // 「晚班 21:00 → 次日早班 09:00」恰好是 12h，阈值取 12 会让它变成合规
      params: { hours: 13 },
    },
    {
      id: 'R-08',
      type: 'respect_unavailability',
      name: '不可用时段绝不排班',
      enabled: true,
      locked: true,
      params: {},
    },
    {
      id: 'R-09',
      type: 'skill_source_integrity',
      name: '技能只能来自员工档案',
      enabled: true,
      locked: true,
      params: {},
    },
    {
      id: 'R-10',
      type: 'one_shift_per_day',
      name: '每人每天最多一个班',
      enabled: true,
      locked: false,
      params: {},
    },
  ];
}

/** 每次调用返回全新对象：配置是可编辑状态的初始值，共享引用会让「恢复默认」污染下一次 */
export function defaultConfig(): SchedulerConfig {
  return {
    version: 1,
    scenario: {
      name: '门店周排班',
      days: DAY_IDS.map((id, i) => ({ id, label: WEEK_LABELS[i], peak: i >= 5 })),
      shifts: [
        { id: 's1', name: '早班', start: '09:00', end: '17:00', hours: shiftHours('09:00', '17:00') },
        { id: 's2', name: '晚班', start: '13:00', end: '21:00', hours: shiftHours('13:00', '21:00') },
      ],
    },
    skill_pool: [...DEFAULT_SKILL_POOL],
    role_pool: [...DEFAULT_ROLE_POOL],
    employees: defaultEmployees(),
    rules: defaultRules(),
  };
}
