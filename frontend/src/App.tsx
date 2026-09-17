import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  generate as apiGenerate,
  validate as apiValidate,
  getHealth,
  getMeta,
  getScenarios,
  isMockMode,
  mockCaseOverride,
  setMockCase,
} from './api';
import type {
  ApiFailure,
  GenerateResponse,
  Meta,
  Scenario,
  SoftMetrics,
  Slot,
  SolvePhase,
  Suggestion,
  Validation,
} from './types';
import { applySlotChange, buildCorrectedInstruction, buildIssueIndex } from './lib/schedule';
import { slotKey } from './lib/utils';
import { TopBar, type HealthState } from './components/TopBar';
import { MockToolbar } from './components/MockToolbar';
import { InstructionPanel } from './components/InstructionPanel';
import { IntentCard } from './components/IntentCard';
import { DiffBanner } from './components/DiffBanner';
import { ScheduleBoard } from './components/ScheduleBoard';
import { ValidationPanel } from './components/ValidationPanel';
import { ExplanationCard } from './components/ExplanationCard';
import { InfeasibleCard } from './components/InfeasibleCard';
import { ClarifyCard } from './components/ClarifyCard';
import { EmptyBoard } from './components/EmptyBoard';
import { BoardSkeleton } from './components/BoardSkeleton';
import { ErrorCard } from './components/ErrorCard';
import { SwapDialog, type SwapTarget } from './components/SwapDialog';
import { Skeleton } from './components/ui/Skeleton';

const PHASE_TIMELINE: Array<{ at: number; phase: SolvePhase }> = [
  { at: 0, phase: 'parse' },
  { at: 900, phase: 'solve' },
  { at: 1300, phase: 'validate' },
  { at: 1600, phase: 'explain' },
];

function toFailure(e: unknown): ApiFailure {
  if (e instanceof ApiError) return { message: e.message, status: e.status, detail: e.detail };
  if (e instanceof Error) return { message: e.message };
  return { message: String(e) };
}

