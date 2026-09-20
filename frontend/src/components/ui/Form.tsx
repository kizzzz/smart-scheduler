import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react';
import { Check, ChevronDown, Minus, Plus } from 'lucide-react';
import { cn } from '../../lib/utils';

/**
 * 配置页用到的表单原子组件。
 *
 * 刻意不引入表单库：这里需要的只是「统一的边框、焦点环、错误态」，
 * 而项目的视觉语言已经由 Button/Badge/Card 定好了，再塞一套组件库的样式体系
 * 只会让 teal 主色和圆角在两处各自演化。
 */

const BASE_CONTROL =
  'w-full rounded-md border bg-white px-2 text-[12.5px] text-ink transition-colors duration-150 placeholder:text-mut-2 focus:outline-none disabled:bg-soft disabled:text-mut';

/**
 * 控件高度做成 prop 而不是靠 className 覆盖。
 * 原因很实际：`cn(..., 'h-8', className)` 里传 `h-7` 并不一定生效 ——
 * 谁赢取决于 Tailwind 生成 CSS 的先后顺序，而不是这里的拼接顺序。
 */
export type ControlSize = 'sm' | 'md';

function sizeClass(size: ControlSize): string {
  return size === 'sm' ? 'h-7' : 'h-8';
}

/**
 * 控件问题有两档：`true`/'error' 是排不出班的硬错误，'warn' 是「这么填等于没限制」之类的提醒。
 * 两者必须长得不一样 —— 否则用户会看到红框却发现「生成」按钮是可点的，只能怀疑是 bug。
 */
export type InvalidTone = boolean | 'warn';

export interface FieldIssue {
  message: string;
  level: 'error' | 'warn';
}

/** 把 FormIssue（或 null）翻译成控件的 invalid 取值，省得每个调用点都写三元 */
export function toneOf(issue: FieldIssue | null | undefined): InvalidTone {
  if (!issue) return false;
  return issue.level === 'warn' ? 'warn' : true;
}

function controlTone(invalid?: InvalidTone): string {
  if (invalid === 'warn') return 'border-pend-border focus:border-pend hover:border-pend';
  return invalid
    ? 'border-fail-border focus:border-fail'
    : 'border-line focus:border-teal-500 hover:border-teal-200';
}

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  /**
   * 有值即显示提示；多条只显示第一条，超过一条时用户先修完第一个再看下一个更不容易懵。
   * 传字符串＝按硬错误显示，传 FormIssue 则按其 level 决定红字还是黄字。
   */
  error?: string | FieldIssue | null;
  children: ReactNode;
  className?: string;
  htmlFor?: string;
}) {
  const msg = typeof error === 'string' ? error : (error?.message ?? null);
  const warn = typeof error === 'object' && error !== null && error.level === 'warn';
  return (
    <div className={cn('min-w-0', className)}>
      {label ? (
        <label htmlFor={htmlFor} className="mb-1 block text-[11px] font-semibold text-mut">
          {label}
        </label>
      ) : null}
      {children}
      {msg ? (
        <p className={cn('mt-1 text-[10.5px] leading-snug', warn ? 'text-pend-deep' : 'text-fail')}>
          {msg}
        </p>
      ) : hint ? (
        <p className="mt-1 text-[10.5px] leading-snug text-mut-2">{hint}</p>
      ) : null}
    </div>
  );
}

export interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  invalid?: InvalidTone;
  size?: ControlSize;
}

export function TextInput({ invalid, size = 'md', className, ...rest }: TextInputProps) {
  return (
    <input className={cn(BASE_CONTROL, sizeClass(size), controlTone(invalid), className)} {...rest} />
  );
}

