import type { CandidateEmployee, CandidatesResponse, EmployeeDef, SchedulerConfig } from '../types';
import { defaultConfig } from '../config/defaults';
import { isUnavailable } from '../config/derive';

/**
 * mock 模式下的 `POST /api/candidates`（契约 5.5）。
 *
 * 两处刻意与后端逐字对齐，否则 mock 演示与真实环境会给出两种体验：
 * 1. **员工池取自 config**。这正是契约要新增 POST 的原因——GET 带不了配置，
 *    自定义门店里会返回默认 20 人档案（幽灵员工）。
 * 2. **day / shift 不在配置里 → 400**，而不是静默返回空名单。前端点开的格子
 *    可能是上一份配置留下的，空名单会被读成「没人能上」，真相是这一格已经不存在了。
 *
 * 与后端一致的另一个细节：候选只受**配置**约束，不掺入当前这张表的中间态
 * （后端用的是空 State）。所以「当天已排别的班」「已达上限」这类与具体排班有关的
 * 提示由界面本地标注，而不是在这里先偷偷过滤掉。
 */

/** 用 status + body 表达，而不是抛异常：mock 层不引用 api.ts 的 ApiError，避免循环依赖 */
export interface MockCandidatesResult {
  status: number;
  body: CandidatesResponse | { ok: false; detail: string; message: string };
}

function reject(message: string): MockCandidatesResult {
  // 后端同时给 detail 与 message（契约两处措辞不一致），mock 照抄
  return { status: 400, body: { ok: false, detail: message, message } };
}

/**
 * EmployeeDef → 契约里的候选形状：原字段 + 三个兼容字段。
 *
 * `leave_days` 只收有具名原因的不可用（请假/培训），无原因的整日不可用属于
 * 「本来就不上班」（兼职只做周末），后端也是这么分的。
 */
function toCandidate(config: SchedulerConfig, e: EmployeeDef): CandidateEmployee {
  const { days, shifts } = config.scenario;
  return {
    ...e,
    unavailable: e.unavailable.map((u) => ({ ...u })),
    skills: [...e.skills],
    preferred_shifts: [...e.preferred_shifts],
    available_days: days.filter((d) => shifts.some((s) => !isUnavailable(e, d.id, s.id))).map((d) => d.id),
    leave_days: e.unavailable.filter((u) => Boolean(u.reason)).map((u) => u.day),
    preference: e.preferred_shifts[0] ?? null,
  };
}

export function mockCandidates(
  day: string,
  shift: string,
  taken: string[],
  config?: SchedulerConfig | null,
): MockCandidatesResult {
  const cfg = config ?? defaultConfig();
  const { days, shifts } = cfg.scenario;

  if (!days.some((d) => d.id === day)) {
    return reject(`day='${day}' 不在当前配置里，有效取值：${days.map((d) => d.id).join('、')}`);
  }
  if (!shifts.some((s) => s.id === shift)) {
    return reject(`shift='${shift}' 不在当前配置里，有效取值：${shifts.map((s) => s.id).join('、')}`);
  }

  const excluded = new Set(taken);
  const pool = cfg.employees
    .filter((e) => e.active)
    .filter((e) => !excluded.has(e.id))
    .filter((e) => !isUnavailable(e, day, shift));

  return { status: 200, body: { day, shift, candidates: pool.map((e) => toCandidate(cfg, e)) } };
}
