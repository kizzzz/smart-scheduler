import { clamp01, cn } from '../../lib/utils';

export function MetricBar({
  label,
  value,
  display,
  hint,
  tone = 'teal',
}: {
  label: string;
  value: number;
  display: string;
  hint?: string;
  tone?: 'teal' | 'pass' | 'pend';
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
        <b className="text-[13px] font-semibold tabular-nums text-ink">{display}</b>
      </div>
      <div className="h-[5px] overflow-hidden rounded-full bg-line-2">
        <div
          className={cn('h-full rounded-full transition-[width] duration-500 ease-out', fill)}
          style={{ width: `${clamp01(value) * 100}%` }}
        />
      </div>
      {hint ? <p className="mt-1 text-[10.5px] leading-snug text-mut-2">{hint}</p> : null}
    </div>
  );
}
