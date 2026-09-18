import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  downloadTemplate,
  generate as apiGenerate,
  importSchedule,
  validate as apiValidate,
  getHealth,
  getMeta,
  getModels,
  getScenarios,
  isMockMode,
  mockCaseOverride,
  setMockCase,
} from './api';
import type {
  ApiFailure,
  GenerateResponse,
  ImportKind,
  ImportResponse,
  Meta,
  ModelCatalog,
  Scenario,
  SoftMetrics,
  Slot,
  SolvePhase,
  Suggestion,
  Validation,
} from './types';
import { isImageFile } from './mock';
import {
  applySlotChange,
  buildCorrectedInstruction,
  buildIssueIndex,
  normalizeImportedSlots,
} from './lib/schedule';
import { slotKey } from './lib/utils';
import { TopBar, type HealthState } from './components/TopBar';
import { ModelPicker } from './components/ModelPicker';
import { MockToolbar } from './components/MockToolbar';
import { InstructionPanel } from './components/InstructionPanel';
import { ImportPanel } from './components/ImportPanel';
import { ImportPreview } from './components/ImportPreview';
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

/** 导入确认态的待确认结果：解析响应 + 已按 meta 补全的 slots，应用前不进主状态 */
interface ImportDraft {
  response: ImportResponse;
  slots: Slot[];
  fileName: string;
}

/** 当前看板这张表从哪来：AI 生成 vs 导入的既有排班表，两者的措辞与还原目标都不同 */
type BoardOrigin = 'generated' | 'imported';

