import { CalendarRange, CircleAlert, FileUp, Loader2, Plus, RotateCcw, Sparkles } from 'lucide-react';
import type { Meta, Slot } from '../types';
import { cn, slotKey } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Card, CardBody, CardHeader } from './ui/Card';
import { Button } from './ui/Button';
import { EmployeeChip, SkillDot } from './EmployeeChip';

export interface SlotIssue {
  ruleIds: string[];
  employees: Set<string>;
  messages: string[];
}

export function ScheduleBoard({
  meta,
  slots,
  issues,
  flashKeys,
  dirty,
  validating,
  origin = 'generated',
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

  return (
    <Card className="overflow-hidden">
      <CardHeader
        icon={<CalendarRange size={15} />}
        title="一周排班看板"
        subtitle={
          imported
            ? '当前基线来自导入的排班表，可继续用自然语言微调或点击换人'
            : 'AI 出 0→80 的草案，店长做 80→100 的微调'
        }
        right={
          <>
            <Badge tone="neutral">共 {headcount} 人次</Badge>
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
          <div
            className="grid min-w-[880px] gap-1"
            style={{ gridTemplateColumns: '54px repeat(7, minmax(0, 1fr))' }}
          >
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
                <span className="ml-1 font-normal text-mut-2">≥{d.min_required}</span>
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
          <span className="inline-flex items-center gap-1.5">
            <EmployeeChip id="E01" employee={empMap.get('E01')} interactive={false} />
            店长值守资格
          </span>
          <span className="inline-flex items-center gap-1.5">
            <SkillDot kind="drink" />
            饮品制作
          </span>
          <span className="inline-flex items-center gap-1.5">
            <SkillDot kind="cashier" />
            收银
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="rounded bg-[#F1F5F4] px-1 text-[9.5px] font-semibold leading-[15px] text-mut">
              5/4
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
        <b className="text-[11px] font-semibold text-ink-2">{shift.label}</b>
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
            <div key={key} className="rounded-lg border border-dashed border-line bg-soft p-2 text-center text-[10px] text-mut-2">
              缺失
            </div>
          );
        }
        const short = slot.employees.length < slot.min_required;
        return (
          <div
            key={nonce ? `${key}-${nonce}` : key}
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
