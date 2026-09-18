import { CalendarPlus, FileUp, MousePointerClick, ShieldCheck, Sparkles } from 'lucide-react';
import { Card } from './ui/Card';

const EXAMPLES = [
  '下周正常排班，尽量满足大家的班次偏好',
  '下周正常排班，E05 周六请假，周末早班多留一个收银',
  '下周正常排班，注意 E01 周二培训、E02 和 E04 周二都请假',
];

export function EmptyBoard({ onPick }: { onPick: (text: string) => void }) {
  return (
    <Card className="px-6 py-9">
      <div className="mx-auto max-w-[620px] text-center">
        <div className="mx-auto mb-4 w-fit">
          <div
            className="grid gap-1.5 rounded-xl border border-line bg-soft p-3"
            style={{ gridTemplateColumns: 'repeat(7, 26px)' }}
          >
            {Array.from({ length: 14 }).map((_, i) => (
              <div
                key={i}
                className={`h-7 rounded-[5px] border border-dashed ${
                  i % 7 >= 5 ? 'border-teal-200 bg-teal-50/60' : 'border-line bg-white'
                }`}
              />
            ))}
          </div>
        </div>

        <h3 className="text-[15px] font-semibold text-ink">还没有排班表</h3>
        <p className="mx-auto mt-1.5 max-w-[430px] text-[12px] leading-relaxed text-mut">
          用一句话描述下周的排班需求，AI 会生成 0→80 的草案：先解析意图，再由确定性求解器排班，最后过一遍 9
          条硬规则校验。你只需要做 80→100 的微调。
        </p>

        <div className="mt-4 flex flex-col items-stretch gap-1.5 text-left">
          <p className="text-[11px] font-semibold text-mut">试试这些指令</p>
          {EXAMPLES.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onPick(t)}
              className="group flex items-center gap-2 rounded-lg border border-line bg-white px-3 py-2 text-[12px] text-ink-2 transition-colors duration-150 hover:border-teal-300 hover:bg-teal-50 focus-ring"
            >
              <Sparkles size={13} className="flex-none text-teal-600" />
              <span className="min-w-0 flex-1 truncate">{t}</span>
              <span className="flex-none text-[10.5px] text-mut-2 group-hover:text-teal-700">一键填入</span>
            </button>
          ))}
        </div>

        <div className="mt-5 grid gap-3 border-t border-line-2 pt-4 text-left sm:grid-cols-2 lg:grid-cols-4">
          {[
            { icon: <CalendarPlus size={13} />, t: '0→80 草案', d: '7 天 × 2 班次一次成型' },
            { icon: <ShieldCheck size={13} />, t: '9 条硬规则', d: '独立校验器兜底，零违规' },
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