export function Select({
  invalid,
  size = 'md',
  className,
  children,
  ...rest
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> & {
  invalid?: InvalidTone;
  size?: ControlSize;
}) {
  return (
    <span className="relative block">
      <select
        className={cn(
          BASE_CONTROL,
          sizeClass(size),
          'appearance-none pr-6',
          controlTone(invalid),
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown
        size={12}
        className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-mut-2"
      />
    </span>
  );
}

/**
 * 数值输入。`value=null` 表示留空（例如「个人上限留空 = 用全局规则」），
 * 所以不能用受控 number input 的 0 来代表空——那会让「不限制」和「0 个班」混成一个值。
 */
export function NumberInput({
  value,
  onChange,
  min,
  max,
  step = 1,
  invalid,
  placeholder,
  suffix,
  className,
  size = 'md',
  disabled,
  'aria-label': ariaLabel,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  invalid?: InvalidTone;
  placeholder?: string;
  suffix?: string;
  className?: string;
  size?: ControlSize;
  disabled?: boolean;
  'aria-label'?: string;
}) {
  return (
    <span className={cn('relative block', className)}>
      <input
        type="number"
        inputMode="numeric"
        aria-label={ariaLabel}
        value={value === null ? '' : value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') {
            onChange(null);
            return;
          }
          const n = Number(raw);
          onChange(Number.isNaN(n) ? null : n);
        }}
        className={cn(
          BASE_CONTROL,
          sizeClass(size),
          'tabular-nums',
          suffix ? 'pr-8' : '',
          controlTone(invalid),
        )}
      />
      {suffix ? (
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10.5px] text-mut-2">
          {suffix}
        </span>
      ) : null}
    </span>
  );
}

export function Stepper({
  value,
  onChange,
  min = 1,
  max = 99,
  suffix,
  disabled,
  label,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  suffix?: string;
  disabled?: boolean;
  label?: string;
}) {
  const btn =
    'flex h-7 w-7 flex-none items-center justify-center rounded-md border border-line bg-white text-mut transition-colors duration-150 hover:border-teal-300 hover:text-teal-700 disabled:cursor-not-allowed disabled:text-mut-2 disabled:hover:border-line focus-ring';
  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        type="button"
        className={btn}
        aria-label={`${label ?? ''}减少`}
        disabled={disabled || value <= min}
        onClick={() => onChange(Math.max(min, value - 1))}
      >
        <Minus size={12} />
      </button>
      <b className="min-w-[44px] text-center text-[13px] font-semibold tabular-nums text-ink">
        {value}
        {suffix ? <span className="ml-0.5 text-[10.5px] font-normal text-mut">{suffix}</span> : null}
      </b>
      <button
        type="button"
        className={btn}
        aria-label={`${label ?? ''}增加`}
        disabled={disabled || value >= max}
        onClick={() => onChange(Math.min(max, value + 1))}
      >
        <Plus size={12} />
      </button>
    </span>
  );
}

export function Slider({
  value,
  onChange,
  min,
  max,
  ariaLabel,
  className,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  ariaLabel: string;
  className?: string;
}) {
  return (
    <input
      type="range"
      aria-label={ariaLabel}
      value={value}
      min={min}
      max={max}
      step={1}
      onChange={(e) => onChange(Number(e.target.value))}
      className={cn('h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line-2 accent-teal-700', className)}
    />
  );
}

export function Switch({
  checked,
  onChange,
  disabled,
  label,
  size = 'md',
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** 无障碍名称；视觉上的文字由调用方自己排版 */
  label: string;
  size?: 'sm' | 'md';
}) {
  const w = size === 'sm' ? 'h-4 w-7' : 'h-[18px] w-8';
  const knob = size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5';
  const shift = size === 'sm' ? 'translate-x-3' : 'translate-x-[14px]';
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex flex-none items-center rounded-full border transition-colors duration-150 focus-ring',
        w,
        checked ? 'border-teal-700 bg-teal-700' : 'border-line bg-[#E9EEED]',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
      )}
    >
      <span
        className={cn(
          'ml-[2px] rounded-full bg-white shadow-card transition-transform duration-150',
          knob,
          checked ? shift : 'translate-x-0',
        )}
      />
    </button>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  indeterminate,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  /** 批量选择的「部分选中」态：全选框必须能表达出来，否则用户不知道当前选了一部分 */
  indeterminate?: boolean;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? 'mixed' : checked}
      aria-label={label}
      title={label}
      onClick={() => onChange(!checked)}
      className={cn(
        'flex h-[15px] w-[15px] flex-none items-center justify-center rounded-[4px] border transition-colors duration-150 focus-ring',
        checked || indeterminate
          ? 'border-teal-700 bg-teal-700 text-white'
          : 'border-line bg-white hover:border-teal-400',
      )}
    >
      {indeterminate ? (
        <Minus size={10} strokeWidth={3} />
      ) : checked ? (
        <Check size={10} strokeWidth={3} />
      ) : null}
    </button>
  );
}
