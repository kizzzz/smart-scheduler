import { Loader2 } from 'lucide-react';
import type { SolvePhase } from '../types';
import { Card, CardBody, CardHeader } from './ui/Card';
import { Skeleton, SkeletonChip } from './ui/Skeleton';
import { PHASE_TEXT } from './InstructionPanel';
import { Badge } from './ui/Badge';

/** 进度条上的文案也得跟着配置走：格数与规则条数都是配置算出来的，不是常量 */
function steps(
  slotCount: number | null,
  ruleCount: number | null,
): Array<{ key: SolvePhase; label: string; detail: string }> {
  return [
    { key: 'parse', label: '解析指令', detail: 'LLM 抽取周期 / 临时约束 / 优化目标' },
    {
      key: 'solve',
      label: '求解排班',
      detail: slotCount ? `确定性回溯 + 随机重启，${slotCount} 个格子` : '确定性回溯 + 随机重启',
    },
    {
      key: 'validate',
      label: '校验规则',
      detail: ruleCount ? `独立校验器逐条核对 ${ruleCount} 条硬规则` : '独立校验器逐条核对硬规则',
    },
    { key: 'explain', label: '生成解释', detail: '把决策日志翻译成店长能懂的人话' },
  ];
}

export function BoardSkeleton({
  phase,
  slotCount = null,
  ruleCount = null,
}: {
  phase: SolvePhase;
  slotCount?: number | null;
  ruleCount?: number | null;
}) {
  const STEPS = steps(slotCount, ruleCount);
  const activeIndex = STEPS.findIndex((s) => s.key === phase);

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          icon={<Loader2 size={15} className="animate-spin" />}
          title={`${PHASE_TEXT[phase]}…`}
          subtitle="求解与校验都在后端完成，前端不做规则裁决"
          right={<Badge tone="teal">进行中 {activeIndex + 1}/4</Badge>}
        />
        <CardBody>
          <div className="grid gap-2 sm:grid-cols-4">
            {STEPS.map((s, i) => (
              <div
                key={s.key}
                className={`rounded-lg border px-2.5 py-2 transition-colors duration-300 ${
                  i < activeIndex
                    ? 'border-pass-border bg-pass-bg'
                    : i === activeIndex
                      ? 'border-teal-300 bg-teal-50'
                      : 'border-line bg-soft'
                }`}
              >
                <p className="text-[12px] font-semibold text-ink-2">
                  {i + 1}. {s.label}
                  {i < activeIndex ? <span className="ml-1 text-pass">✓</span> : null}
                </p>
                <p className="mt-0.5 text-[10.5px] leading-snug text-mut">{s.detail}</p>
              </div>
            ))}
          </div>

          <div className="-mx-1 mt-3 overflow-x-auto px-1 pb-1 scrollbar-thin">
            <div
              className="grid min-w-[880px] gap-1"
              style={{ gridTemplateColumns: '54px repeat(7, minmax(0, 1fr))' }}
            >
              <div />
              {Array.from({ length: 7 }).map((_, i) => (
                <Skeleton key={`h-${i}`} className="h-4" />
              ))}
              {Array.from({ length: 2 }).map((_, row) => (
                <RowSkeleton key={row} />
              ))}
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<Loader2 size={15} className="animate-spin" />} title="规则校验" />
        <CardBody>
          <div className="grid gap-x-5 gap-y-2 md:grid-cols-2">
            {Array.from({ length: 9 }).map((_, i) => (
              <div key={i} className="flex items-center gap-2">
                <Skeleton className="h-[18px] w-9" />
                <Skeleton className="h-[12px] flex-1" />
              </div>
            ))}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

function RowSkeleton() {
  return (
    <>
      <Skeleton className="h-[62px] rounded-lg" />
      {Array.from({ length: 7 }).map((_, i) => (
        <div key={i} className="rounded-lg border border-line bg-white p-1.5">
          <div className="mb-1.5 flex justify-end">
            <Skeleton className="h-[13px] w-7" />
          </div>
          <div className="flex flex-wrap gap-1">
            {Array.from({ length: i > 4 ? 6 : 5 }).map((__, j) => (
              <SkeletonChip key={j} />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
