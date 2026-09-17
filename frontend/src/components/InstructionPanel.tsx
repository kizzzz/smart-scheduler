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

const PLACEHOLDER = '例如：下周正常排班，E05 周六请假，尽量满足大家的班次偏好';

export function InstructionPanel({
  instruction,
  onChange,
  onGenerate,
  loading,
  phase,
  scenarios,
  onScenario,
  hasSchedule,
}: {
  instruction: string;
  onChange: (v: string) => void;
  onGenerate: () => void;
  loading: boolean;
  phase: SolvePhase;
  scenarios: Scenario[];
  onScenario: (s: Scenario) => void;
  hasSchedule: boolean;
}) {
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
            placeholder={PLACEHOLDER}
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
            disabled={loading || !instruction.trim()}
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

          {loading ? (
            <div className="flex items-center gap-1">
              {PHASE_ORDER.map((p) => (
                <span
                  key={p}
                  className={cn(
                    'h-[3px] flex-1 rounded-full transition-colors duration-300',
                    PHASE_ORDER.indexOf(p) <= PHASE_ORDER.indexOf(phase) ? 'bg-teal-600' : 'bg-line',
                  )}
                />
              ))}
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
                      disabled={loading || (s.base_required && !hasSchedule)}
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
