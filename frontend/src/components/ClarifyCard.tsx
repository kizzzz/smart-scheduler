import { HelpCircle, MessageSquareQuote, PencilLine, Quote } from 'lucide-react';
import type { Clarification } from '../types';
import { Badge } from './ui/Badge';

/** 常见含糊表述 → 可直接排班的改写示例（纯前端文案，点一下替换输入框，不调接口） */
const REWRITES: Array<{ label: string; text: string; why: string }> = [
  {
    label: '用员工编号替代姓名',
    text: 'E06 周四请假，重新排一下，其他人的班尽量别动。',
    why: '把「小王」换成档案里的编号，把「明天」换成具体星期',
  },
  {
    label: '说清是整天还是某个班',
    text: 'E06 周四晚班不要排他，其他照常排。',
    why: '只有一个班次不可用时，别让求解器整天空出来',
  },
  {
    label: '直接指定谁来顶班',
    text: 'E06 周四请假，周四早班让 E16 顶上，其他人的班别动。',
    why: '你已有人选时，写成「指定在岗」比让 AI 猜更快',
  },
];

export function ClarifyCard({
  clarification,
  lead,
  onUseRewrite,
}: {
  clarification: Clarification;
  lead?: string;
  onUseRewrite: (text: string) => void;
}) {
  return (
    <section className="space-y-3">
      <div className="rounded-card border-[1.5px] border-pend-border bg-pend-bg px-4 py-3.5">
        <div className="flex items-center gap-2">
          <HelpCircle size={16} className="flex-none text-pend" />
          <h3 className="text-[14.5px] font-semibold text-pend-deep">需要你确认一下</h3>
          <div className="ml-auto flex items-center gap-2">
            <Badge tone="pend">指令无法唯一确定</Badge>
            <Badge tone="pend">未生成排班</Badge>
          </div>
        </div>

        <p className="mt-2 text-[12.5px] leading-relaxed text-[#7C4F0C]">
          {lead ?? '这条指令还不能唯一确定，先确认以下问题再排，避免排错班。'}
        </p>

        <div className="mt-2.5 flex items-start gap-2 rounded-lg border border-pend-border bg-white px-2.5 py-2">
          <Quote size={12} className="mt-[3px] flex-none text-pend" />
          <p className="min-w-0 flex-1 text-[11.5px] leading-relaxed text-mut">
            你的原话：<span className="text-ink-2">{clarification.raw || '（空指令）'}</span>
          </p>
        </div>

        <ul className="mt-2.5 space-y-1.5">
          {clarification.questions.map((q, i) => (
            <li
              key={`${q}-${i}`}
              className="flex items-start gap-2 rounded-lg border border-pend-border bg-white px-2.5 py-2"
            >
              <span className="mt-[1px] flex h-[17px] w-[17px] flex-none items-center justify-center rounded-full bg-pend-bg text-[10px] font-semibold text-pend-deep">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ink-2">{q}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-card border border-line bg-white px-4 py-3 shadow-card">
        <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-2">
          <MessageSquareQuote size={13} className="text-teal-700" />
          改写建议
          <span className="font-normal text-mut-2">点一下直接替换上方输入框，再点「生成排班」</span>
        </p>
        <div className="mt-2 grid gap-2 md:grid-cols-3">
          {REWRITES.map((r) => (
            <button
              key={r.label}
              type="button"
              onClick={() => onUseRewrite(r.text)}
              className="group flex flex-col rounded-lg border border-line bg-soft px-3 py-2.5 text-left transition-colors duration-150 hover:border-teal-200 hover:bg-teal-50 focus-ring"
            >
              <span className="flex items-center gap-1.5 text-[11.5px] font-semibold text-teal-800">
                <PencilLine size={11} />
                {r.label}
              </span>
              <span className="mt-1 text-[12px] leading-relaxed text-ink-2">「{r.text}」</span>
              <span className="mt-1 text-[10.5px] leading-snug text-mut-2">{r.why}</span>
            </button>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-mut-2">
          澄清态不产出排班表：宁可多问一句，也不猜错一个人的班。
        </p>
      </div>
    </section>
  );
}
