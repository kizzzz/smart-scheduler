import {
  CalendarRange,
  CircleAlert,
  Crown,
  FileUp,
  History,
  Loader2,
  Plus,
  RotateCcw,
  Sparkles,
} from 'lucide-react';
import type { Meta, Slot } from '../types';
import { cn, slotKey } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Card, CardBody, CardHeader } from './ui/Card';
import { Button } from './ui/Button';
import { EmployeeChip, SKILL_CASHIER, SKILL_DRINK, SKILL_MANAGER, SkillDot } from './EmployeeChip';

export interface SlotIssue {
  ruleIds: string[];
  employees: Set<string>;
  messages: string[];
}

/** 单列的期望宽度：容得下 4 个 chip 换行两排，再窄就会开始挤成一列 */
const COL_MIN_PX = 112;
const LABEL_COL_PX = 54;

export function ScheduleBoard({
  meta,
  slots,
  issues,
  flashKeys,
  dirty,
  validating,
  origin = 'generated',
  stale = false,
  scenarioName = null,
  onPickChip,
  onAddEmployee,
  onReoptimize,
  onReset,
  canReoptimize,
}: {
  meta: Meta;
  slots: Slot[];
  issues: Map<string, SlotIssue>;
  flashKeys: Map<string, number>;
  dirty: boolean;
  validating: boolean;
  /** 表的来源：导入的基线不是 AI 生成的，状态徽标与副标题都不该说「已生成」 */
  origin?: 'generated' | 'imported';
  /** 配置已改、这张表还是旧配置的产物。只标记，不清空（契约 2.2） */
  stale?: boolean;
  scenarioName?: string | null;
  onPickChip: (slot: Slot, employeeId: string) => void;
  onAddEmployee: (slot: Slot) => void;
  onReoptimize: () => void;
  onReset: () => void;
  canReoptimize: boolean;
}) {
  const empMap = new Map(meta.employees.map((e) => [e.id, e]));
  const days = meta.days;
  const shifts = meta.shifts;
  const bySlot = new Map(slots.map((s) => [slotKey(s.day, s.shift), s]));
  const headcount = slots.reduce((n, s) => n + s.employees.length, 0);
  const imported = origin === 'imported';

  /**
   * 列数由维度决定，不再写死 7。14 天 × 4 班时靠横向滚动解决：
   * 把列压到 60px 以下看板就没法读了，滚动比「全塞进屏幕」更诚实。
   */
  const gridTemplate = `${LABEL_COL_PX}px repeat(${Math.max(days.length, 1)}, minmax(0, 1fr))`;
  const minWidth = LABEL_COL_PX + Math.max(days.length, 1) * COL_MIN_PX;

  // 图例只展示这份档案里真实存在的技能，否则配置换了行业还在讲「饮品制作」
  const hasManager = meta.employees.some((e) => e.skills.includes(SKILL_MANAGER));
  const hasDrink = meta.employees.some((e) => e.skills.includes(SKILL_DRINK));
  const hasCashier = meta.employees.some((e) => e.skills.includes(SKILL_CASHIER));

  return (
    <Card className="overflow-hidden">
      <CardHeader
        icon={<CalendarRange size={15} />}
        title={`排班看板 · ${days.length} 天 × ${shifts.length} 班`}
        subtitle={
          imported
            ? '当前基线来自导入的排班表，可继续用自然语言微调或点击换人'
            : `${scenarioName ? `${scenarioName} · ` : ''}AI 出 0→80 的草案，店长做 80→100 的微调`
        }
        right={
          <>
            <Badge tone="neutral">共 {headcount} 人次</Badge>
            {stale ? (
              <Badge tone="pend" icon={<History size={10} />}>
                基于旧配置
              </Badge>
            ) : null}
            {validating ? (
              <Badge tone="teal" icon={<Loader2 size={10} className="animate-spin" />}>
                实时校验中
              </Badge>
            ) : dirty ? (
              <Badge tone="pend">微调态 · 已本地修改</Badge>
            ) : imported ? (
              <Badge tone="teal" icon={<FileUp size={10} />}>
                导入基线
              </Badge>
            ) : (
              <Badge tone="pass">已生成</Badge>
            )}
          </>
        }
      />
      <CardBody className="pb-3.5">
        <div className="-mx-1 overflow-x-auto px-1 pb-1 scrollbar-thin">
          <div className="grid gap-1" style={{ gridTemplateColumns: gridTemplate, minWidth }}>
            <div />
            {days.map((d) => (
              <div
                key={d.key}
                className={cn(
                  'rounded-md pb-1 pt-0.5 text-center text-[11px] font-semibold',
                  d.is_weekend ? 'bg-teal-50/70 text-teal-800' : 'text-mut',
                )}
              >
                {d.label}
                <span className="ml-1 font-normal text-mut-2">{demandLabel(slots, d.key)}</span>
              </div>
            ))}

            {shifts.map((sh) => (
              <BoardRow
                key={sh.key}
                shift={sh}
                days={days}
                bySlot={bySlot}
                issues={issues}
                flashKeys={flashKeys}
                empMap={empMap}
                onPickChip={onPickChip}
                onAddEmployee={onAddEmployee}
              />
            ))}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line-2 pt-2.5 text-[11px] text-mut">
          {hasManager ? (
            <span className="inline-flex items-center gap-1.5">
              <Crown size={11} className="text-teal-700" />
              {SKILL_MANAGER}资格
            </span>
          ) : null}
          {hasDrink ? (
            <span className="inline-flex items-center gap-1.5">
              <SkillDot kind="drink" />
              {SKILL_DRINK}
            </span>
          ) : null}
          {hasCashier ? (
            <span className="inline-flex items-center gap-1.5">
              <SkillDot kind="cashier" />
              {SKILL_CASHIER}
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1.5">
            <span className="rounded bg-[#F1F5F4] px-1 text-[9.5px] font-semibold leading-[15px] text-mut">
              3/2
            </span>
            实排 / 最低要求
          </span>
          <span className="inline-flex items-center gap-1.5 text-mut-2">
            <CircleAlert size={11} className="text-fail" />
            红框 = 命中违规
          </span>
          <span className="ml-auto flex items-center gap-2">
            <span className="text-mut-2">点击员工可换人，调整后实时校验</span>
            {dirty ? (
              <Button size="sm" variant="ghost" onClick={onReset}>
                <RotateCcw size={12} />
                {imported ? '还原导入基线' : '还原本次生成'}
              </Button>
            ) : null}
            <Button size="sm" variant="outline" onClick={onReoptimize} disabled={!canReoptimize}>
              <Sparkles size={12} />
              以当前表为基础最小扰动重排
            </Button>
          </span>
        </div>
      </CardBody>
    </Card>
  );
}

/**
 * 当天的人数下限。
 *
 * 配置化之后同一天的不同班次可以有不同下限（早班 4 人、晚班 2 人），
 * 只显示一个数字会骗人，所以不一致时显示区间。数值取自 slot.min_required——
 * 那是后端本次求解真正用的值，而不是前端再算一遍。
 */
function demandLabel(slots: Slot[], dayKey: string): string {
  const mins = slots.filter((s) => s.day === dayKey).map((s) => s.min_required);
  if (mins.length === 0) return '';
  const lo = Math.min(...mins);
  const hi = Math.max(...mins);
  return lo === hi ? `≥${lo}` : `≥${lo}–${hi}`;
}

function BoardRow({
  shift,
  days,
  bySlot,
  issues,
  flashKeys,
  empMap,
  onPickChip,
  onAddEmployee,
}: {
  shift: Meta['shifts'][number];
  days: Meta['days'];
  bySlot: Map<string, Slot>;
  issues: Map<string, SlotIssue>;
  flashKeys: Map<string, number>;
  empMap: Map<string, Meta['employees'][number]>;
  onPickChip: (slot: Slot, employeeId: string) => void;
  onAddEmployee: (slot: Slot) => void;
}) {
  return (
    <>
      <div className="flex flex-col items-center justify-center gap-0.5 rounded-lg border border-line-2 bg-soft px-1 py-1.5">
        <b className="text-[11px] font-semibold leading-tight text-ink-2">{shift.label}</b>
        {shift.time.split('–').map((t) => (
          <i key={t} className="text-[8.5px] not-italic leading-[1.25] text-mut-2">
            {t}
          </i>
        ))}
      </div>
      {days.map((d) => {
        const key = slotKey(d.key, shift.key);
        const slot = bySlot.get(key);
        const issue = issues.get(key);
        const nonce = flashKeys.get(key);
        if (!slot) {
          return (
            <div
              key={key}
              className="rounded-lg border border-dashed border-line bg-soft p-2 text-center text-[10px] text-mut-2"
            >
              缺失
            </div>
          );
        }
        const short = slot.employees.length < slot.min_required;
        return (
          <div
            key={nonce ? `${key}-${nonce}` : key}
            // 稳定的格子标识：联调脚本 / 截图脚本要能定位到具体某一格（尤其是旧配置留下的格子）
            data-slot={key}
            className={cn(
              'group rounded-lg border p-1 transition-colors duration-150',
              nonce ? 'animate-flash' : '',
              issue
                ? 'border-fail-border bg-fail-bg/60'
                : d.is_weekend
                  ? 'border-teal-200 bg-[#FCFEFE]'
                  : 'border-line bg-white',
            )}
          >
            <div className="mb-1 flex items-center justify-between gap-1">
              {issue ? (
                <span className="truncate rounded bg-white px-1 text-[9px] font-semibold leading-[14px] text-fail">
                  {issue.ruleIds.join(' ')}
                </span>
              ) : (
                <span />
              )}
              <span
                className={cn(
                  'flex-none rounded px-1 text-[9.5px] font-semibold leading-[15px] tabular-nums',
                  short ? 'bg-fail-bg text-fail' : 'bg-pass-bg text-pass',
                )}
              >
                {slot.employees.length}/{slot.min_required}
              </span>
            </div>
            <div className="flex flex-wrap gap-1">
              {slot.employees.map((id) => (
                <EmployeeChip
                  key={id}
                  id={id}
                  employee={empMap.get(id)}
                  violating={issue?.employees.has(id)}
                  onClick={() => onPickChip(slot, id)}
                />
              ))}
              <button
                type="button"
                onClick={() => onAddEmployee(slot)}
                title="补一个人"
                aria-label={`在${d.label}${shift.label}补一个人`}
                className="inline-flex h-[17px] w-[19px] items-center justify-center rounded-[5px] border border-dashed border-[#D5DEDD] text-[#B3C1C0] transition-colors duration-150 hover:border-teal-400 hover:bg-teal-50 hover:text-teal-700 focus-ring"
              >
                <Plus size={10} />
              </button>
            </div>
          </div>
        );
      })}
    </>
  );
}
