import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Crown, RefreshCw, Search, SlidersHorizontal, UserMinus } from 'lucide-react';
import type { ApiFailure, CandidateEmployee, Meta, SchedulerConfig, Slot } from '../types';
import { fetchCandidates } from '../api';
import { formatScope, weeklyLoad } from '../lib/schedule';
import { globalMaxShifts, hasRule } from '../config/derive';
import { toFailure } from '../lib/failure';
import { cn } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Dialog } from './ui/Dialog';
import { Skeleton } from './ui/Skeleton';
import { SkillDot, SKILL_CASHIER, SKILL_DRINK, SKILL_MANAGER } from './EmployeeChip';

export interface SwapTarget {
  slot: Slot;
  employeeId: string | null;
}

/**
 * 换人面板。
 *
 * 候选名单一律来自 `POST /api/candidates`（契约 5.5），不再在前端自己过滤：
 * 过滤条件（不可用、上限、连班、休息间隔、时间重叠）与求解器共用一套判定，
 * 前端复刻一份必然会和后端漂移，而这里恰恰是店长做决策的地方。
 *
 * 界面只补两件后端**不该**知道的事：
 * - 「这一格来自旧配置」——后端给 400，前端把它翻译成人话并给出重新生成的出口。
 * - 「当天已排别的班 / 已达上限」——后端按空排班状态算候选（契约明确不掺入某次求解的
 *   中间态），所以这些与当前这张表有关的冲突由前端标注；标注而不隐藏，例外由店长定。
 */
