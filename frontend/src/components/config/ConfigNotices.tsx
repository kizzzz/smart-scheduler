import { CircleAlert, FlaskConical, RefreshCw, Settings2, TriangleAlert } from 'lucide-react';
import type { ConfigValidateResponse, SchedulerConfig } from '../../types';
import type { FormScope } from '../../config/checks';
import { SCOPE_LABEL, scopeOfIssue } from '../../config/issueScope';
import { Button } from '../ui/Button';

/**
 * 生成页上与配置相关的三条横幅。
 *
 * 它们对应契约第 2 节的三个设计决定：配置可跳过（示例提示）、改配置不清空已有排班
 * （旧配置提示）、生成前先自检（拦截提示）。所以措辞的重点都在「现在发生了什么、
 * 你可以做什么」，而不是报错。
 */

export function SampleConfigNotice({
  config,
  onGoConfig,
}: {
  config: SchedulerConfig;
  onGoConfig: () => void;
}) {
  const { days, shifts } = config.scenario;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-card border border-teal-200 bg-teal-50/70 px-3 py-2 shadow-card">
      <FlaskConical size={14} className="flex-none text-teal-700" />
      <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-teal-900">
        <b className="font-semibold">当前使用示例门店配置</b>
        <span className="text-teal-800">
          （{days.length} 天 × {shifts.length} 班、{config.employees.length} 名员工、
          {config.rules.length} 条规则）
        </span>
        ，可以直接生成排班先看效果；换成自己门店的员工与规则只需几分钟。
      </p>
      <Button size="sm" variant="outline" onClick={onGoConfig}>
        <Settings2 size={12} />
        去配置我的门店
      </Button>
    </div>
  );
}

/**
 * 「排班基于旧配置」。
 *
 * 关键是**不清空已有排班**：排班是有成本的决策产物，不能因为改了个人名就被静默作废。
 * 所以这里只标记差异，重排时机交给用户。
 */
export function StaleConfigNotice({
  onRegenerate,
  onGoConfig,
  disabled,
}: {
  onRegenerate: () => void;
  onGoConfig: () => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-card border border-pend-border bg-pend-bg px-3 py-2 shadow-card">
      <TriangleAlert size={14} className="flex-none text-pend" />
      <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-pend-deep">
        <b className="font-semibold">配置已变更，当前排班基于旧配置。</b>
        已有排班没有被清空 —— 你可以继续微调它，也可以按新配置重排。
      </p>
      <Button size="sm" variant="ghost" onClick={onGoConfig}>
        看看改了什么
      </Button>
      <Button size="sm" variant="outline" disabled={disabled} onClick={onRegenerate}>
        <RefreshCw size={12} />
        按新配置重排
      </Button>
    </div>
  );
}

/**
 * 生成前被自检拦下来。
 *
 * 两个要点：
 * - 只说 message + fix，不展示 code。code 是给我们排查用的，用户看到 `daily_capacity_lt_demand`
 *   只会更慌。
 * - 每条都带「去『X』处理」，跳到与配置页自检面板**同一个**步骤（共用 `scopeOfIssue`）。
 *   一条错误在两个入口指向不同的地方，用户会以为是两个问题。
 *
 * 真实接口把 `POST /api/generate` 的配置自检失败也返回成同构的 400 body，所以这块渲染
 * 对「点生成才发现」和「配置页就发现」是同一套，不会退化成一句「请求失败」。
 */
export function ConfigBlockedNotice({
  check,
  formIssueCount,
  onGoConfig,
  onJump,
}: {
  check: ConfigValidateResponse | null;
  formIssueCount: number;
  onGoConfig: () => void;
  onJump?: (scope: FormScope) => void;
}) {
  const errors = check?.errors ?? [];
  const total = errors.length + formIssueCount;
  return (
    <div className="rounded-card border border-fail-border bg-fail-bg px-3 py-2.5 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <CircleAlert size={14} className="flex-none text-fail" />
        <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-fail-deep">
          <b className="font-semibold">配置自检发现 {total} 项问题，已拦下这次生成。</b>
          这几类问题一定排不出可行解，等 60 秒拿一句「无解」不如现在就改掉。
        </p>
        <Button size="sm" variant="danger" onClick={() => onGoConfig()}>
          <Settings2 size={12} />
          去修复配置
        </Button>
      </div>
      {errors.length ? (
        <ul className="mt-1.5 space-y-1 pl-[22px]">
          {errors.slice(0, 3).map((e, i) => {
            const scope = scopeOfIssue(e);
            return (
              <li key={`${e.code}-${i}`} className="text-[11.5px] leading-relaxed text-fail-deep">
                {e.message}
                {e.fix ? <span className="text-mut"> · {e.fix}</span> : null}
                {onJump && scope ? (
                  <button
                    type="button"
                    onClick={() => onJump(scope)}
                    className="ml-1 whitespace-nowrap font-semibold text-fail underline decoration-dotted underline-offset-2 hover:decoration-solid"
                  >
                    去「{SCOPE_LABEL[scope]}」处理
                  </button>
                ) : null}
              </li>
            );
          })}
          {errors.length > 3 ? (
            <li className="text-[11px] text-mut">还有 {errors.length - 3} 项，见配置页右侧自检面板</li>
          ) : null}
        </ul>
      ) : null}
      {formIssueCount > 0 ? (
        <p className="mt-1 pl-[22px] text-[11.5px] text-fail-deep">
          另有 {formIssueCount} 处填写问题（空值 / 重复工号 / 格式不对）待修正。
        </p>
      ) : null}
    </div>
  );
}
