import { Eraser } from 'lucide-react';
import type { DayDef, EmployeeDef, ShiftDef } from '../../types';
import { unavailableCells } from '../../config/edit';
import { cn, slotKey } from '../../lib/utils';
import { Button } from '../ui/Button';

/**
 * 不可排班时段网格。
 *
 * 为什么用网格而不是「请假日期」多选：契约把请假与不可用合并成了一个维度，且精确到班次
 * （三班制门店「只能上早班」是常态）。日期多选表达不了「周三只是不能上晚班」，
 * 而网格点一下就说清了，同时也天然是「这个人到底什么时候能上」的可视化。
 *
 * 交互约定：点表头 = 整天取反（最高频的操作是「某天全天请假」），点格子 = 单班取反。
 */
export function UnavailableGrid({
  employee,
  days,
  shifts,
  onToggleCell,
  onToggleDay,
  onClear,
}: {
  employee: EmployeeDef;
  days: DayDef[];
  shifts: ShiftDef[];
  onToggleCell: (day: string, shift: string) => void;
  onToggleDay: (day: string) => void;
  onClear: () => void;
}) {
  const cells = unavailableCells(
    employee,
    days.map((d) => d.id),
    shifts.map((s) => s.id),
  );
  const blockedCount = cells.size;
  const total = days.length * shifts.length;

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <p className="text-[11px] font-semibold text-mut">不可排班时段</p>
        <span className="text-[10.5px] text-mut-2">
          点格子切换单个班次，点日期表头切换一整天 · 已标记 {blockedCount}/{total} 格
        </span>
        {blockedCount > 0 ? (
          <Button size="sm" variant="ghost" onClick={onClear} className="ml-auto h-6 px-2">
            <Eraser size={11} />
            全部清空
          </Button>
        ) : null}
      </div>

      <div className="-mx-0.5 overflow-x-auto px-0.5 pb-1 scrollbar-thin">
        <div
          className="grid gap-[3px]"
          style={{ gridTemplateColumns: `52px repeat(${days.length}, minmax(44px, 1fr))` }}
        >
          <div />
          {days.map((d) => {
            const whole = shifts.every((s) => cells.has(slotKey(d.id, s.id)));
            return (
              <button
                key={d.id}
                type="button"
                onClick={() => onToggleDay(d.id)}
                title={whole ? `恢复${d.label}全天可排` : `把${d.label}整天标为不可排`}
                className={cn(
                  'truncate rounded-[5px] border px-1 py-[2px] text-[10px] font-semibold transition-colors duration-150 focus-ring',
                  whole
                    ? 'border-fail-border bg-fail-bg text-fail'
                    : d.peak
                      ? 'border-teal-200 bg-teal-50 text-teal-800'
                      : 'border-line bg-white text-mut',
                )}
              >
                {d.label}
              </button>
            );
          })}

          {shifts.map((s) => (
            <div key={s.id} className="contents">
              <div
                className="flex items-center justify-center truncate rounded-[5px] border border-line-2 bg-soft px-1 text-[10px] font-semibold text-ink-2"
                title={`${s.name} ${s.start}–${s.end}`}
              >
                {s.name}
              </div>
              {days.map((d) => {
                const key = slotKey(d.id, s.id);
                const off = cells.has(key);
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => onToggleCell(d.id, s.id)}
                    aria-pressed={off}
                    title={`${d.label} ${s.name}：${off ? '不可排班（点击恢复）' : '可排班（点击标为不可排）'}`}
                    className={cn(
                      'h-[22px] rounded-[5px] border text-[9.5px] font-semibold transition-colors duration-150 focus-ring',
                      off
                        ? 'border-fail-border bg-fail-bg text-fail'
                        : 'border-line bg-white text-mut-2 hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700',
                    )}
                  >
                    {off ? '不可排' : '可排'}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
