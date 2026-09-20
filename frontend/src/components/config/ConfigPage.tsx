import { useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CalendarRange,
  CheckCheck,
  Download,
  RotateCcw,
  Settings2,
  Shield,
  Sparkles,
  Upload,
  Users,
} from 'lucide-react';
import type { ConfigStateApi } from '../../config/useConfigState';
import { issuesByTarget, type FormScope } from '../../config/checks';
import { activeEmployees } from '../../config/derive';
import { cn } from '../../lib/utils';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card, CardBody, CardHeader } from '../ui/Card';
import { Dialog } from '../ui/Dialog';
import { Skeleton } from '../ui/Skeleton';
import { CapacityMatrix } from './CapacityMatrix';
import { CheckPanel } from './CheckPanel';
import { StepEmployees } from './StepEmployees';
import { StepRules } from './StepRules';
import { StepScenario } from './StepScenario';

/**
 * 配置页（三步向导）。
 *
 * 几个刻意的产品决定：
 * - **三步可自由跳转**，不是必须按顺序走完的 wizard。用户常常只是回来改一个人的请假，
 *   强制他重新经过「场景」一步纯属浪费。
 * - **自动保存**，没有「保存」按钮。配置有几十个输入框，「填了半天忘保存」的代价是重填；
 *   自动保存的代价只是多写几次 localStorage。
 * - **自检常驻右侧**，改一个数字就能立刻看到供需变化 —— 这是把「等 60 秒才知道无解」
 *   提前到配置阶段的关键。
 */

const STEPS: Array<{ key: FormScope; title: string; hint: string; icon: typeof CalendarRange }> = [
  { key: 'scenario', title: '排班场景', hint: '排几天、每天几个班', icon: CalendarRange },
  { key: 'employees', title: '员工', hint: '名单、技能、不可排班时段', icon: Users },
  { key: 'rules', title: '规则', hint: '硬约束与参数', icon: Shield },
];