export default function App() {
  const mock = isMockMode();
  const mockCase = mockCaseOverride();

  const [health, setHealth] = useState<HealthState>('checking');
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaFailure, setMetaFailure] = useState<ApiFailure | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);

  const [instruction, setInstruction] = useState('');
  const [loading, setLoading] = useState(false);
  const [phase, setPhase] = useState<SolvePhase>('parse');
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [metrics, setMetrics] = useState<SoftMetrics | null>(null);
  const [validating, setValidating] = useState(false);
  const [validateFailure, setValidateFailure] = useState<ApiFailure | null>(null);
  const [dirty, setDirty] = useState(false);

  const [flash, setFlash] = useState<Map<string, number>>(new Map());
  const [swapTarget, setSwapTarget] = useState<SwapTarget | null>(null);

  const rawInstructionRef = useRef('');
  const lastRequestRef = useRef<{ instruction: string; base: Slot[] | null; raw: string } | null>(null);
  const timersRef = useRef<number[]>([]);
  const flashSeq = useRef(0);

  /* ---------------- 启动加载 ---------------- */

  const checkHealth = useCallback(async () => {
    setHealth('checking');
    try {
      const r = await getHealth();
      setHealth(r.ok ? 'ok' : 'down');
    } catch {
      setHealth('down');
    }
  }, []);

  const loadMeta = useCallback(async () => {
    setMetaFailure(null);
    try {
      const m = await getMeta();
      setMeta(m);
    } catch (e) {
      setMetaFailure(toFailure(e));
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    void loadMeta();
    getScenarios()
      .then(setScenarios)
      .catch(() => setScenarios([]));
  }, [checkHealth, loadMeta]);

  useEffect(() => () => timersRef.current.forEach((t) => window.clearTimeout(t)), []);

  /* ---------------- 高亮闪烁 ---------------- */

  const triggerFlash = useCallback((keys: string[]) => {
    if (keys.length === 0) return;
    flashSeq.current += 1;
    const seq = flashSeq.current;
    setFlash((prev) => {
      const next = new Map(prev);
      keys.forEach((k) => next.set(k, seq));
      return next;
    });
    const t = window.setTimeout(() => {
      setFlash((prev) => {
        const next = new Map(prev);
        keys.forEach((k) => {
          if (next.get(k) === seq) next.delete(k);
        });
        return next;
      });
    }, 700);
    timersRef.current.push(t);
  }, []);

  /* ---------------- 生成 / 重排 ---------------- */

  const runGenerate = useCallback(
    async (text: string, base: Slot[] | null, raw?: string) => {
      const instr = text.trim();
      if (!instr || loading) return;
      lastRequestRef.current = { instruction: instr, base, raw: raw ?? instr };
      rawInstructionRef.current = raw ?? instr;

      setLoading(true);
      setFailure(null);
      setValidateFailure(null);
      setPhase('parse');
      timersRef.current.forEach((t) => window.clearTimeout(t));
      timersRef.current = PHASE_TIMELINE.slice(1).map((p) =>
        window.setTimeout(() => setPhase(p.phase), p.at),
      );

      try {
        const res = await apiGenerate(instr, base);
        setResult(res);
        setDirty(false);
        setValidation(res.validation ?? null);
        if (res.solution?.slots?.length) {
          setSlots(res.solution.slots);
          setMetrics(res.solution.soft_metrics ?? null);
          const changed = res.diff?.changed_slots?.map((s) => slotKey(s.day, s.shift)) ?? [];
          triggerFlash(changed);
        } else {
          setSlots(null);
          setMetrics(null);
        }
        if (health !== 'ok') setHealth('ok');
      } catch (e) {
        setFailure(toFailure(e));
      } finally {
        timersRef.current.forEach((t) => window.clearTimeout(t));
        timersRef.current = [];
        setLoading(false);
      }
    },
    [health, loading, triggerFlash],
  );

  const retryLast = useCallback(() => {
    const last = lastRequestRef.current;
    if (last) void runGenerate(last.instruction, last.base, last.raw);
  }, [runGenerate]);

  /* ---------------- 实时校验 ---------------- */

  const runValidate = useCallback(
    async (next: Slot[]) => {
      setValidating(true);
      setValidateFailure(null);
      try {
        const res = await apiValidate(next, meta);
        setValidation(res.validation);
        setMetrics(res.soft_metrics ?? null);
      } catch (e) {
        setValidateFailure(toFailure(e));
      } finally {
        setValidating(false);
      }
    },
    [meta],
  );

  const applyChange = useCallback(
    (day: string, shift: string, remove: string | null, add: string | null) => {
      if (!slots) return;
      const next = applySlotChange(slots, day, shift, remove, add);
      setSlots(next);
      setDirty(true);
      triggerFlash([slotKey(day, shift)]);
      void runValidate(next);
    },
    [slots, runValidate, triggerFlash],
  );

  const applySuggestion = useCallback(
    (s: Suggestion) => applyChange(s.day, s.shift, s.remove, s.add),
    [applyChange],
  );

  const resetToGenerated = useCallback(() => {
    if (!result?.solution?.slots) return;
    setSlots(result.solution.slots);
    setValidation(result.validation ?? null);
    setMetrics(result.solution.soft_metrics ?? null);
    setDirty(false);
    setValidateFailure(null);
  }, [result]);

  /* ---------------- 派生 ---------------- */

  const issues = useMemo(() => buildIssueIndex(validation), [validation]);
  const hasSchedule = Boolean(slots?.length);
  // 澄清态优先：后端 mode=clarify 时不产出排班，也不产出无解诊断
  const clarification =
    result && (result.mode === 'clarify' || result.clarification) ? result.clarification : null;
  const infeasible = clarification ? null : (result?.infeasible ?? null);

  const onScenario = (s: Scenario) => {
    setInstruction(s.instruction);
    void runGenerate(s.instruction, s.base_required ? (slots ?? null) : null);
  };

  return (
    <div className="min-h-screen bg-soft">
      <TopBar health={health} mock={mock} onRecheck={() => void checkHealth()} />

      <main className="mx-auto max-w-[1240px] space-y-3 px-5 py-4">
        {mock ? <MockToolbar current={mockCase} onSelect={(c) => setMockCase(c)} /> : null}

        {metaFailure ? (
          <ErrorCard
            failure={metaFailure}
            title="员工档案加载失败 · GET /api/meta"
            onRetry={() => void loadMeta()}
            retryLabel="重新加载"
          />
        ) : null}

        <InstructionPanel
          instruction={instruction}
          onChange={setInstruction}
          onGenerate={() => void runGenerate(instruction, null)}
          loading={loading}
          phase={phase}
          scenarios={scenarios}
          onScenario={onScenario}
          hasSchedule={hasSchedule}
        />

        {result?.intent && !loading ? (
          <IntentCard
            intent={result.intent}
            loading={loading}
            onRegenerate={(chips) =>
              void runGenerate(
                buildCorrectedInstruction(rawInstructionRef.current || instruction, chips),
                null,
                rawInstructionRef.current || instruction,
              )
            }
          />
        ) : null}

        {failure ? (
          <ErrorCard
            failure={failure}
            title="排班生成失败 · POST /api/generate"
            onRetry={retryLast}
            retryLabel="重试本次请求"
          />
        ) : null}

        {loading ? (
          <BoardSkeleton phase={phase} />
        ) : clarification ? (
          <>
            <ClarifyCard
              clarification={clarification}
              lead={result?.explanation?.bullets?.[0]}
              onUseRewrite={(text) => {
                setInstruction(text);
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
            />
            {meta ? (
              <ValidationPanel
                meta={meta}
                validation={null}
                softMetrics={null}
                validating={false}
                pending
                pendingHint="澄清态不产出排班，硬规则等指令明确后再校验"
                onApplySuggestion={applySuggestion}
              />
            ) : null}
          </>
        ) : infeasible ? (
          <>
            <InfeasibleCard infeasible={infeasible} />
            {result?.explanation ? (
              <ExplanationCard explanation={result.explanation} timing={result.timing} />
            ) : null}
            {meta ? (
              <ValidationPanel
                meta={meta}
                validation={null}
                softMetrics={null}
                validating={false}
                pending
                pendingHint="本次无可行解，没有排班可校验；请先按上方解锁路径放宽条件"
                onApplySuggestion={applySuggestion}
              />
            ) : null}
          </>
        ) : hasSchedule && meta && slots ? (
          <>
            {result?.mode === 'adjust' && result.diff ? <DiffBanner diff={result.diff} /> : null}

            <ScheduleBoard
              meta={meta}
              slots={slots}
              issues={issues}
              flashKeys={flash}
              dirty={dirty}
              validating={validating}
              onPickChip={(slot, employeeId) => setSwapTarget({ slot, employeeId })}
              onAddEmployee={(slot) => setSwapTarget({ slot, employeeId: null })}
              onReoptimize={() =>
                void runGenerate(
                  rawInstructionRef.current || instruction || '在当前排班基础上做最小扰动重排',
                  slots,
                  rawInstructionRef.current || instruction,
                )
              }
              onReset={resetToGenerated}
              canReoptimize={!loading}
            />

            {validateFailure ? (
              <ErrorCard
                failure={validateFailure}
                title="实时校验失败 · POST /api/validate"
                onRetry={() => slots && void runValidate(slots)}
                retryLabel="重新校验"
              />
            ) : null}

            <ValidationPanel
              meta={meta}
              validation={validation}
              softMetrics={metrics}
              validating={validating}
              onApplySuggestion={applySuggestion}
            />

            {result?.explanation ? (
              <ExplanationCard explanation={result.explanation} timing={result.timing} />
            ) : null}
          </>
        ) : hasSchedule && !meta ? (
          <div className="space-y-2 rounded-card border border-line bg-white p-4 shadow-card">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-[120px]" />
            <p className="text-[11.5px] text-mut">正在加载员工档案（/api/meta）后渲染看板…</p>
          </div>
        ) : (
          <EmptyBoard
            onPick={(t) => {
              setInstruction(t);
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
          />
        )}

        <footer className="pb-6 pt-1 text-center text-[10.5px] leading-relaxed text-mut-2">
          AI 出 0→80 的草案，店长做 80→100 的微调，微调时实时校验兜底 ·
          求解与校验全部在后端完成，前端只做展示与本地编辑
          {mock ? ' · 当前为 mock 数据' : ''}
        </footer>
      </main>

      {meta && slots ? (
        <SwapDialog
          target={swapTarget}
          meta={meta}
          slots={slots}
          onClose={() => setSwapTarget(null)}
          onApply={applyChange}
        />
      ) : null}
    </div>
  );
}
