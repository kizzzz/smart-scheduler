import { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Loader2,
  RefreshCw,
  Stethoscope,
  TriangleAlert,
  Wrench,
} from 'lucide-react';
import type { ConfigIssue, ConfigValidateResponse, ApiFailure, SchedulerConfig } from '../../types';
import { whereEmployee, whereRule } from '../../types';
import type { FormIssue, FormScope } from '../../config/checks';
import { targetLabel } from '../../config/checks';
import { SCOPE_LABEL, scopeOfIssue } from '../../config/issueScope';
import { cn } from '../../lib/utils';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card, CardBody, CardHeader } from '../ui/Card';

/**
 * 配置自检面板。
 *
 * 分两层信息，对应两种不同的「错」：
 * - **填写问题**（本地即时校验）：空值、重复工号、格式不对。打字时就要能看到。
 * - **排不出来**（`/api/config/validate`）：供需算不过来。这一层必须与求解器同源，
 *   所以由后端给结论，前端只负责把 message / fix 说清楚。
 *
 * 界面上刻意不展示 `code`：后端随时会加新 code，穷举渲染必然过期；code 只用于图标、定位与埋点。
 */

const SCOPE_ORDER: FormScope[] = ['scenario', 'employees', 'rules'];

export function CheckPanel({
  config,
  check,
  checking,
  checkFailure,
  formIssues,
  onJump,
  onRevalidate,
}: {
  config: SchedulerConfig;
  check: ConfigValidateResponse | null;
  checking: boolean;
  checkFailure: ApiFailure | null;
  formIssues: FormIssue[];
  onJump: (scope: FormScope) => void;
  onRevalidate: () => void;
}) {
  const errors = check?.errors ?? [];
  const warnings = check?.warnings ?? [];
  const capacity = check?.capacity ?? null;
  const formErrors = formIssues.filter((i) => i.level === 'error');
  const formWarns = formIssues.filter((i) => i.level === 'warn');
  const mustFix = errors.length + formErrors.length;
  const adviceCount = warnings.length + formWarns.length;
  const clean = mustFix === 0 && adviceCount === 0 && check !== null;

  const whereLabel = (issue: ConfigIssue): string | null => {
    const w = issue.where;
    if (!w) return null;
    const parts: string[] = [];
    if (w.day) parts.push(config.scenario.days.find((d) => d.id === w.day)?.label ?? w.day);
    if (w.shift) parts.push(config.scenario.shifts.find((s) => s.id === w.shift)?.name ?? w.shift);
    const emp = whereEmployee(w);
    if (emp) parts.push(emp);
    const rule = whereRule(w);
    if (rule) parts.push(rule);
    return parts.length ? parts.join(' · ') : null;
  };

  return (
    <Card className="overflow-hidden">
      <CardHeader
        icon={<Stethoscope size={15} />}
        title="配置自检"
        subtitle="改一个字就重跑一次，不用等到点生成才知道排不出来"
        right={
          checking ? (
            <Badge tone="teal" icon={<Loader2 size={10} className="animate-spin" />}>
              自检中
            </Badge>
          ) : mustFix > 0 ? (
            <Badge tone="fail" icon={<CircleAlert size={10} />}>
              {mustFix} 项必须修
            </Badge>
          ) : clean ? (
            <Badge tone="pass" icon={<CheckCircle2 size={10} />}>
              可以生成
            </Badge>
          ) : (
            <Badge tone="pend" icon={<TriangleAlert size={10} />}>
              {adviceCount} 条提醒
            </Badge>
          )
        }
      />
      <CardBody className="space-y-2.5">
        {checkFailure ? (
          <div className="rounded-lg border border-pend-border bg-pend-bg px-2.5 py-2">
            <p className="text-[11.5px] font-semibold text-pend-deep">
              自检接口没响应，无法判断这份配置能不能排出来
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-mut">
              {checkFailure.message}
              {checkFailure.status ? `（HTTP ${checkFailure.status}）` : ''}
            </p>
            <Button size="sm" variant="secondary" className="mt-1.5" onClick={onRevalidate}>
              <RefreshCw size={11} />
              重新自检
            </Button>
          </div>
        ) : null}

        {formErrors.length > 0 ? (
          <section className="rounded-lg border border-fail-border bg-fail-bg/70 px-2.5 py-2">
            <p className="text-[11.5px] font-semibold text-fail-deep">
              有 {formErrors.length} 处填写问题需要修正
            </p>
            <FormIssueList config={config} issues={formErrors} tone="fail" onJump={onJump} />
          </section>
        ) : null}

        {errors.length > 0 ? (
          <section className="space-y-1.5">
            <p className="text-[11px] font-semibold text-fail">
              这样配一定排不出来（{errors.length}）
            </p>
            {errors.map((issue, i) => (
              <IssueRow
                key={`${issue.code}-${i}`}
                issue={issue}
                tone="fail"
                where={whereLabel(issue)}
                onJump={onJump}
              />
            ))}
          </section>
        ) : null}

        {formWarns.length > 0 ? (
          <section className="rounded-lg border border-pend-border bg-pend-bg/60 px-2.5 py-2">
            <p className="text-[11.5px] font-semibold text-pend-deep">
              有 {formWarns.length} 处填法值得确认，但不影响生成
            </p>
            <FormIssueList config={config} issues={formWarns} tone="pend" onJump={onJump} />
          </section>
        ) : null}

        {warnings.length > 0 ? (
          <WarningList warnings={warnings} whereLabel={whereLabel} onJump={onJump} />
        ) : null}

        {clean ? (
          <p className="flex items-start gap-1.5 rounded-lg border border-pass-border bg-pass-bg px-2.5 py-2 text-[11.5px] leading-relaxed text-pass-deep">
            <CheckCircle2 size={13} className="mt-[1px] flex-none text-pass" />
            供需算得过来，也没有需要提醒的地方。可以去生成排班了。
          </p>
        ) : null}

        {capacity ? (
          <section className="rounded-lg border border-line bg-soft px-2.5 py-2">
            <div className="flex items-baseline justify-between text-[11px]">
              <span className="text-mut">需求人次</span>
              <b className="font-semibold tabular-nums text-ink">{capacity.demand_person_shifts}</b>
            </div>
            <div className="mt-0.5 flex items-baseline justify-between text-[11px]">
              <span className="text-mut">可供人次</span>
              <b className="font-semibold tabular-nums text-ink">{capacity.supply_person_shifts}</b>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line-2">
              <span
                className={cn(
                  'block h-full rounded-full transition-all duration-300',
                  capacity.headroom_pct < 0
                    ? 'bg-fail'
                    : capacity.headroom_pct < 10
                      ? 'bg-pend'
                      : 'bg-teal-600',
                )}
                style={{
                  width: `${Math.max(2, Math.min(100, ratio(capacity.demand_person_shifts, capacity.supply_person_shifts)))}%`,
                }}
              />
            </div>
            <p className="mt-1 text-[10.5px] leading-relaxed text-mut">
              冗余 {capacity.headroom_pct}%
              {capacity.headroom_pct < 10
                ? ' · 太紧了，有人请假就会无解'
                : ' · 有余量应对临时请假'}
            </p>
          </section>
        ) : null}
      </CardBody>
    </Card>
  );
}

