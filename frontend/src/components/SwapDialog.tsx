import { useMemo, useState } from 'react';
import { Crown, Search, UserMinus } from 'lucide-react';
import type { Meta, Slot } from '../types';
import { candidatesForSlot, weeklyLoad } from '../lib/schedule';
import { cn } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Dialog } from './ui/Dialog';
import { SkillDot } from './EmployeeChip';

export interface SwapTarget {
  slot: Slot;
  employeeId: string | null;
}

export function SwapDialog({
  target,
  meta,
  slots,
  onClose,
  onApply,
}: {
  target: SwapTarget | null;
  meta: Meta;
  slots: Slot[];
  onClose: () => void;
  onApply: (day: string, shift: string, remove: string | null, add: string | null) => void;
}) {
  const [q, setQ] = useState('');

  const slot = target?.slot;
  const load = useMemo(() => weeklyLoad(slots), [slots]);
  const candidates = useMemo(() => {
    if (!slot) return [];
    const ids = candidatesForSlot(meta, slots, slot);
    return ids
      .map((id) => meta.employees.find((e) => e.id === id)!)
      .filter((e) => (q ? `${e.id}${e.role}${e.skills.join('')}`.includes(q) : true))
      .sort((a, b) => {
        const pref = Number(b.preference === slot.shift) - Number(a.preference === slot.shift);
        if (pref) return pref;
        return (load.get(a.id) ?? 0) - (load.get(b.id) ?? 0);
      });
  }, [slot, meta, slots, q, load]);

  if (!target || !slot) return null;
  const replacing = target.employeeId;

  return (
    <Dialog
      open
      onClose={onClose}
      width="max-w-lg"
      title={
        <span className="flex items-center gap-2">
          {replacing ? `更换 ${replacing}` : '补充人员'}
          <Badge tone="teal">
            {slot.day_label} {slot.shift} · {slot.shift_time}
          </Badge>
        </span>
      }
      subtitle={`候选人已按「当日可工作 / 未请假 / 未在当天另一班次」过滤，并优先展示班次偏好匹配、本周工时较少的员工。当前实排 ${slot.employees.length} 人，最低要求 ${slot.min_required} 人。`}
      footer={
        <>
          {replacing ? (
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
            取消
          </Button>
        </>
      }
    >
      <div className="mb-2.5 flex items-center gap-2 rounded-lg border border-line bg-soft px-2.5 py-1.5">
        <Search size={13} className="flex-none text-mut-2" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索工号 / 岗位 / 技能"
          className="w-full bg-transparent text-[12px] text-ink outline-none placeholder:text-mut-2"
        />
        <span className="flex-none text-[11px] text-mut-2">{candidates.length} 人可选</span>
      </div>

      {candidates.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-[12px] text-mut">
          该班次没有符合条件的候选人。可考虑放宽约束，或直接移出当前员工后由 AI 重排。
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-1.5">
          {candidates.map((e) => {
            const isManager = e.skills.includes('店长值守');
            const shifts = load.get(e.id) ?? 0;
            const prefMatch = e.preference === slot.shift;
            return (
              <li key={e.id}>
                <button
                  type="button"
                  onClick={() => {
                    onApply(slot.day, slot.shift, replacing, e.id);
                    onClose();
                  }}
                  className={cn(
                    'w-full rounded-lg border bg-white px-2.5 py-2 text-left transition-colors duration-150 hover:border-teal-400 hover:bg-teal-50 focus-ring',
                    isManager ? 'border-teal-200' : 'border-line',
                  )}
                >
                  <span className="flex items-center gap-1.5">
                    {isManager ? <Crown size={11} className="flex-none text-teal-700" /> : null}
                    <b className="text-[12.5px] font-semibold text-ink">{e.id}</b>
                    <span className="truncate text-[11px] text-mut">{e.role}</span>
                    <span
                      className={cn(
                        'ml-auto flex-none rounded px-1 text-[10px] font-semibold leading-4 tabular-nums',
                        shifts >= 5 ? 'bg-fail-bg text-fail' : 'bg-[#F1F5F4] text-mut',
                      )}
                      title="本周已排班次数（R-05 上限 5）"
                    >
                      {shifts}/5 班
                    </span>
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px] text-mut">
                    {e.skills.includes('饮品制作') ? <SkillDot kind="drink" /> : null}
                    {e.skills.includes('收银') ? <SkillDot kind="cashier" /> : null}
                    <span className="truncate">{e.skills.join(' / ')}</span>
                    {prefMatch ? <Badge tone="pass">偏好匹配</Badge> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-2.5 text-[11px] leading-relaxed text-mut-2">
        选定后会立即调用 <code className="font-mono">POST /api/validate</code> 重新校验 9 条硬规则；
        校验器与求解器解耦，AI 生成与手工微调走同一套校验。
      </p>
    </Dialog>
  );
}
