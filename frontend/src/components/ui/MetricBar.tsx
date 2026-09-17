import { clamp01, cn } from '../../lib/utils';

export function MetricBar({
  label,
  value,
  display,
  hint,
  tone = 'teal',
  muted = false,
}: {
  label: string;
  value: number;
  display: string;
  hint?: string;
  tone?: 'teal' | 'pass' | 'pend';
  /** 无数据（待排班）时置灰并清零进度 */
  muted?: boolean;
}) {
  const fill = {
    teal: 'bg-teal-700',
    pass: 'bg-pass',
    pend: 'bg-pend',
  }[tone];

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="truncate text-[11.5px] text-mut">{label}</span>
        <b className={cn('text-[13px] font-semibold tabular-nums', muted ? 'text-mut-2' : 'text-ink')}>
          {display}
        </b>
      </div>
      <div className="h-[5px] overflow-hidden rounded-full bg-line-2">
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-500 ease-out',
            muted ? 'bg-line' : fill,
          )}
          style={{ width: muted ? '0%' : `${clamp01(value) * 100}%` }}
        />
      </div>
      {hint ? <p className="mt-1 text-[10.5px] leading-snug text-mut-2">{hint}</p> : null}
    </div>
  );
}
