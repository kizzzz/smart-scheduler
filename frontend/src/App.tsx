import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  downloadTemplate,
  generate as apiGenerate,
  importSchedule,
  validate as apiValidate,
  getHealth,
  getModels,
  getScenarios,
  isMockMode,
  mockCaseOverride,
  setMockCase,
} from './api';
import type {
  ApiFailure,
  ConfigValidateResponse,
  GenerateResponse,
  ImportKind,
  ImportResponse,
  Meta,
  ModelCatalog,
  Scenario,
  ScenarioEcho,
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
import { toFailure } from './lib/failure';
import { useConfigState } from './config/useConfigState';
import type { FormScope } from './config/checks';
import {
  exampleInstructions,
  metaFromConfig,
  metaFromEcho,
  minRequiredFor,
  scenarioEchoFromConfig,
} from './config/derive';
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
import { ConfigPage } from './components/config/ConfigPage';
import {
  ConfigBlockedNotice,
  SampleConfigNotice,
  StaleConfigNotice,
} from './components/config/ConfigNotices';
import { ViewTabs, type AppView } from './components/ViewTabs';
import { Skeleton } from './components/ui/Skeleton';

/**
 * 阶段进度条按所选模型的**实测整体耗时**铺开，不用固定毫秒数。
 *
 * 起因是默认模型换成了 glm-4.5-flash（实测整体约 52 秒）：旧实现在 1.6 秒就把进度推到
 * 「生成解释中」，然后原地停 50 秒，用户只会以为页面挂了。
 *
 * 比例来自真实链路构成：一次生成跑两趟 LLM（意图解析、解释生成），求解与校验是毫秒级。
 * 所以解析约占前半程，解释约占后半程。
 */
const PHASE_RATIO: Array<{ ratio: number; phase: SolvePhase }> = [
  { ratio: 0, phase: 'parse' },
  { ratio: 0.45, phase: 'solve' },
  { ratio: 0.48, phase: 'validate' },
  { ratio: 0.52, phase: 'explain' },
];

/** 兜底 10 秒：模型清单没拿到时按旧默认模型的量级估，宁可估短也不要不动 */
const FALLBACK_ETA_S = 10;

function phaseTimeline(etaSeconds: number): Array<{ at: number; phase: SolvePhase }> {
  const eta = Math.max(etaSeconds, 2) * 1000;
  return PHASE_RATIO.map((p) => ({ phase: p.phase, at: Math.round(eta * p.ratio) }));
}

/**
 * 从 400 响应里认出「这是配置自检不通过」。
 *
 * 契约 5.3 规定 `/api/generate` 遇到 config errors 时返回 400，body 与 `/api/config/validate`
 * 同构。认出来就能渲染成「配置有问题 + 去修哪里」，而不是一句「请求失败（HTTP 400）」。
 */
function configCheckFromError(e: unknown): ConfigValidateResponse | null {
  if (!(e instanceof ApiError) || e.status !== 400) return null;
  const body = e.body as Partial<ConfigValidateResponse> | null;
  if (!body || !Array.isArray(body.errors) || body.errors.length === 0) return null;
  return {
    ok: false,
    errors: body.errors,
    warnings: Array.isArray(body.warnings) ? body.warnings : [],
    capacity: body.capacity ?? null,
  };
}

/** 导入确认态的待确认结果：解析响应 + 已按维度补全的 slots，应用前不进主状态 */
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

  /** 配置是全局单一状态源：看板维度、候选人、校验口径、每次请求的 payload 都从它来 */
  const cfg = useConfigState();
  const config = cfg.config;

  const [view, setView] = useState<AppView>('generate');
  const [health, setHealth] = useState<HealthState>('checking');
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

  /**
   * 看板上这张表**当时**用的维度与配置指纹。
   *
   * 契约 2.2：改配置不清空已有排班。所以看板必须能按产出时的维度继续渲染，
   * 并且能对比出「配置已经变了」——这正是 StaleConfigNotice 的判据。
   */
  const [boardScenario, setBoardScenario] = useState<ScenarioEcho | null>(null);
  const [boardFingerprint, setBoardFingerprint] = useState<string | null>(null);
  /**
   * 产出这张表时的场景名。单独记一份，是因为回显契约里只有天/班维度、没有名字：
   * 若直接读当前配置，改完名字后看板会一边挂着「基于旧配置」一边显示新名字，自相矛盾。
   */
  const [boardScenarioName, setBoardScenarioName] = useState<string | null>(null);

  /** 后端自检结论（400 的 body）；本地 blocked 时为 null，用 cfg.check 兜底展示 */
  const [serverCheck, setServerCheck] = useState<ConfigValidateResponse | null>(null);
  const [blockedVisible, setBlockedVisible] = useState(false);
  /**
   * 配置页停在哪一步。状态放在这里而不是 ConfigPage 内部，是为了让生成页那条「生成被拦下」
   * 的横幅能直接把用户送到能改这件事的那一步 —— 只切到配置页再让他自己找「规则」，
   * 等于把定位工作退回给用户。
   */
  const [configStep, setConfigStep] = useState<FormScope>('scenario');

  const [importBusy, setImportBusy] = useState<{ kind: ImportKind; name: string } | null>(null);
  const [importFailure, setImportFailure] = useState<ApiFailure | null>(null);
  const [importDraft, setImportDraft] = useState<ImportDraft | null>(null);

  const [flash, setFlash] = useState<Map<string, number>>(new Map());
  const [swapTarget, setSwapTarget] = useState<SwapTarget | null>(null);

  /**
   * 当前所选文本模型的实测整体耗时。默认模型 glm-4.5-flash 约 52 秒，
   * 所以这个数字必须真实传到 UI：进度条按它铺开，按钮下方也照它提示预计等待。
   */
  const etaSeconds = useMemo(() => {
    const id = textModel ?? models?.default_text;
    const m = models?.text.find((x) => x.id === id);
    return m?.measured?.e2e_latency_s ?? FALLBACK_ETA_S;
  }, [models, textModel]);

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
    void loadModels();
    getScenarios()
      .then(setScenarios)
      .catch(() => setScenarios([]));
  }, [checkHealth, loadModels]);

  useEffect(() => () => timersRef.current.forEach((t) => window.clearTimeout(t)), []);

  /* ---------------- 维度投影 ---------------- */

  /**
   * 看板 / 换人 / 校验面板共用的维度视图。
   *
   * 有 `boardScenario`（本次求解的回显）时按它渲染，否则按当前配置。这样「配置改成 3 天」
   * 之后，旧的 7 天排班仍然完整可读，而不是有 4 列显示成「缺失」。
   */
  const meta: Meta | null = useMemo(() => {
    if (!config) return null;
    return boardScenario ? metaFromEcho(boardScenario, config) : metaFromConfig(config);
  }, [boardScenario, config]);

  /** 配置页与「按新配置重排」用的是当前配置维度，不能被旧回显影响 */
  const configMeta: Meta | null = useMemo(() => (config ? metaFromConfig(config) : null), [config]);

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

  const gotoConfig = useCallback(() => {
    setView('config');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  /** 带落点的跳转：横幅上每条错误都知道自己该去哪一步 */
  const gotoConfigStep = useCallback(
    (scope: FormScope) => {
      setConfigStep(scope);
      gotoConfig();
    },
    [gotoConfig],
  );

  const runGenerate = useCallback(
    async (text: string, base: Slot[] | null, raw?: string) => {
      const instr = text.trim();
      if (!instr || loading || !config) return;

      /**
       * 生成前先拦一道（契约 2.3）。等 60 秒换来一句「无解」是最差的体验，
       * 而这些问题（供不应求、技能不存在、工号重复）在配置阶段就是确定性可判的。
       */
      if (cfg.blocked) {
        setServerCheck(null);
        setBlockedVisible(true);
        setFailure(null);
        window.scrollTo({ top: 0, behavior: 'smooth' });
        return;
      }

      cfg.flushSave();
      lastRequestRef.current = { instruction: instr, base, raw: raw ?? instr };
      rawInstructionRef.current = raw ?? instr;

      setLoading(true);
      setFailure(null);
      setValidateFailure(null);
      setBlockedVisible(false);
      setServerCheck(null);
      setPhase('parse');
      timersRef.current.forEach((t) => window.clearTimeout(t));
      timersRef.current = phaseTimeline(etaSeconds)
        .slice(1)
        .map((p) => window.setTimeout(() => setPhase(p.phase), p.at));

      try {
        const res = await apiGenerate(instr, base, textModel, config);
        setResult(res);
        setDirty(false);
        setValidation(res.validation ?? null);
        if (res.solution?.slots?.length) {
          setSlots(res.solution.slots);
          setMetrics(res.solution.soft_metrics ?? null);
          setOrigin('generated');
          // 维度回显缺失（旧后端）时按本地配置记账，看板仍然按正确的列数渲染
          setBoardScenario(res.scenario ?? scenarioEchoFromConfig(config));
          setBoardFingerprint(cfg.fingerprint);
          setBoardScenarioName(config?.scenario.name ?? null);
          const changed = res.diff?.changed_slots?.map((s) => slotKey(s.day, s.shift)) ?? [];
          triggerFlash(changed);
        } else {
          setSlots(null);
          setMetrics(null);
        }
        if (health !== 'ok') setHealth('ok');
      } catch (e) {
        const check = configCheckFromError(e);
        if (check) {
          // 后端自检拦下来的：按「配置有问题」呈现，而不是一次普通的请求失败
          setServerCheck(check);
          setBlockedVisible(true);
          // 横幅在页面顶部，而点「生成」的按钮通常在下面；不滚上去等于提示没出现
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else {
          setFailure(toFailure(e));
        }
      } finally {
        timersRef.current.forEach((t) => window.clearTimeout(t));
        timersRef.current = [];
        setLoading(false);
      }
    },
    [cfg, config, etaSeconds, health, loading, textModel, triggerFlash],
  );

  const retryLast = useCallback(() => {
    const last = lastRequestRef.current;
    if (last) void runGenerate(last.instruction, last.base, last.raw);
  }, [runGenerate]);

  /** 按新配置整表重排：维度可能已经变了，所以不拿旧表当 base_slots */
  const regenerateWithNewConfig = useCallback(() => {
    const text = rawInstructionRef.current || instruction || '按当前配置排一版';
    void runGenerate(text, null, rawInstructionRef.current || instruction || undefined);
  }, [instruction, runGenerate]);

  /* ---------------- 实时校验 ---------------- */

  const runValidate = useCallback(
    async (next: Slot[]) => {
      setValidating(true);
      setValidateFailure(null);
      try {
        const res = await apiValidate(next, config);
        setValidation(res.validation);
        setMetrics(res.soft_metrics ?? null);
      } catch (e) {
        setValidateFailure(toFailure(e));
      } finally {
        setValidating(false);
      }
    },
    [config],
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
      if (!config || !configMeta) {
        setImportFailure({ message: '配置还没加载完成，导入结果无法体检，请稍后重试。' });
        return;
      }
      const kind: ImportKind = isImageFile(file.name) ? 'image' : 'sheet';
      setImportBusy({ kind, name: file.name });
      setImportFailure(null);
      try {
        const res = await importSchedule(file, kind === 'image' ? visionModel : null, config);
        // 导入的表按**当前配置**的维度与人数下限归一：导入后它就是要继续编辑的基线
        const normalized = normalizeImportedSlots(configMeta, res.slots ?? [], (day, shift) =>
          minRequiredFor(config, day, shift),
        );
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
    [config, configMeta, importBusy, visionModel],
  );

  const applyImport = useCallback(() => {
    const draft = importDraft;
    if (!draft || !config) return;
    setSlots(draft.slots);
    setValidation(draft.response.validation ?? null);
    setMetrics(draft.response.soft_metrics ?? null);
    // 导入的表没有 intent / 解释，留着上一次生成的卡片会让人误读成「这是 AI 排的」
    setResult(null);
    setFailure(null);
    setValidateFailure(null);
    setDirty(false);
    setOrigin('imported');
    setBoardScenario(scenarioEchoFromConfig(config));
    setBoardFingerprint(cfg.fingerprint);
    setBoardScenarioName(config?.scenario.name ?? null);
    importedBaselineRef.current = draft;
    setImportDraft(null);
    triggerFlash(draft.slots.map((s) => slotKey(s.day, s.shift)));
    // 契约保证 import 带回 validation；万一缺失就本地补一次校验，别让面板空着
    if (!draft.response.validation) void runValidate(draft.slots);
  }, [cfg.fingerprint, config, importDraft, runValidate, triggerFlash]);

  /* ---------------- 派生 ---------------- */

  const issues = useMemo(() => buildIssueIndex(validation), [validation]);
  const hasSchedule = Boolean(slots?.length);
  /** 排班还在，但配置已经变了。不清空、不静默，只标记（契约 2.2） */
  const staleBoard = hasSchedule && boardFingerprint !== null && boardFingerprint !== cfg.fingerprint;
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

  const ruleCount = config ? config.rules.filter((r) => r.enabled || r.locked).length : null;
  const slotCount = config
    ? config.scenario.days.length * config.scenario.shifts.length
    : null;
  /** 示例指令按当前配置拼，点一下就填进输入框的东西不能指向不存在的人或日期 */
  const examples = useMemo(() => (config ? exampleInstructions(config) : []), [config]);
  const blockedReason = cfg.blocked
    ? `配置自检有 ${(cfg.check?.errors.length ?? 0) + cfg.formErrors.length} 项必须先修`
    : null;

  /**
   * 顶栏副标题里的规则口径。
   *
   * 原来恒为「N 条硬规则零违规」。但无解态（validation=null）根本没排出表，
   * 澄清态同理——这时候还宣称「零违规」就是在说假话，而且正好和下方的「本周期无可行解」
   * 自相矛盾。所以只在真有校验结论时才敢说结论。
   */
  const ruleTagline =
    validation === null
      ? `${ruleCount} 条硬规则待校验`
      : validation.violation_count > 0
        ? `${ruleCount} 条硬规则 · ${validation.violation_count} 处违规`
        : `${ruleCount} 条硬规则零违规`;

  return (
    <div className="min-h-screen bg-soft">
      <TopBar
        health={health}
        mock={mock}
        subtitle={
          config
            ? `${config.scenario.name} · ${config.scenario.days.length} 天 × ${config.scenario.shifts.length} 班 · ${ruleTagline}`
            : '自然语言排班 · 硬规则零违规'
        }
        onRecheck={() => void checkHealth()}
        nav={
          <ViewTabs
            view={view}
            onChange={setView}
            configIssues={(cfg.check?.errors.length ?? 0) + cfg.formErrors.length}
            configWarnings={
              (cfg.check?.warnings.length ?? 0) +
              cfg.formIssues.filter((i) => i.level === 'warn').length
            }
          />
        }
        modelPicker={
          view === 'generate' ? (
            <ModelPicker
              catalog={models}
              loading={modelsLoading}
              textModel={textModel}
              visionModel={visionModel}
              onSelectText={setTextModel}
              onSelectVision={setVisionModel}
            />
          ) : null
        }
      />

      <main className="mx-auto max-w-[1240px] space-y-3 px-5 py-4">
        {mock ? <MockToolbar current={mockCase} onSelect={(c) => setMockCase(c)} /> : null}

        {cfg.loadFailure ? (
          <ErrorCard
            failure={cfg.loadFailure}
            tone="pend"
            title="默认配置加载失败 · GET /api/config/default"
            onRetry={cfg.reload}
            retryLabel="重新加载"
            hint="已临时使用内置的示例门店配置，你仍然可以编辑并生成排班。"
          />
        ) : null}

        {view === 'config' ? (
          <ConfigPage
            state={cfg}
            step={configStep}
            onStepChange={setConfigStep}
            onGotoGenerate={() => setView('generate')}
          />
        ) : importDraft && meta ? (
          /* 导入确认态接管主区域：先确认再生效，避免用户以为看板已被覆盖 */
          <>
            <ImportPreview
              meta={configMeta ?? meta}
              result={importDraft.response}
              fileName={importDraft.fileName}
              slots={importDraft.slots}
              onApply={applyImport}
              onDiscard={() => setImportDraft(null)}
            />
            <ValidationPanel
              meta={configMeta ?? meta}
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
            {blockedVisible ? (
              <ConfigBlockedNotice
                check={serverCheck ?? cfg.check}
                formIssueCount={serverCheck ? 0 : cfg.formErrors.length}
                onGoConfig={gotoConfig}
                onJump={gotoConfigStep}
              />
            ) : cfg.isSample && config ? (
              <SampleConfigNotice config={config} onGoConfig={gotoConfig} />
            ) : null}

            {staleBoard ? (
              <StaleConfigNotice
                disabled={loading}
                onGoConfig={gotoConfig}
                onRegenerate={regenerateWithNewConfig}
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
              etaSeconds={etaSeconds}
              blockedReason={blockedReason}
              onGoConfig={gotoConfig}
              placeholderExample={examples[1] ?? examples[0] ?? null}
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
              <BoardSkeleton phase={phase} slotCount={slotCount} ruleCount={ruleCount} />
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
                <InfeasibleCard infeasible={infeasible} ruleCount={ruleCount} />
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
                    pendingHint="本次无可行解，没有排班可校验；请先按上方解锁路径放宽条件，或回到配置页降低人数下限"
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
                  stale={staleBoard}
                  scenarioName={boardScenarioName ?? config?.scenario.name ?? null}
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
                  pendingHint="这次响应没有带回校验结论，动一下排班或点上方「重新校验」即可重新体检"
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
                <p className="text-[11.5px] text-mut">正在加载排班配置后渲染看板…</p>
              </div>
            ) : (
              <EmptyBoard
                dims={
                  config
                    ? {
                        days: config.scenario.days.length,
                        shifts: config.scenario.shifts.length,
                        peak: config.scenario.days.map((d) => d.peak),
                      }
                    : null
                }
                ruleCount={ruleCount}
                examples={examples}
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
          求解与校验全部在后端完成，前端只做展示与本地编辑 ·
          配置存在浏览器本地，随每次请求带给后端
          {mock ? ' · 当前为 mock 数据' : ''}
        </footer>
      </main>

      {meta && slots && !importDraft && view === 'generate' ? (
        <SwapDialog
          target={swapTarget}
          meta={meta}
          config={config}
          slots={slots}
          onClose={() => setSwapTarget(null)}
          onApply={applyChange}
          /**
           * 旧配置的格子取不到候选（后端 400）时的唯一正解：先按当前配置重排一版。
           * 所以这个出口必须开在弹窗里——用户是在那儿撞上问题的。
           */
          onRegenerate={loading ? undefined : regenerateWithNewConfig}
          onGoConfig={gotoConfig}
        />
      ) : null}
    </div>
  );
}
