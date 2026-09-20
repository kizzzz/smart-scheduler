import { useState, type ReactNode } from 'react';
import { Info, Plus, X } from 'lucide-react';
import { cn } from '../../lib/utils';
import { Button } from '../ui/Button';
import { TextInput } from '../ui/Form';

/** 配置页内部复用的小零件。放在一起是为了让三个步骤组件的视觉语言保持一致。 */

export function SectionTitle({
  title,
  hint,
  right,
  className,
}: {
  title: ReactNode;
  hint?: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('mb-2 flex items-end gap-2', className)}>
      <div className="min-w-0 flex-1">
        <h4 className="text-[12.5px] font-semibold text-ink">{title}</h4>
        {hint ? <p className="mt-0.5 text-[11px] leading-relaxed text-mut">{hint}</p> : null}
      </div>
      {right ? <div className="flex flex-none items-center gap-1.5">{right}</div> : null}
    </div>
  );
}

/** 一段「为什么这样设计」的说明。配置项一多，用户最先问的就是这个 */
export function WhyNote({ children, tone = 'plain' }: { children: ReactNode; tone?: 'plain' | 'teal' }) {
  return (
    <p
      className={cn(
        'flex items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] leading-relaxed',
        tone === 'teal' ? 'border-teal-200 bg-teal-50/70 text-teal-900' : 'border-line bg-soft text-mut',
      )}
    >
      <Info size={12} className={cn('mt-[2px] flex-none', tone === 'teal' ? 'text-teal-700' : 'text-mut-2')} />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

export function ToggleChip({
  selected,
  onClick,
  children,
  disabled,
  title,
  tone = 'teal',
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
  disabled?: boolean;
  title?: string;
  tone?: 'teal' | 'pend';
}) {
  const on =
    tone === 'pend'
      ? 'border-pend bg-pend-bg text-pend-deep'
      : 'border-teal-600 bg-teal-50 text-teal-800';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-pressed={selected}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-[2px] text-[11px] font-medium transition-colors duration-150 focus-ring',
        selected ? on : 'border-line bg-white text-mut hover:border-teal-300 hover:text-teal-800',
        disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
      )}
    >
      {children}
    </button>
  );
}

/**
 * 字典编辑器（角色池 / 技能池）。
 *
 * 删除按钮在「还有人或规则在用」时是禁用而不是隐藏的：隐藏会让用户以为这一项不能删，
 * 而禁用 + title 能说清「先去解绑」这个前置条件。
 */
export function DictEditor({
  label,
  hint,
  items,
  addPlaceholder,
  onAdd,
  removeBlockReason,
  onRemove,
}: {
  label: string;
  hint?: string;
  items: string[];
  addPlaceholder: string;
  onAdd: (value: string) => void;
  /** 返回非空字符串 = 不允许删除，字符串即原因 */
  removeBlockReason: (item: string) => string | null;
  onRemove: (item: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const submit = () => {
    const v = draft.trim();
    if (!v) return;
    onAdd(v);
    setDraft('');
  };

  return (
    <div className="rounded-lg border border-line bg-white px-2.5 py-2">
      <p className="text-[11px] font-semibold text-mut">
        {label}
        <span className="ml-1 font-normal text-mut-2">{items.length} 项</span>
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {items.length === 0 ? (
          <span className="text-[11px] text-mut-2">还没有任何项，先添加一个</span>
        ) : null}
        {items.map((item) => {
          const blocked = removeBlockReason(item);
          return (
            <span
              key={item}
              className="inline-flex items-center gap-1 rounded-full border border-line bg-soft py-[2px] pl-2 pr-1 text-[11px] text-ink-2"
            >
              {item}
              <button
                type="button"
                onClick={() => !blocked && onRemove(item)}
                disabled={Boolean(blocked)}
                title={blocked ?? `删除「${item}」`}
                aria-label={`删除 ${item}`}
                className="rounded-full p-[2px] text-mut-2 transition-colors duration-150 hover:bg-fail-bg hover:text-fail disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-mut-2 focus-ring"
              >
                <X size={10} />
              </button>
            </span>
          );
        })}
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        <TextInput
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={addPlaceholder}
          size="sm"
        />
        <Button size="sm" variant="outline" onClick={submit} disabled={!draft.trim()}>
          <Plus size={12} />
          添加
        </Button>
      </div>
      {hint ? <p className="mt-1 text-[10.5px] leading-snug text-mut-2">{hint}</p> : null}
    </div>
  );
}