function ratio(demand: number, supply: number): number {
  if (supply <= 0) return 100;
  return Math.round((demand / supply) * 100);
}

function groupByScope(issues: FormIssue[]): Array<[FormScope, FormIssue[]]> {
  return SCOPE_ORDER.map(
    (scope) => [scope, issues.filter((i) => i.scope === scope)] as [FormScope, FormIssue[]],
  ).filter(([, list]) => list.length > 0);
}

/**
 * 填写问题的汇总列表。
 *
 * 每条都带上「在哪」（`targetLabel`）而不是只显示第一条 message：这批问题的典型来源是
 * **导入别人给的 JSON**——用户没填过任何一个框，只看到「应为 ≥1 的整数」根本不知道
 * 说的是哪条规则的哪个参数。同一步骤里超过 3 条才折叠，因为这类错误往往是同一个
 * 手改动作批量造成的，列全反而盖住其他步骤。
 */
function FormIssueList({
  config,
  issues,
  tone,
  onJump,
}: {
  config: SchedulerConfig;
  issues: FormIssue[];
  tone: 'fail' | 'pend';
  onJump: (scope: FormScope) => void;
}) {
  const color = tone === 'fail' ? 'text-fail-deep' : 'text-pend-deep';
  return (
    <ul className="mt-1 space-y-1.5">
      {groupByScope(issues).map(([scope, list]) => (
        <li key={scope} className={cn('text-[11px] leading-relaxed', color)}>
          <button
            type="button"
            onClick={() => onJump(scope)}
            className="font-semibold underline decoration-dotted underline-offset-2 hover:decoration-solid"
          >
            {SCOPE_LABEL[scope]}（{list.length}）
          </button>
          <ul className="mt-0.5 space-y-0.5 pl-3">
            {list.slice(0, 3).map((issue, i) => {
              const at = targetLabel(config, issue.target);
              return (
                <li key={`${issue.target}-${i}`} className="list-disc">
                  {at ? <b className="font-semibold">{at}</b> : null}
                  {at ? '：' : null}
                  {issue.message}
                </li>
              );
            })}
            {list.length > 3 ? (
              <li className="list-none text-mut">另有 {list.length - 3} 处，进去后会就地标红</li>
            ) : null}
          </ul>
        </li>
      ))}
    </ul>
  );
}

