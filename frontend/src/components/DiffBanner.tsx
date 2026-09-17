import { useState } from 'react';
import { ChevronDown, GitCompareArrows, MinusCircle, PlusCircle } from 'lucide-react';
import type { Diff } from '../types';
import { Badge } from './ui/Badge';
import { cn } from '../lib/utils';

const DAY_LABEL: Record<string, string> = {
  一: '周一',
  二: '周二',
  三: '周三',
  四: '周四',
  五: '周五',
  六: '周六',
  日: '周日',
};

export function DiffBanner({ diff }: { diff: Diff }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="rounded-card border border-teal-200 bg-teal-50/70 px-3.5 py-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 text-left focus-ring"
      >
        <GitCompareArrows size={14} className="flex-none text-teal-700" />
        <span className="text-[12.5px] font-semibold text-ink">
          最小扰动重排完成 · 本次仅调整 {diff.changed_count} 个班次
        </span>
        {diff.new_violations > 0 ? (
          <Badge tone="fail">新增 {diff.new_violations} 条违规</Badge>
        ) : (
          <Badge tone="pass">未引入新违规</Badge>
        )}
        <ChevronDown
          size={14}
          className={cn('ml-auto flex-none text-mut transition-transform duration-150', open && 'rotate-180')}
        />
      </button>
      {open ? (
        <ul className="mt-2 space-y-1.5 border-t border-teal-200/70 pt-2">
          {diff.changed_slots.map((s) => (
            <li key={`${s.day}-${s.shift}`} className="flex flex-wrap items-center gap-2 text-[11.5px]">
              <span className="rounded bg-white px-1.5 py-[1px] font-semibold text-teal-800">
                {DAY_LABEL[s.day] ?? s.day} {s.shift}
              </span>
              {s.removed.map((e) => (
                <span key={`r-${e}`} className="inline-flex items-center gap-1 text-fail">
                  <MinusCircle size={11} />
                  {e}
                </span>
              ))}
              {s.added.map((e) => (
                <span key={`a-${e}`} className="inline-flex items-center gap-1 text-pass">
                  <PlusCircle size={11} />
                  {e}
                </span>
              ))}
            </li>
          ))}
          {diff.changed_slots.length === 0 ? (
            <li className="text-[11.5px] text-mut">后端未返回具体变动明细。</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
