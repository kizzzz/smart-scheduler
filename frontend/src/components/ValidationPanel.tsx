import { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Loader2,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import type { Meta, RuleResult, SoftMetrics, Suggestion, Validation } from '../types';
import { cn, pct } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Card, CardBody, CardHeader } from './ui/Card';
import { MetricBar } from './ui/MetricBar';

export function ValidationPanel({
  meta,
  validation,
  softMetrics,
  validating,
  pending = false,
  pendingHint,
  subtitle,
  suggestionsDisabled = false,
  suggestionsHint,
  onApplySuggestion,
}: {
  meta: Meta;
  validation: Validation | null;
  softMetrics: SoftMetrics | null;
  validating: boolean;
  /** 没有排班可校验（澄清态 / 无解态）：全部规则显示为灰色「待排班」，不显示红色违规 */
  pending?: boolean;
  pendingHint?: string;
  subtitle?: string;
  /** 导入确认态：结论要看得见，但修复建议要等应用为基线之后才能点，避免改到一份还没生效的表 */
  suggestionsDisabled?: boolean;
  suggestionsHint?: string;
  onApplySuggestion: (s: Suggestion) => void;
}) {
  const byId = new Map((validation?.rules ?? []).map((r) => [r.id, r]));
  const rows: RuleResult[] = meta.rules.map(
    (r) => byId.get(r.id) ?? { id: r.id, text: r.text, passed: true, violations: [] },
  );
  const total = rows.length;
  const passedCount = rows.filter((r) => r.passed).length;
  const violationCount = validation?.violation_count ?? 0;
  const allPassed = violationCount === 0 && passedCount === total;

  return (
    <Card>
      <CardHeader
        icon={<ShieldCheck size={15} />}
        title={pending ? `规则校验 · 待排班（共 ${total} 条硬规则）` : `规则校验 · ${passedCount}/${total} 通过`}
        subtitle={
          pending
            ? pendingHint ?? '本次没有产出排班，硬规则尚未参与校验'
            : subtitle ?? '校验器独立于求解器，任何来源的排班都过同一套硬规则'
        }
        right={
          pending ? (
            <Badge tone="neutral" icon={<Clock3 size={10} />}>
              待排班
            </Badge>
          ) : validating ? (
            <Badge tone="teal" icon={<Loader2 size={10} className="animate-spin" />}>
              校验中
            </Badge>
          ) : allPassed ? (
            <Badge tone="pass" icon={<CheckCircle2 size={10} />}>
              全部通过
            </Badge>
          ) : (
            <Badge tone="fail" icon={<CircleAlert size={10} />}>
              {violationCount} 条违规
            </Badge>
          )
        }
      />
      <CardBody>
        <div className="grid gap-x-5 gap-y-0.5 md:grid-cols-2">
          {rows.map((r) => (
            <RuleRow
              key={r.id}
              rule={r}
              pending={pending}
              suggestionsDisabled={suggestionsDisabled}
              suggestionsHint={suggestionsHint}
              onApplySuggestion={onApplySuggestion}
            />
          ))}
        </div>

        <p className="mt-3 border-t border-line-2 pt-2.5 text-[11.5px] font-semibold text-ink-2">
          软约束满足度
        </p>
        <div className="mt-2 grid gap-4 sm:grid-cols-3">
          <MetricBar
            label="偏好满足率"
            value={softMetrics?.preference_rate ?? 0}
            display={softMetrics ? pct(softMetrics.preference_rate) : '—'}
            hint="被排班次与员工班次偏好一致的比例"
            muted={pending}
          />
          <MetricBar
            label="工时均衡度"
            value={softMetrics?.balance_score ?? 0}
            display={softMetrics ? softMetrics.balance_score.toFixed(2) : '—'}
            hint="0–1，越高说明人均班次越平均"
            muted={pending}
          />
          <MetricBar
            label="技能冗余度"
            value={softMetrics?.skill_redundancy ?? 0}
            display={softMetrics ? pct(softMetrics.skill_redundancy) : '—'}
            hint="平均每班的技能冗余人数，按每班 8 人次冗余记满分"
            muted={pending}
          />
        </div>
        <p className="mt-2.5 text-[11px] text-mut-2">
          {pending
            ? '软约束数值随排班方案产出，当前无方案可计算。'
            : '软约束不阻塞生成，仅用于方案排序与向店长解释权衡。'}
        </p>
      </CardBody>
    </Card>
  );
}

