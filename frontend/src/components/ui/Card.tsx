import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';

type Tone = 'default' | 'soft' | 'pass' | 'fail' | 'pend' | 'teal';

const TONES: Record<Tone, string> = {
  default: 'bg-white border-line',
  soft: 'bg-soft border-line',
  pass: 'bg-pass-bg border-pass-border',
  fail: 'bg-fail-bg border-fail-border',
  pend: 'bg-pend-bg border-pend-border',
  teal: 'bg-teal-50 border-teal-200',
};

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: Tone;
}

export function Card({ tone = 'default', className, children, ...rest }: CardProps) {
  return (
    <div
      className={cn('rounded-card border shadow-card', TONES[tone], className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  icon,
  title,
  subtitle,
  right,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex items-center gap-2 px-4 pt-3.5 pb-2.5', className)}>
      {icon ? <span className="flex-none text-teal-700">{icon}</span> : null}
      <div className="min-w-0 flex-1">
        <h3 className="truncate text-[14px] font-semibold tracking-[0.2px] text-ink">{title}</h3>
        {subtitle ? <p className="mt-0.5 truncate text-[11.5px] text-mut">{subtitle}</p> : null}
      </div>
      {right ? <div className="flex flex-none items-center gap-2">{right}</div> : null}
    </div>
  );
}

export function CardBody({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('px-4 pb-4', className)} {...rest}>
      {children}
    </div>
  );
}
