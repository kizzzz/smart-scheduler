import { useEffect, useRef, useState } from 'react';
import { Loader2, Sparkles, Wand2, Zap } from 'lucide-react';
import type { Scenario, SolvePhase } from '../types';
import { Card } from './ui/Card';
import { Button } from './ui/Button';
import { cn } from '../lib/utils';

export const PHASE_TEXT: Record<SolvePhase, string> = {
  parse: '解析指令中',
  solve: '求解排班中',
  validate: '校验规则中',
  explain: '生成解释中',
};

const PHASE_ORDER: SolvePhase[] = ['parse', 'solve', 'validate', 'explain'];

const PLACEHOLDER_FALLBACK = '例如：正常排班，尽量满足大家的班次偏好';

/**
 * 等待期只有阶段文案是不够的：默认模型 glm-4.5-flash 实测整体约 52 秒，
 * 没有秒数的话用户会以为页面卡死并反复点按钮。所以按秒回显「已等待 / 预计」。
 */
function useElapsed(active: boolean) {
  const [sec, setSec] = useState(0);
  const startRef = useRef(0);
  useEffect(() => {
    if (!active) {
      setSec(0);
      return;
    }
    startRef.current = Date.now();
    setSec(0);
    const id = window.setInterval(
      () => setSec(Math.round((Date.now() - startRef.current) / 1000)),
      1000,
    );
    return () => window.clearInterval(id);
  }, [active]);
  return sec;
}

export function InstructionPanel({
  instruction,
  onChange,
  onGenerate,
  loading,
  phase,
  scenarios,
  onScenario,
  hasSchedule,
  etaSeconds,
  blockedReason = null,
  onGoConfig,
  placeholderExample = null,
}: {
  instruction: string;
  onChange: (v: string) => void;
  onGenerate: () => void;
  loading: boolean;
  phase: SolvePhase;
  scenarios: Scenario[];
  onScenario: (s: Scenario) => void;
  hasSchedule: boolean;
  /** 所选模型的实测整体耗时，用于把等待时间说清楚而不是让用户干等 */
  etaSeconds: number;
  /**
   * 配置自检没过时的原因。有值就禁用生成——与其让用户等 60 秒换一句「无解」，
   * 不如现在就把他带去能改的地方（契约 2.3）。
   */
  blockedReason?: string | null;
  onGoConfig?: () => void;
  /** 由当前配置拼出的示例句；配置里没有足够素材时退回不含工号/日期的通用句 */
  placeholderExample?: string | null;
}) {
  const elapsed = useElapsed(loading);
  const eta = Math.round(etaSeconds);
  // 超过预计值就不再报预计数，改为「仍在等待」：继续报一个已经被打破的承诺只会更让人不安
  const overdue = elapsed > eta;
  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <label
            htmlFor="instruction"
            className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-teal-700"
          >
            <Wand2 size={12} />
            排班需求（自然语言）
          </label>
          <textarea
            id="instruction"
            value={instruction}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholderExample ? `例如：${placeholderExample}` : PLACEHOLDER_FALLBACK}
            rows={2}
            disabled={loading}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') onGenerate();
            }}
            className="w-full resize-none rounded-[10px] border-[1.5px] border-teal-200 bg-white px-3 py-2 text-[13.5px] leading-relaxed text-ink placeholder:text-mut-2 transition-colors duration-150 focus:border-teal-500 focus:outline-none disabled:bg-soft"
          />
          <p className="mt-1.5 text-[11px] text-mut-2">
            ⌘/Ctrl + Enter 直接生成 · 支持「请假」「偏好」「周末加人」等临时约束
          </p>
        </div>

        <div className="flex w-[212px] flex-none flex-col gap-2">
          <Button
            size="lg"
            variant="primary"
            onClick={onGenerate}
            disabled={loading || !instruction.trim() || Boolean(blockedReason)}
            title={blockedReason ?? undefined}
            className="w-full"
          >
            {loading ? (
              <>
                <Loader2 size={15} className="animate-spin" />
                {PHASE_TEXT[phase]}…
              </>
            ) : (
              <>
                <Sparkles size={15} />
                {hasSchedule ? '重新生成排班' : '生成排班'}
              </>
            )}
          </Button>

          {blockedReason && !loading ? (
            <p className="rounded-md border border-fail-border bg-fail-bg px-2 py-1 text-[10.5px] leading-relaxed text-fail-deep">
              {blockedReason}
              {onGoConfig ? (
                <button
                  type="button"
                  onClick={onGoConfig}
                  className="ml-1 font-semibold underline decoration-dotted underline-offset-2 hover:decoration-solid"
                >
                  去配置页修复
                </button>
              ) : null}
            </p>
          ) : null}

          {loading ? (
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1">
                {PHASE_ORDER.map((p) => (
                  <span
                    key={p}
                    className={cn(
                      'h-[3px] flex-1 rounded-full transition-colors duration-300',
                      PHASE_ORDER.indexOf(p) <= PHASE_ORDER.indexOf(phase)
                        ? 'bg-teal-600'
                        : 'bg-line',
                    )}
                  />
                ))}
              </div>
              <p className="text-center text-[10.5px] tabular-nums text-mut-2">
                已等待 {elapsed}s
                {overdue ? ' · 仍在等待模型返回' : ` · 该模型实测约 ${eta}s`}
              </p>
            </div>
          ) : (
            <div className="rounded-[10px] border border-line bg-soft p-2">
              <p className="mb-1.5 flex items-center gap-1 text-[10.5px] font-semibold text-mut">
                <Zap size={10} />
                预置场景
              </p>
              <div className="flex flex-col gap-1">
                {scenarios.length === 0 ? (
                  <span className="text-[11px] text-mut-2">场景加载中…</span>
                ) : (
                  scenarios.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      title={`${s.description}${s.base_required ? '（需已有一张排班表）' : ''}`}
                      onClick={() => onScenario(s)}
                      disabled={
                        loading || Boolean(blockedReason) || (s.base_required && !hasSchedule)
                      }
                      className="flex items-center justify-between gap-1 rounded-md border border-line bg-white px-2 py-[3px] text-left text-[11px] text-ink-2 transition-colors duration-150 hover:border-teal-300 hover:bg-teal-50 hover:text-teal-800 disabled:cursor-not-allowed disabled:text-mut-2 disabled:hover:border-line disabled:hover:bg-white focus-ring"
                    >
                      <span className="truncate">{s.title}</span>
                      {s.base_required ? (
                        <span className="flex-none text-[9.5px] text-mut-2">需基准表</span>
                      ) : null}
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