/** 违规定位：周工时这类跨班次规则后端不给 day/shift，此时不能渲染成「周 」 */
function violationScope(day: string, shift: string): string {
  if (day && shift) return `周${day} ${shift}`;
  if (day) return `周${day} 全天`;
  return '本周整体';
}

function RuleRow({
  rule,
  pending,
  suggestionsDisabled,
  suggestionsHint,
  onApplySuggestion,
}: {
  rule: RuleResult;
  pending: boolean;
  suggestionsDisabled: boolean;
  suggestionsHint?: string;
  onApplySuggestion: (s: Suggestion) => void;
}) {
  // 导入确认态默认把违规明细展开：体检结论是导入功能的主角，不该让用户再点一次才看见
  const [open, setOpen] = useState(suggestionsDisabled);
  const failed = !pending && !rule.passed;

  return (
    <div
      className={cn(
        'border-b border-dashed border-line-2 py-1',
        failed && 'border-solid border-fail-border',
      )}
    >
      <button
        type="button"
        onClick={() => failed && setOpen((v) => !v)}
        className={cn(
          'flex w-full items-start gap-2 text-left transition-colors duration-150',
          failed ? 'cursor-pointer' : 'cursor-default',
        )}
      >
        <code
          className={cn(
            'flex-none rounded px-1 font-mono text-[10.5px] font-semibold leading-[18px]',
            failed
              ? 'bg-fail-bg text-fail'
              : pending
                ? 'bg-[#F1F5F4] text-mut'
                : 'bg-teal-50 text-teal-800',
          )}
        >
          {rule.id}
        </code>
        <span
          className={cn(
            'min-w-0 flex-1 text-[12px] leading-snug',
            failed ? 'text-fail-deep' : pending ? 'text-mut' : 'text-ink-2',
          )}
        >
          {rule.text}
          {failed ? (
            <span className="ml-1.5 rounded bg-fail-bg px-1 text-[10px] font-semibold text-fail">
              {rule.violations.length} 处
            </span>
          ) : null}
        </span>
        {failed ? (
          <ChevronDown
            size={13}
            className={cn(
              'mt-[3px] flex-none text-fail transition-transform duration-150',
              open && 'rotate-180',
            )}
          />
        ) : pending ? (
          <span className="mt-[2px] flex-none text-[10px] font-semibold text-mut-2">待排班</span>
        ) : (
          <CheckCircle2 size={13} className="mt-[3px] flex-none text-pass" />
        )}
      </button>

      {failed && open ? (
        <div className="mb-1.5 mt-1.5 space-y-2 rounded-lg border border-fail-border bg-fail-bg/70 px-2.5 py-2">
          {rule.violations.map((v, i) => (
            <div key={`${v.day}-${v.shift}-${i}`} className="space-y-1.5">
              <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-fail-deep">
                <CircleAlert size={12} className="mt-[2px] flex-none text-fail" />
                <span>
                  <b className="font-semibold">{violationScope(v.day, v.shift)}</b>
                  {v.employees.length ? `（${v.employees.join('、')}）` : ''}：{v.message}
                </span>
              </p>
              {v.suggestions.length ? (
                <div className="flex flex-wrap gap-1.5 pl-4">
                  {v.suggestions.map((s, j) => (
                    <button
                      key={`${s.label}-${j}`}
                      type="button"
                      disabled={suggestionsDisabled}
                      onClick={() => onApplySuggestion(s)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-fail-border bg-white px-2 py-1 text-[11.5px] font-semibold text-fail-deep transition-colors duration-150 hover:border-fail hover:bg-fail-bg disabled:cursor-not-allowed disabled:border-line disabled:bg-soft disabled:text-mut focus-ring"
                    >
                      <Wrench size={11} />
                      {s.label}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="pl-4 text-[11px] text-mut">
                  后端未提供自动修复建议，请手工点击 chip 调整。
                </p>
              )}
            </div>
          ))}
          <p className="pl-4 text-[10.5px] text-mut">
            {suggestionsDisabled
              ? suggestionsHint ?? '修复建议在这张表成为基线后可用。'
              : '点击修复建议将直接改动本地排班并重新校验。'}
          </p>
        </div>
      ) : null}
    </div>
  );
}