export default function App() {
  const mock = isMockMode();
  const mockCase = mockCaseOverride();

  const [health, setHealth] = useState<HealthState>('checking');
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaFailure, setMetaFailure] = useState<ApiFailure | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);

  const [models, setModels] = useState<ModelCatalog | null>(null);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [textModel, setTextModel] = useState<string | null>(null);
  const [visionModel, setVisionModel] = useState<string | null>(null);

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
  const [origin, setOrigin] = useState<BoardOrigin>('generated');

  const [importBusy, setImportBusy] = useState<{ kind: ImportKind; name: string } | null>(null);
  const [importFailure, setImportFailure] = useState<ApiFailure | null>(null);
  const [importDraft, setImportDraft] = useState<ImportDraft | null>(null);

  const [flash, setFlash] = useState<Map<string, number>>(new Map());
  const [swapTarget, setSwapTarget] = useState<SwapTarget | null>(null);

  const rawInstructionRef = useRef('');
  const lastRequestRef = useRef<{ instruction: string; base: Slot[] | null; raw: string } | null>(null);
  const importedBaselineRef = useRef<ImportDraft | null>(null);
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

  /**
   * 模型清单拿不到不阻塞主流程：selected 保持 null，`/api/generate` 就不带 model 字段，
   * 后端按 default_text 处理——比弹错误框打断排班更合适。
   */
  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    try {
      const c = await getModels();
      setModels(c);
      setTextModel((prev) => (prev && c.text.some((m) => m.id === prev) ? prev : c.default_text));
      setVisionModel((prev) => (prev && c.vision.some((m) => m.id === prev) ? prev : c.default_vision));
    } catch {
      setModels(null);
    } finally {
      setModelsLoading(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    void loadMeta();
    void loadModels();
    getScenarios()
      .then(setScenarios)
      .catch(() => setScenarios([]));
  }, [checkHealth, loadMeta, loadModels]);

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
        const res = await apiGenerate(instr, base, textModel);
        setResult(res);
        setDirty(false);
        setValidation(res.validation ?? null);
        if (res.solution?.slots?.length) {
          setSlots(res.solution.slots);
          setMetrics(res.solution.soft_metrics ?? null);
          setOrigin('generated');
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
    [health, loading, textModel, triggerFlash],
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

  /** 还原到「未手工微调」的那张表：生成来源回到本次求解结果，导入来源回到导入基线 */
  const resetToBaseline = useCallback(() => {
    if (origin === 'imported') {
      const baseline = importedBaselineRef.current;
      if (!baseline) return;
      setSlots(baseline.slots);
      setValidation(baseline.response.validation ?? null);
      setMetrics(baseline.response.soft_metrics ?? null);
      setDirty(false);
      setValidateFailure(null);
      return;
    }
    if (!result?.solution?.slots) return;
    setSlots(result.solution.slots);
    setValidation(result.validation ?? null);
    setMetrics(result.solution.soft_metrics ?? null);
    setDirty(false);
    setValidateFailure(null);
  }, [origin, result]);

  /* ---------------- 导入 ---------------- */

  const runImport = useCallback(
    async (file: File) => {
      if (importBusy) return;
      if (!meta) {
        setImportFailure({
          message: '员工档案（/api/meta）还没加载完成，导入结果无法体检，请稍后重试。',
        });
        return;
      }
      const kind: ImportKind = isImageFile(file.name) ? 'image' : 'sheet';
      setImportBusy({ kind, name: file.name });
      setImportFailure(null);
      try {
        const res = await importSchedule(file, kind === 'image' ? visionModel : null);
        const normalized = normalizeImportedSlots(meta, res.slots ?? []);
        // ok=false 是「读不出班次」的正常返回（不是 5xx），把后端给的原因直接展示
        if (!res.ok || normalized.length === 0) {
          setImportFailure({
            message:
              res.warnings?.join('；') ||
              '文件里没能读出任何班次。请检查是否包含日期列与班次列，或改用 CSV 模板。',
          });
          return;
        }
        setImportDraft({ response: res, slots: normalized, fileName: file.name });
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } catch (e) {
        setImportFailure(toFailure(e));
      } finally {
        setImportBusy(null);
      }
    },
    [importBusy, meta, visionModel],
  );

  const applyImport = useCallback(() => {
    const draft = importDraft;
    if (!draft) return;
    setSlots(draft.slots);
    setValidation(draft.response.validation ?? null);
    setMetrics(draft.response.soft_metrics ?? null);
    // 导入的表没有 intent / 解释，留着上一次生成的卡片会让人误读成「这是 AI 排的」
    setResult(null);
    setFailure(null);
    setValidateFailure(null);
    setDirty(false);
    setOrigin('imported');
    importedBaselineRef.current = draft;
    setImportDraft(null);
    triggerFlash(draft.slots.map((s) => slotKey(s.day, s.shift)));
    // 契约保证 import 带回 validation；万一缺失就本地补一次校验，别让面板空着
    if (!draft.response.validation) void runValidate(draft.slots);
  }, [importDraft, runValidate, triggerFlash]);

  /* ---------------- 派生 ---------------- */

  const issues = useMemo(() => buildIssueIndex(validation), [validation]);
  const hasSchedule = Boolean(slots?.length);
  // 澄清态优先：后端 mode=clarify 时不产出排班，也不产出无解诊断
  const clarification =
    result && (result.mode === 'clarify' || result.clarification) ? result.clarification : null;
  const infeasible = clarification ? null : (result?.infeasible ?? null);

  /** intent.model_used 是模型 id，label 由 /api/models 提供；清单不可用时退回展示 id */
  const modelLabel = useMemo(() => {
    const id = result?.intent?.model_used;
    if (!id) return null;
    return [...(models?.text ?? []), ...(models?.vision ?? [])].find((m) => m.id === id)?.label ?? id;
  }, [models, result]);

  const onScenario = (s: Scenario) => {
    setInstruction(s.instruction);
    void runGenerate(s.instruction, s.base_required ? (slots ?? null) : null);
  };

  return (
    <div className="min-h-screen bg-soft">
      <TopBar
        health={health}
        mock={mock}
        onRecheck={() => void checkHealth()}
        modelPicker={
          <ModelPicker
            catalog={models}
            loading={modelsLoading}
            textModel={textModel}
            visionModel={visionModel}
            onSelectText={setTextModel}
            onSelectVision={setVisionModel}
          />
        }
      />

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

        {importDraft && meta ? (
          /* 导入确认态接管主区域：先确认再生效，避免用户以为看板已被覆盖 */
          <>
            <ImportPreview
              meta={meta}
              result={importDraft.response}
              fileName={importDraft.fileName}
              slots={importDraft.slots}
              onApply={applyImport}
              onDiscard={() => setImportDraft(null)}
            />
            <ValidationPanel
              meta={meta}
              validation={importDraft.response.validation ?? null}
              softMetrics={importDraft.response.soft_metrics ?? null}
              validating={false}
              subtitle="导入的排班表已过同一套校验器——这就是「拖进来一键看出有没有违规」"
              suggestionsDisabled
              suggestionsHint="修复建议在「应用为基线」之后可一键执行；当前这张表还没生效。"
              onApplySuggestion={applySuggestion}
            />
          </>
        ) : (
          <>
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

            <ImportPanel
              busy={Boolean(importBusy)}
              busyKind={importBusy?.kind ?? null}
              busyFileName={importBusy?.name ?? null}
              failure={importFailure}
              mock={mock}
              onFile={(f) => void runImport(f)}
              onDownloadTemplate={downloadTemplate}
              onDismissFailure={() => setImportFailure(null)}
            />

            {result?.intent && !loading ? (
              <IntentCard
                intent={result.intent}
                loading={loading}
                modelLabel={modelLabel}
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
                  origin={origin}
                  onPickChip={(slot, employeeId) => setSwapTarget({ slot, employeeId })}
                  onAddEmployee={(slot) => setSwapTarget({ slot, employeeId: null })}
                  onReoptimize={() =>
                    void runGenerate(
                      rawInstructionRef.current || instruction || '在当前排班基础上做最小扰动重排',
                      slots,
                      rawInstructionRef.current || instruction,
                    )
                  }
                  onReset={resetToBaseline}
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
          </>
        )}

        <footer className="pb-6 pt-1 text-center text-[10.5px] leading-relaxed text-mut-2">
          AI 出 0→80 的草案，店长做 80→100 的微调，微调时实时校验兜底 ·
          求解与校验全部在后端完成，前端只做展示与本地编辑
          {mock ? ' · 当前为 mock 数据' : ''}
        </footer>
      </main>

      {meta && slots && !importDraft ? (
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