export function SwapDialog({
  target,
  meta,
  config,
  slots,
  onClose,
  onApply,
  onRegenerate,
  onGoConfig,
}: {
  target: SwapTarget | null;
  /** 看板当时的维度（可能来自旧配置），用于把 d7/s2 翻译成「周日·晚班」 */
  meta: Meta;
  /** 随请求带给后端的当前配置；null 时后端按默认配置作答 */
  config: SchedulerConfig | null;
  slots: Slot[];
  onClose: () => void;
  onApply: (day: string, shift: string, remove: string | null, add: string | null) => void;
  /** 旧配置的格子取不到候选时的出口：按当前配置重排一版 */
  onRegenerate?: () => void;
  onGoConfig?: () => void;
}) {
  const [q, setQ] = useState('');
  const [data, setData] = useState<CandidateEmployee[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  const slot = target?.slot ?? null;
  const replacing = target?.employeeId ?? null;
  /** 已排在这一格的人 = 契约里的 taken，由后端剔除（含正在被替换的那个人） */
  const takenKey = slot ? slot.employees.join(',') : '';

  /**
   * 这一格还在当前配置里吗。
   *
   * 本地就能判定，所以不等 400 回来再说话——弹窗打开的第一帧就给结论。
   * 但请求照发：后端的 400 会带上「有效取值」，排查时比前端的推断更可靠。
   */
  const missing = useMemo(() => {
    if (!slot || !config) return null;
    if (!config.scenario.days.some((d) => d.id === slot.day)) return 'day' as const;
    if (!config.scenario.shifts.some((s) => s.id === slot.shift)) return 'shift' as const;
    return null;
  }, [config, slot]);

  const load = useCallback(async () => {
    if (!slot) return;
    setLoading(true);
    setFailure(null);
    try {
      const res = await fetchCandidates(slot.day, slot.shift, slot.employees, config);
      setData(res.candidates);
    } catch (e) {
      setData(null);
      setFailure(toFailure(e));
    } finally {
      setLoading(false);
    }
  }, [config, slot]);

  useEffect(() => {
    if (!slot) {
      setData(null);
      setFailure(null);
      return;
    }
    setQ('');
    void load();
    // takenKey 进依赖：移出一个人之后再打开，候选里应该立刻能看到他
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slot?.day, slot?.shift, takenKey]);

  const fallbackCap = config ? globalMaxShifts(config) : null;
  const oneShiftPerDay = config ? hasRule(config, 'one_shift_per_day') : true;
  const usage = useMemo(() => weeklyLoad(slots), [slots]);

  /** 同一天其他班次已排的人：员工 id → 那个班次的名字 */
  const sameDayShift = useMemo(() => {
    const m = new Map<string, string>();
    if (!slot) return m;
    for (const s of slots) {
      if (s.day !== slot.day || s.shift === slot.shift) continue;
      const label = meta.shifts.find((x) => x.key === s.shift)?.label ?? s.shift;
      for (const id of s.employees) m.set(id, label);
    }
    return m;
  }, [meta.shifts, slot, slots]);

  const rows = useMemo(() => {
    if (!data || !slot) return [];
    const key = q.trim().toLowerCase();
    return data
      .filter((e) =>
        key ? `${e.id}${e.name}${e.role}${e.skills.join('')}`.toLowerCase().includes(key) : true,
      )
      .map((e) => {
        const used = usage.get(e.id) ?? 0;
        // 个人上限优先，没有就看全局规则；两者都没有就是「不限」，不能编一个 5 出来
        const cap = e.max_shifts ?? fallbackCap ?? null;
        return {
          e,
          used,
          cap,
          full: cap !== null && used >= cap,
          prefMatch: e.preferred_shifts.includes(slot.shift),
          conflict: oneShiftPerDay ? (sameDayShift.get(e.id) ?? null) : null,
        };
      })
      .sort((a, b) => {
        if (Boolean(a.conflict) !== Boolean(b.conflict)) return a.conflict ? 1 : -1;
        if (a.full !== b.full) return a.full ? 1 : -1;
        if (a.prefMatch !== b.prefMatch) return a.prefMatch ? -1 : 1;
        if (a.used !== b.used) return a.used - b.used;
        return a.e.id < b.e.id ? -1 : 1;
      });
  }, [data, fallbackCap, oneShiftPerDay, q, sameDayShift, slot, usage]);

  if (!target || !slot) return null;

  const scopeLabel = formatScope(meta, slot.day, slot.shift).replace(' ', '·');
  /** 400 一律按「格子对不上当前配置」解释：这个接口不跑容量自检，不会因为排不满而 400 */
  const staleView = missing !== null || failure?.status === 400;
  const dims = config
    ? `${config.scenario.days.length} 天 × ${config.scenario.shifts.length} 班`
    : null;

  return (
    <Dialog
      open
      onClose={onClose}
      width="max-w-lg"
      title={
        <span className="flex items-center gap-2">
          {replacing ? `更换 ${replacing}` : '补充人员'}
          <Badge tone={staleView ? 'pend' : 'teal'}>
            {scopeLabel} · {slot.shift_time}
          </Badge>
        </span>
      }
      subtitle={
        staleView
          ? '这一格不在当前配置里，暂时取不到候选名单。'
          : `候选名单来自 POST /api/candidates，按当前配置筛出「在职 + 这一格可排班」的人，已排除本格已有的 ${slot.employees.length} 人。这一格下限 ${slot.min_required} 人。`
      }
      footer={
        <>
          {staleView ? (
            onRegenerate ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  onClose();
                  onRegenerate();
                }}
              >
                <RefreshCw size={12} />
                按当前配置重新生成
              </Button>
            ) : null
          ) : replacing ? (
            <Button
              variant="danger"
              size="sm"
              onClick={() => {
                onApply(slot.day, slot.shift, replacing, null);
                onClose();
              }}
            >
              <UserMinus size={12} />
              仅移出 {replacing}（不补人）
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={onClose}>
            {staleView ? '先不改' : '取消'}
          </Button>
        </>
      }
    >
      {staleView ? (
        <div className="space-y-2.5">
          <div className="flex gap-2 rounded-lg border border-pend-border bg-pend-bg px-3 py-2.5">
            <AlertTriangle size={14} className="mt-[1px] flex-none text-pend" />
            <div className="min-w-0 space-y-1">
              <p className="text-[12.5px] font-semibold leading-relaxed text-ink">
                这一格属于旧配置（{scopeLabel}），当前配置里已经没有它了。请重新生成排班后再调整。
              </p>
              <p className="text-[11.5px] leading-relaxed text-mut">
                看板上这张表是改配置之前排出来的，所以它还留着{scopeLabel}这一格
                {dims ? `，而当前配置是 ${dims}` : ''}。 换人得先知道「谁能上这一格」，
                这一格已经不在排班周期里，后端因此不给名单，而不是返回一份空名单让你误以为「没人能上」。
              </p>
            </div>
          </div>
          {failure?.message ? (
            <p className="rounded-lg border border-line bg-soft px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-mut-2">
              POST /api/candidates → 400：{failure.message}
            </p>
          ) : null}
          {onGoConfig ? (
            <button
              type="button"
              onClick={() => {
                onClose();
                onGoConfig();
              }}
              className="flex items-center gap-1.5 text-[11.5px] font-semibold text-teal-800 underline decoration-teal-300 underline-offset-2 hover:text-teal-900 focus-ring"
            >
              <SlidersHorizontal size={12} />
              先去配置页确认天数与班次
            </button>
          ) : null}
        </div>
      ) : (
        <>
          <div className="mb-2.5 flex items-center gap-2 rounded-lg border border-line bg-soft px-2.5 py-1.5">
            <Search size={13} className="flex-none text-mut-2" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索工号 / 姓名 / 岗位 / 技能"
              className="w-full bg-transparent text-[12px] text-ink outline-none placeholder:text-mut-2"
              disabled={loading}
            />
            <span className="flex-none text-[11px] text-mut-2">
              {loading ? '正在取候选…' : `${rows.length} 人可选`}
            </span>
          </div>

          {loading ? (
            <ul className="grid grid-cols-2 gap-1.5" aria-label="候选人加载中">
              {Array.from({ length: 6 }).map((_, i) => (
                <li key={i}>
                  <Skeleton className="h-[52px]" />
                </li>
              ))}
            </ul>
          ) : failure ? (
            <div className="space-y-2 rounded-lg border border-fail-border bg-fail-bg px-3 py-2.5">
              <p className="text-[12.5px] font-semibold text-ink">取候选名单失败</p>
              <p className="text-[11.5px] leading-relaxed text-mut">{failure.message}</p>
              <Button variant="secondary" size="sm" onClick={() => void load()}>
                <RefreshCw size={12} />
                重试
              </Button>
            </div>
          ) : rows.length === 0 ? (
            <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-[12px] leading-relaxed text-mut">
              {q.trim()
                ? '没有匹配这个关键词的候选人。'
                : '这一格没有可排班的候选人：在职员工要么被标记为当格不可排班，要么已经排在这一格里。'}
              <br />
              可以回到配置页放宽不可排班时段，也可以先移出当前员工再让 AI 重排。
            </p>
          ) : (
            <ul className="grid grid-cols-2 gap-1.5">
              {rows.map(({ e, used, cap, full, prefMatch, conflict }) => {
                const isManager = e.skills.includes(SKILL_MANAGER);
                const blocked = e.unavailable.length;
                return (
                  <li key={e.id}>
                    <button
                      type="button"
                      onClick={() => {
                        onApply(slot.day, slot.shift, replacing, e.id);
                        onClose();
                      }}
                      title={[
                        `${e.id}${e.name && e.name !== e.id ? ` ${e.name}` : ''} · ${e.role}`,
                        cap === null ? '班次上限：不限' : `班次上限：${cap} 班`,
                        blocked ? `标记了 ${blocked} 处不可排班` : '无不可排班标记',
                        conflict ? `当天已排${conflict}` : null,
                      ]
                        .filter(Boolean)
                        .join('\n')}
                      className={cn(
                        'w-full rounded-lg border bg-white px-2.5 py-2 text-left transition-colors duration-150 hover:border-teal-400 hover:bg-teal-50 focus-ring',
                        conflict
                          ? 'border-pend-border'
                          : isManager
                            ? 'border-teal-200'
                            : 'border-line',
                      )}
                    >
                      <span className="flex items-center gap-1.5">
                        {isManager ? <Crown size={11} className="flex-none text-teal-700" /> : null}
                        <b className="flex-none text-[12.5px] font-semibold text-ink">{e.id}</b>
                        <span className="truncate text-[11px] text-mut">
                          {e.name && e.name !== e.id ? `${e.name} · ${e.role}` : e.role}
                        </span>
                        <span
                          className={cn(
                            'ml-auto flex-none rounded px-1 text-[10px] font-semibold leading-4 tabular-nums',
                            full ? 'bg-fail-bg text-fail' : 'bg-[#F1F5F4] text-mut',
                          )}
                          title={
                            cap === null
                              ? '本周期已排班次数（未设上限）'
                              : `本周期已排班次数 / 上限 ${cap}`
                          }
                        >
                          {cap === null ? `${used} 班` : `${used}/${cap} 班`}
                        </span>
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px] text-mut">
                        {e.skills.includes(SKILL_DRINK) ? <SkillDot kind="drink" /> : null}
                        {e.skills.includes(SKILL_CASHIER) ? <SkillDot kind="cashier" /> : null}
                        <span className="truncate">{e.skills.join(' / ') || '无技能标签'}</span>
                        {prefMatch ? <Badge tone="pass">偏好匹配</Badge> : null}
                        {full ? <Badge tone="pend">已达上限</Badge> : null}
                        {conflict ? <Badge tone="pend">当天已排{conflict}</Badge> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <p className="mt-2.5 text-[11px] leading-relaxed text-mut-2">
            后端只按配置给候选（不看当前这张表），所以「当天已排别的班」「已达上限」由本页标注出来
            供你判断。选定后立即调用 <code className="font-mono">POST /api/validate</code> 按{' '}
            {meta.rules.length} 条硬规则重新校验。
          </p>
        </>
      )}
    </Dialog>
  );
}
