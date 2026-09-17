import { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  CircleAlert,
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
  onApplySuggestion,
}: {
  meta: Meta;
  validation: Validation | null;
  softMetrics: SoftMetrics | null;
  validating: boolean;
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
        title={`规则校验 · ${passedCount}/${total} 通过`}
        subtitle="校验器独立于求解器，任何来源的排班都过同一套硬规则"
        right={
          validating ? (
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
            <RuleRow key={r.id} rule={r} onApplySuggestion={onApplySuggestion} />
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
          />
          <MetricBar
            label="工时均衡度"
            value={softMetrics?.balance_score ?? 0}
            display={softMetrics ? softMetrics.balance_score.toFixed(2) : '—'}
            hint="0–1，越高说明人均班次越平均"
          />
          <MetricBar
            label="技能冗余度"
            value={Math.min(1, (softMetrics?.skill_redundancy ?? 0) / 2)}
            display={softMetrics ? `${softMetrics.skill_redundancy.toFixed(1)}×` : '—'}
            hint="关键技能相对最低要求的倍数，越高越抗突发请假"
          />
        </div>
        <p className="mt-2.5 text-[11px] text-mut-2">
          软约束不阻塞生成，仅用于方案排序与向店长解释权衡。
        </p>
      </CardBody>
    </Card>
  );
}

function RuleRow({
  rule,
  onApplySuggestion,
}: {
  rule: RuleResult;
  onApplySuggestion: (s: Suggestion) => void;
}) {
  const [open, setOpen] = useState(false);
  const failed = !rule.passed;

  return (
    <div className={cn('border-b border-dashed border-line-2 py-1', failed && 'border-solid border-fail-border')}>
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
            failed ? 'bg-fail-bg text-fail' : 'bg-teal-50 text-teal-800',
          )}
        >
          {rule.id}
        </code>
        <span className={cn('min-w-0 flex-1 text-[12px] leading-snug', failed ? 'text-fail-deep' : 'text-ink-2')}>
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
            className={cn('mt-[3px] flex-none text-fail transition-transform duration-150', open && 'rotate-180')}
          />
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
                  <b className="font-semibold">
                    周{v.day} {v.shift}
                  </b>
                  {v.employees.length ? `（${v.employees.join('、')}）` : ''}：{v.message}
                </span>
              </p>
              {v.suggestions.length ? (
                <div className="flex flex-wrap gap-1.5 pl-4">
                  {v.suggestions.map((s, j) => (
                    <button
                      key={`${s.label}-${j}`}
                      type="button"
                      onClick={() => onApplySuggestion(s)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-fail-border bg-white px-2 py-1 text-[11.5px] font-semibold text-fail-deep transition-colors duration-150 hover:border-fail hover:bg-fail-bg focus-ring"
                    >
                      <Wrench size={11} />
                      {s.label}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="pl-4 text-[11px] text-mut">后端未提供自动修复建议，请手工点击 chip 调整。</p>
              )}
            </div>
          ))}
          <p className="pl-4 text-[10.5px] text-mut">点击修复建议将直接改动本地排班并重新校验。</p>
        </div>
      ) : null}
    </div>
  );
}