export function ConfigPage({
  state,
  step,
  onStepChange,
  onGotoGenerate,
}: {
  state: ConfigStateApi;
  /**
   * 当前步骤由外部持有：生成页那条「生成被拦下」的横幅要能把用户直接送到对应步骤，
   * 步骤状态藏在这个组件里的话，跳过来只会停在「排班场景」，用户还得自己找。
   */
  step: FormScope;
  onStepChange: (scope: FormScope) => void;
  /** 回到生成页。blocked 为真时仍允许返回（配置可跳过），但生成会被拦住 */
  onGotoGenerate: () => void;
}) {
  const setStep = onStepChange;
  const [confirmReset, setConfirmReset] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const { config, formIssues } = state;
  const formErrors = state.formErrors;
  const errorMap = useMemo(() => issuesByTarget(formIssues), [formIssues]);
  const errorOf = (target: string) => errorMap.get(target)?.[0] ?? null;
  // 步骤角标只数硬错误：把「等于不限制」这类提醒也标红，会让用户以为自己卡住了
  const issueCount = (scope: FormScope) => formErrors.filter((i) => i.scope === scope).length;
  const warnCount = (scope: FormScope) =>
    formIssues.filter((i) => i.scope === scope && i.level === 'warn').length;

  if (!config) {
    return (
      <Card className="space-y-2 p-4">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-[220px]" />
        <p className="text-[11.5px] text-mut">
          {state.loadFailure
            ? `默认配置加载失败（${state.loadFailure.message}），正在回退到本地示例配置…`
            : '正在加载默认配置（GET /api/config/default）…'}
        </p>
      </Card>
    );
  }

  const stepIndex = STEPS.findIndex((s) => s.key === step);
  const activeCount = activeEmployees(config).length;
  const liveRules = config.rules.filter((r) => r.enabled || r.locked).length;

  const handleImport = async (file: File) => {
    const err = await state.importFromFile(file);
    setNotice(
      err
        ? { tone: 'bad', text: `导入失败：${err}` }
        : { tone: 'ok', text: `已导入 ${file.name}，并保存为你的本地配置` },
    );
  };

  return (
    <div className="space-y-3">
      <Card className="overflow-hidden">
        <CardHeader
          icon={<Settings2 size={15} />}
          title="配置你的门店"
          subtitle="改动自动保存在这台浏览器里，每次生成排班时随请求带给后端"
          right={
            <>
              <span className="hidden text-[10.5px] leading-tight text-mut-2 sm:block">
                {state.isSample ? (
                  '当前是示例门店配置'
                ) : state.savedAt ? (
                  <>已保存 · {new Date(state.savedAt).toLocaleTimeString('zh-CN', { hour12: false })}</>
                ) : (
                  '改动会自动保存'
                )}
              </span>
              <input
                ref={fileRef}
                type="file"
                accept=".json,application/json"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleImport(f);
                  e.target.value = '';
                }}
              />
              <Button size="sm" variant="secondary" onClick={() => fileRef.current?.click()}>
                <Upload size={12} />
                导入 JSON
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  const name = state.exportToFile();
                  setNotice(name ? { tone: 'ok', text: `已导出 ${name}` } : null);
                }}
              >
                <Download size={12} />
                导出 JSON
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmReset(true)}>
                <RotateCcw size={12} />
                恢复示例配置
              </Button>
            </>
          }
        />
        <CardBody className="pb-3">
          <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
            <Badge tone="teal" icon={<CalendarRange size={10} />}>
              {config.scenario.days.length} 天 × {config.scenario.shifts.length} 班
            </Badge>
            <Badge tone="neutral" icon={<Users size={10} />}>
              启用 {activeCount} / {config.employees.length} 人
            </Badge>
            <Badge tone="neutral" icon={<Shield size={10} />}>
              生效规则 {liveRules} 条
            </Badge>
            <span className="text-[10.5px] text-mut-2">
              配置存在浏览器本地（localStorage），换设备或清缓存会丢，建议用「导出 JSON」备份
            </span>
          </div>

          <ol className="grid gap-1.5 sm:grid-cols-3">
            {STEPS.map((s, i) => {
              const on = s.key === step;
              const bad = issueCount(s.key);
              const soft = warnCount(s.key);
              const Icon = s.icon;
              return (
                <li key={s.key}>
                  <button
                    type="button"
                    onClick={() => setStep(s.key)}
                    aria-current={on ? 'step' : undefined}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors duration-150 focus-ring',
                      on
                        ? 'border-teal-500 bg-teal-50/80 shadow-card'
                        : 'border-line bg-white hover:border-teal-200 hover:bg-teal-50/40',
                    )}
                  >
                    <span
                      className={cn(
                        'flex h-6 w-6 flex-none items-center justify-center rounded-full text-[11px] font-semibold',
                        on ? 'bg-teal-700 text-white' : 'bg-[#F1F5F4] text-mut',
                      )}
                    >
                      {i + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <b
                        className={cn(
                          'flex items-center gap-1 text-[12.5px] font-semibold',
                          on ? 'text-teal-900' : 'text-ink',
                        )}
                      >
                        <Icon size={12} />
                        {s.title}
                        {bad > 0 ? (
                          <span className="rounded-full bg-fail-bg px-1 text-[10px] font-semibold text-fail">
                            {bad}
                          </span>
                        ) : soft > 0 ? (
                          <span className="rounded-full bg-pend-bg px-1 text-[10px] font-semibold text-pend-deep">
                            {soft}
                          </span>
                        ) : null}
                      </b>
                      <span className="mt-0.5 block truncate text-[10.5px] text-mut">{s.hint}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>

          {notice ? (
            <p
              className={cn(
                'mt-2 rounded-lg border px-2.5 py-1.5 text-[11.5px]',
                notice.tone === 'ok'
                  ? 'border-pass-border bg-pass-bg text-pass-deep'
                  : 'border-fail-border bg-fail-bg text-fail-deep',
              )}
            >
              {notice.text}
            </p>
          ) : null}
        </CardBody>
      </Card>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_304px]">
        <div className="min-w-0 space-y-3">
          <Card>
            <CardBody className="pt-3.5">
              {step === 'scenario' ? (
                <StepScenario config={config} errorOf={errorOf} onUpdate={state.update} />
              ) : step === 'employees' ? (
                <StepEmployees config={config} errorOf={errorOf} onUpdate={state.update} />
              ) : (
                <StepRules config={config} errorOf={errorOf} onUpdate={state.update} />
              )}
            </CardBody>
          </Card>

          {state.check?.capacity ? (
            <Card>
              <CardHeader
                icon={<CheckCheck size={15} />}
                title="供给 vs 需求"
                subtitle="每格「能来几个人」对比「至少要几个人」，瓶颈一眼可见"
                right={
                  <Badge
                    tone={state.check.capacity.headroom_pct < 10 ? 'pend' : 'teal'}
                  >
                    冗余 {state.check.capacity.headroom_pct}%
                  </Badge>
                }
              />
              <CardBody>
                <CapacityMatrix config={config} capacity={state.check.capacity} />
              </CardBody>
            </Card>
          ) : null}

          <div className="flex flex-wrap items-center gap-2 rounded-card border border-line bg-white px-3 py-2.5 shadow-card">
            <Button
              size="sm"
              variant="secondary"
              disabled={stepIndex <= 0}
              onClick={() => setStep(STEPS[Math.max(0, stepIndex - 1)].key)}
            >
              <ArrowLeft size={12} />
              上一步
            </Button>
            {stepIndex < STEPS.length - 1 ? (
              // 最后一步不渲染而不是置灰：置灰按钮上写着「下一步：规则」而当前就在规则页，等于自相矛盾
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setStep(STEPS[stepIndex + 1].key)}
              >
                下一步：{STEPS[stepIndex + 1].title}
                <ArrowRight size={12} />
              </Button>
            ) : null}

            <span className="ml-auto flex flex-wrap items-center gap-2">
              {state.blocked ? (
                <span className="text-[11px] text-fail">
                  还有 {(state.check?.errors.length ?? 0) + formErrors.length} 项必须修，修完才能生成
                </span>
              ) : null}
              <Button size="sm" variant="ghost" onClick={onGotoGenerate}>
                先回生成页
              </Button>
              <Button
                size="md"
                variant="primary"
                disabled={state.blocked}
                title={state.blocked ? '配置自检还有必须修复的问题' : '用这份配置去生成排班'}
                onClick={() => {
                  state.flushSave();
                  onGotoGenerate();
                }}
              >
                <Sparkles size={14} />
                配置好了，去生成排班
              </Button>
            </span>
          </div>
        </div>

        <div className="min-w-0 lg:sticky lg:top-[68px] lg:self-start">
          <CheckPanel
            config={config}
            check={state.check}
            checking={state.checking}
            checkFailure={state.checkFailure}
            formIssues={formIssues}
            onJump={setStep}
            onRevalidate={state.revalidate}
          />
        </div>
      </div>

      <Dialog
        open={confirmReset}
        onClose={() => setConfirmReset(false)}
        title="恢复成示例门店配置？"
        subtitle="你在这台浏览器里保存的配置会被清掉，无法撤销。如果想留个底，先点「导出 JSON」。"
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setConfirmReset(false)}>
              取消
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                const name = state.exportToFile();
                setNotice(name ? { tone: 'ok', text: `已导出 ${name}，可以放心恢复了` } : null);
              }}
            >
              <Download size={12} />
              先导出备份
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                state.resetToSample();
                setConfirmReset(false);
                setStep('scenario');
                setNotice({ tone: 'ok', text: '已恢复为示例门店配置（7 天 × 2 班、20 人）' });
              }}
            >
              <RotateCcw size={12} />
              确认恢复
            </Button>
          </>
        }
      >
        <p className="text-[12px] leading-relaxed text-ink-2">
          示例配置就是原来写死在后端的那份数据：7 天 × 2 班、20 名员工、R-01～R-10 十条规则。
          它可以直接生成排班，适合用来对照着改。
        </p>
      </Dialog>
    </div>
  );
}
