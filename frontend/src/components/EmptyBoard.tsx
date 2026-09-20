import { CalendarPlus, FileUp, MousePointerClick, ShieldCheck, Sparkles } from 'lucide-react';
import { Card } from './ui/Card';

/**
 * 空状态。
 *
 * 这里所有的数字都必须来自当前配置：空状态是用户的第一印象，
 * 写死「7 天 × 2 班 / 9 条硬规则」的话，一个改成 3 天三班制的门店会在第一屏就看到假话。
 * 维度未知（配置还没加载完）时宁可不提数字。
 */
export function EmptyBoard({
  dims,
  ruleCount,
  examples,
  onPick,
}: {
  /** 当前配置的看板形状；null = 配置还没就位 */
  dims: { days: number; shifts: number; peak: boolean[] } | null;
  ruleCount: number | null;
  examples: string[];
  onPick: (text: string) => void;
}) {
  const cols = Math.min(dims?.days ?? 7, 14);
  const rows = Math.min(dims?.shifts ?? 2, 4);
  const shape = dims ? `${dims.days} 天 × ${dims.shifts} 班` : '你配置的天数 × 班次';

  return (
    <Card className="px-6 py-9">
      <div className="mx-auto max-w-[620px] text-center">
        <div className="mx-auto mb-4 w-fit">
          <div
            className="grid gap-1.5 rounded-xl border border-line bg-soft p-3"
            style={{ gridTemplateColumns: `repeat(${cols}, 26px)` }}
          >
            {Array.from({ length: cols * rows }).map((_, i) => (
              <div
                key={i}
                className={`h-7 rounded-[5px] border border-dashed ${
                  dims?.peak[i % cols] ? 'border-teal-200 bg-teal-50/60' : 'border-line bg-white'
                }`}
              />
            ))}
          </div>
        </div>

        <h3 className="text-[15px] font-semibold text-ink">还没有排班表</h3>
        <p className="mx-auto mt-1.5 max-w-[430px] text-[12px] leading-relaxed text-mut">
          用一句话描述下个周期的排班需求，AI 会生成 0→80 的草案：先解析意图，再由确定性求解器排班
          {ruleCount ? `，最后过一遍 ${ruleCount} 条硬规则校验` : '，最后过一遍硬规则校验'}
          。你只需要做 80→100 的微调。
        </p>

        <div className="mt-4 flex flex-col items-stretch gap-1.5 text-left">
          <p className="text-[11px] font-semibold text-mut">试试这些指令</p>
          {examples.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onPick(t)}
              className="group flex items-center gap-2 rounded-lg border border-line bg-white px-3 py-2 text-[12px] text-ink-2 transition-colors duration-150 hover:border-teal-300 hover:bg-teal-50 focus-ring"
            >
              <Sparkles size={13} className="flex-none text-teal-600" />
              <span className="min-w-0 flex-1 truncate">{t}</span>
              <span className="flex-none text-[10.5px] text-mut-2 group-hover:text-teal-700">
                一键填入
              </span>
            </button>
          ))}
        </div>

        <div className="mt-5 grid gap-3 border-t border-line-2 pt-4 text-left sm:grid-cols-2 lg:grid-cols-4">
          {[
            { icon: <CalendarPlus size={13} />, t: '0→80 草案', d: `${shape}一次成型` },
            {
              icon: <ShieldCheck size={13} />,
              t: ruleCount ? `${ruleCount} 条硬规则` : '硬规则校验',
              d: '独立校验器兜底，零违规',
            },
            { icon: <MousePointerClick size={13} />, t: '点击换人', d: '微调即时校验，不做拖拽' },
            { icon: <FileUp size={13} />, t: '导入体检', d: '已有排班表拖进来查违规' },
          ].map((x) => (
            <div key={x.t} className="rounded-lg border border-line bg-soft px-3 py-2">
              <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-2">
                <span className="text-teal-700">{x.icon}</span>
                {x.t}
              </p>
              <p className="mt-0.5 text-[11px] text-mut">{x.d}</p>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
