import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';
type Size = 'sm' | 'md' | 'lg';

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-teal-700 text-white border border-teal-700 hover:bg-teal-800 hover:border-teal-800 active:bg-teal-900 disabled:bg-teal-700/45 disabled:border-transparent',
  secondary:
    'bg-white text-ink-2 border border-line hover:border-teal-200 hover:text-teal-800 hover:bg-teal-50/60',
  outline:
    'bg-white text-teal-800 border border-teal-200 hover:bg-teal-50 hover:border-teal-400',
  ghost: 'bg-transparent text-mut border border-transparent hover:bg-line-2 hover:text-ink-2',
  danger:
    'bg-white text-fail-deep border border-fail-border hover:bg-fail-bg hover:border-fail',
};

const SIZES: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-[12px] gap-1.5 rounded-md',
  md: 'h-9 px-3.5 text-[13px] gap-2 rounded-lg',
  lg: 'h-11 px-5 text-[14px] font-semibold gap-2 rounded-[10px]',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children?: ReactNode;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium transition-colors duration-150 focus-ring disabled:cursor-not-allowed disabled:opacity-70',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
