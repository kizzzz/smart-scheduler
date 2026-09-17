import { FileText, Info, TriangleAlert } from 'lucide-react';
import type { Explanation, Timing } from '../types';
import { ms } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Card, CardBody, CardHeader } from './ui/Card';

export function ExplanationCard({
  explanation,
  timing,
}: {
  explanation: Explanation;
  timing?: Timing | null;
}) {
  return (
    <Card tone="soft">
      <CardHeader
        icon={<FileText size={15} />}
        title="为什么这样排"
        subtitle="基于求解器决策日志生成，可直接转述给店员"
        right={
          explanation.degraded ? (
            <Badge tone="pend">解释生成降级</Badge>
          ) : (
            <Badge tone="neutral">LLM 生成解释</Badge>
          )
        }
      />
      <CardBody>
        {explanation.degraded ? (
          <div className="mb-2.5 flex items-start gap-2 rounded-lg border border-pend-border bg-pend-bg px-2.5 py-2 text-[11.5px] leading-relaxed text-pend-deep">
            <TriangleAlert size={13} className="mt-[1px] flex-none text-pend" />
            <span>解释生成降级，以下为结构化校验摘要，内容仍来自真实求解与校验数据。</span>
          </div>
        ) : null}

        <ul className="space-y-1.5">
          {explanation.bullets.length === 0 ? (
            <li className="text-[12px] text-mut">本次没有额外的决策说明。</li>
          ) : (
            explanation.bullets.map((b, i) => (
              <li key={i} className="relative pl-4 text-[12px] leading-relaxed text-ink-2">
                <span className="absolute left-[3px] top-[7px] h-[5px] w-[5px] rounded-full border border-teal-700 bg-teal-200" />
                {b}
              </li>
            ))
          )}
        </ul>

        <div className="mt-3 rounded-lg border border-line bg-white px-3 py-2.5">
          <p className="mb-1.5 flex items-center gap-1.5 text-[11.5px] font-semibold text-ink-2">
            <Info size={12} className="text-mut" />
            未满足的偏好
            <span className="font-normal text-mut-2">（不是错误，仅供店长知情）</span>
          </p>
          {explanation.unmet_preferences.length === 0 ? (
            <p className="text-[11.5px] text-mut">本次所有员工的班次偏好均已满足。</p>
          ) : (
            <ul className="space-y-1">
              {explanation.unmet_preferences.map((u, i) => (
                <li key={i} className="relative pl-4 text-[11.5px] leading-relaxed text-mut">
                  <span className="absolute left-[3px] top-[7px] h-[5px] w-[5px] rounded-full border border-mut-2 bg-white" />
                  {u}
                </li>
              ))}
            </ul>
          )}
        </div>

        {timing ? (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-mut-2">
            <span>解析 {ms(timing.parse_ms)}</span>
            <span>求解 {ms(timing.solve_ms)}</span>
            <span>校验 {ms(timing.validate_ms)}</span>
            <span>解释 {ms(timing.explain_ms)}</span>
            <span className="font-semibold text-mut">合计 {ms(timing.total_ms)}</span>
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