function IssueRow({
  issue,
  tone,
  where,
  onJump,
}: {
  issue: ConfigIssue;
  tone: 'fail' | 'pend';
  where: string | null;
  onJump: (scope: FormScope) => void;
}) {
  const scope = scopeOfIssue(issue);
  return (
    <div
      className={cn(
        'rounded-lg border px-2.5 py-1.5',
        tone === 'fail' ? 'border-fail-border bg-fail-bg/60' : 'border-pend-border bg-pend-bg/70',
      )}
    >
      <p
        className={cn(
          'flex items-start gap-1.5 text-[11.5px] leading-relaxed',
          tone === 'fail' ? 'text-fail-deep' : 'text-pend-deep',
        )}
      >
        {tone === 'fail' ? (
          <CircleAlert size={12} className="mt-[3px] flex-none text-fail" />
        ) : (
          <TriangleAlert size={12} className="mt-[3px] flex-none text-pend" />
        )}
        <span className="min-w-0">
          {where ? <b className="font-semibold">{where}：</b> : null}
          {issue.message}
        </span>
      </p>
      {issue.fix ? (
        <p className="mt-0.5 flex items-start gap-1.5 pl-[18px] text-[10.5px] leading-relaxed text-mut">
          <Wrench size={10} className="mt-[3px] flex-none" />
          {issue.fix}
        </p>
      ) : null}
      {scope ? (
        <button
          type="button"
          onClick={() => onJump(scope)}
          className="ml-[18px] mt-0.5 text-[10.5px] font-semibold text-teal-700 underline decoration-dotted underline-offset-2 hover:decoration-solid"
        >
          去「{SCOPE_LABEL[scope]}」处理
        </button>
      ) : null}
    </div>
  );
}

/** 提醒默认收起前两条以外的部分：warning 不阻塞生成，不该和 error 抢注意力 */
function WarningList({
  warnings,
  whereLabel,
  onJump,
}: {
  warnings: ConfigIssue[];
  whereLabel: (issue: ConfigIssue) => string | null;
  onJump: (scope: FormScope) => void;
}) {
  const [open, setOpen] = useState(false);
  const shown = open ? warnings : warnings.slice(0, 2);
  return (
    <section className="space-y-1.5">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold text-pend-deep">
        值得注意，但不阻止生成（{warnings.length}）
        {warnings.length > 2 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="ml-auto inline-flex items-center gap-0.5 text-[10.5px] font-normal text-mut hover:text-ink-2"
          >
            {open ? '收起' : `展开全部 ${warnings.length} 条`}
            <ChevronDown size={11} className={cn('transition-transform duration-150', open && 'rotate-180')} />
          </button>
        ) : null}
      </p>
      {shown.map((issue, i) => (
        <IssueRow
          key={`${issue.code}-${i}`}
          issue={issue}
          tone="pend"
          where={whereLabel(issue)}
          onJump={onJump}
        />
      ))}
    </section>
  );
}
