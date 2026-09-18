import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, ChevronDown, Cpu, Eye, Info, Lock, MessageSquareText } from 'lucide-react';
import type { LockedModel, ModelCatalog, ModelOption } from '../types';
import { cn } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Tooltip } from './ui/Tooltip';

/**
 * TopBar 内的模型选择器。文本 / 视觉分两组，locked 模型灰显并给出 reason。
 *
 * 这里最重要的不是选择本身，而是把 `boundary_note` 顶在最前面：
 * 用户天然会以为「换更强的模型排得更好」，而模型只影响自然语言理解与解释，
 * 排班可行性由求解器 + 校验器保证。这个边界说不清，功能就会制造误解。
 */
export function ModelPicker({
  catalog,
  loading,
  textModel,
  visionModel,
  onSelectText,
  onSelectVision,
}: {
  catalog: ModelCatalog | null;
  loading: boolean;
  textModel: string | null;
  visionModel: string | null;
  onSelectText: (id: string) => void;
  onSelectVision: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const current = catalog?.text.find((m) => m.id === textModel);
  const triggerText = loading ? '模型加载中' : (current?.label ?? '后端默认模型');

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={loading || !catalog}
        title={catalog ? '选择解析与解释使用的模型' : '模型清单不可用，将使用后端默认模型'}
        className={cn(
          'inline-flex max-w-[210px] items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11px] transition-colors duration-150 focus-ring',
          open
            ? 'border-teal-400 bg-teal-50 text-teal-800'
            : 'border-line bg-white text-mut hover:border-teal-200 hover:text-teal-800',
          !catalog && 'cursor-not-allowed opacity-70',
        )}
      >
        <Cpu size={11} className="flex-none" />
        <span className="truncate font-semibold">{triggerText}</span>
        <ChevronDown size={11} className={cn('flex-none transition-transform duration-150', open && 'rotate-180')} />
      </button>

      {open && catalog ? (
        <div className="absolute right-0 top-[calc(100%+8px)] z-40 w-[366px] animate-fade-in overflow-hidden rounded-card border border-line bg-white shadow-pop">
          <div className="border-b border-line-2 px-3.5 py-2.5">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
              <Cpu size={13} className="text-teal-700" />
              模型选择
              <Tooltip content={catalog.boundary_note} className="ml-auto inline-flex">
                <span className="inline-flex cursor-help items-center gap-1 text-[10.5px] font-normal text-mut">
                  <Info size={11} />
                  影响范围
                </span>
              </Tooltip>
            </h3>
            <p className="mt-1 rounded-lg border border-teal-200 bg-teal-50/70 px-2 py-1.5 text-[11px] leading-relaxed text-teal-900">
              {catalog.boundary_note}
            </p>
          </div>

          <div className="max-h-[min(64vh,520px)] overflow-y-auto px-3.5 py-2.5 scrollbar-thin">
            <Group
              icon={<MessageSquareText size={12} />}
              title="文本模型"
              hint="用于意图解析与解释生成"
              options={catalog.text}
              selected={textModel ?? catalog.default_text}
              onSelect={onSelectText}
            />
            <Group
              icon={<Eye size={12} />}
              title="视觉模型"
              hint="仅在导入排班表图片时调用"
              options={catalog.vision}
              selected={visionModel ?? catalog.default_vision}
              onSelect={onSelectVision}
            />
            {catalog.locked.length ? <LockedGroup locked={catalog.locked} note={catalog.unlock_note} /> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Group({
  icon,
  title,
  hint,
  options,
  selected,
  onSelect,
}: {
  icon: ReactNode;
  title: string;
  hint: string;
  options: ModelOption[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="mb-3 last:mb-0">
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-mut">
        <span className="text-teal-700">{icon}</span>
        {title}
        <span className="font-normal text-mut-2">· {hint}</span>
      </p>
      <div className="space-y-1.5">
        {options.map((m) => (
          <OptionRow key={m.id} option={m} active={m.id === selected} onSelect={() => onSelect(m.id)} />
        ))}
      </div>
    </section>
  );
}

/** measured 是结构化实测数据，标签由前端补，数值原样展示；没有实测的模型后端不给这个字段 */
function measuredLine(option: ModelOption): string {
  const m = option.measured;
  const parts: string[] = [];
  if (m?.avg_latency_s !== undefined) parts.push(`实测均值 ${m.avg_latency_s}s`);
  if (m?.hallucinated_cases) parts.push(`编造 ${m.hallucinated_cases} case`);
  if (m?.cell_accuracy) parts.push(`格子准确率 ${m.cell_accuracy}`);
  if (option.max_output_tokens) parts.push(`输出上限 ${option.max_output_tokens} tokens`);
  if (m?.probed_at) parts.push(`探测于 ${m.probed_at}`);
  return parts.join(' · ');
}

function OptionRow({
  option,
  active,
  onSelect,
}: {
  option: ModelOption;
  active: boolean;
  onSelect: () => void;
}) {
  const measured = measuredLine(option);

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'w-full rounded-lg border px-2.5 py-2 text-left transition-colors duration-150 focus-ring',
        active ? 'border-teal-400 bg-teal-50/60' : 'border-line bg-white hover:border-teal-200 hover:bg-teal-50/40',
      )}
    >
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            'flex h-[14px] w-[14px] flex-none items-center justify-center rounded-full border',
            active ? 'border-teal-700 bg-teal-700 text-white' : 'border-line-2 bg-white',
          )}
        >
          {active ? <Check size={9} strokeWidth={3} /> : null}
        </span>
        <b className="truncate text-[12.5px] font-semibold text-ink">{option.label}</b>
        {option.is_default ? <Badge tone="neutral">默认</Badge> : null}
        {option.tier === 'paid' ? <Badge tone="pend">付费</Badge> : null}
      </span>
      <span className="mt-1 flex flex-wrap items-center gap-1.5 pl-[20px]">
        <Badge tone="teal">{option.latency_hint}</Badge>
        <Badge tone="neutral">{option.accuracy_hint}</Badge>
      </span>
      <span className="mt-1 block pl-[20px] text-[11px] leading-snug text-mut">{option.note}</span>
      {measured ? (
        <span className="mt-0.5 block pl-[20px] font-mono text-[10px] leading-snug text-mut-2">{measured}</span>
      ) : null}
    </button>
  );
}

/**
 * 灰显而不是隐藏：让用户知道这些模型存在、以及为什么现在不能选，
 * 比「界面上根本没有」更能解释清楚当前系统的边界。
 */
function LockedGroup({ locked, note }: { locked: LockedModel[]; note: string }) {
  return (
    <section className="border-t border-line-2 pt-2.5">
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-mut">
        <Lock size={11} className="text-mut-2" />
        当前不可用（{locked.length}）
      </p>
      <div className="space-y-1">
        {locked.map((m) => (
          <div key={m.id} className="rounded-lg border border-dashed border-line bg-soft px-2.5 py-1.5">
            <p className="truncate text-[12px] font-semibold text-mut-2">{m.id}</p>
            <p className="mt-0.5 text-[10.5px] leading-snug text-mut-2">{m.reason}</p>
          </div>
        ))}
      </div>
      <p className="mt-1.5 text-[10.5px] leading-relaxed text-mut-2">{note}</p>
    </section>
  );
}
