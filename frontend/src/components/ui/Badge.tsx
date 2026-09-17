import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

type Tone = 'neutral' | 'pass' | 'fail' | 'pend' | 'teal';

const TONES: Record<Tone, string> = {
  neutral: 'text-mut bg-[#F1F5F4] border-line',
  pass: 'text-pass bg-pass-bg border-pass-border',
  fail: 'text-fail bg-fail-bg border-fail-border',
  pend: 'text-pend bg-pend-bg border-pend-border',
  teal: 'text-teal-800 bg-teal-50 border-teal-200',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
  icon,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  icon?: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-[1.5px] text-[10.5px] font-semibold leading-4 transition-colors duration-150',
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}
