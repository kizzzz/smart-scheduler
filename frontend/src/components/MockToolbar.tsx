import { FlaskConical } from 'lucide-react';
import { MOCK_CASES, type MockCase } from '../mock';
import { cn } from '../lib/utils';

export function MockToolbar({
  current,
  onSelect,
}: {
  current: MockCase | null;
  onSelect: (c: MockCase) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-card border border-pend-border bg-pend-bg px-3.5 py-2">
      <span className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-pend-deep">
        <FlaskConical size={12} />
        Mock 自验模式
      </span>
      <span className="text-[11px] text-[#8A6017]">
        走 src/mock 下的契约样例数据，不请求后端；换人 / 应用修复建议由前端参考校验器实时反馈。
      </span>
      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        {MOCK_CASES.map((c) => (
          <button
            key={c.value}
            type="button"
            onClick={() => onSelect(c.value)}
            className={cn(
              'rounded-md border px-2 py-[3px] text-[11px] font-semibold transition-colors duration-150 focus-ring',
              current === c.value
                ? 'border-pend bg-white text-pend-deep'
                : 'border-pend-border bg-white/60 text-[#8A6017] hover:bg-white',
            )}
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}
