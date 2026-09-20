import type { GenerateResponse, Meta, ModelCatalog, Scenario, SchedulerConfig, Slot } from '../types';
import metaJson from './meta.json';
import modelsJson from './models.json';
import scenariosJson from './scenarios.json';
import normalJson from './generate_normal.json';
import violationJson from './generate_violation.json';
import adjustJson from './generate_adjust.json';
import infeasibleJson from './generate_infeasible.json';
import clarifyJson from './generate_clarify.json';
import { defaultConfig } from '../config/defaults';
import {
  alignSlotsToMeta,
  configFingerprint,
  metaFromConfig,
  minRequiredFor,
  scenarioEchoFromConfig,
} from '../config/derive';
import { validateSlotsWithConfig } from './configValidator';
import { mockGenerateWithConfig } from './configSolver';

export const mockMeta = metaJson as unknown as Meta;
export const mockModels = modelsJson as unknown as ModelCatalog;
export const mockScenarios = scenariosJson as unknown as Scenario[];
export const mockNormal = normalJson as unknown as GenerateResponse;
export const mockViolation = violationJson as unknown as GenerateResponse;
export const mockAdjust = adjustJson as unknown as GenerateResponse;
export const mockInfeasible = infeasibleJson as unknown as GenerateResponse;
export const mockClarify = clarifyJson as unknown as GenerateResponse;

export {
  IMPORT_ACCEPT,
  IMPORT_MAX_BYTES,
  MOCK_SAMPLE_FILES,
  MOCK_TEMPLATE_CSV,
  isImageFile,
  isSupportedFile,
  mockImport,
} from './import';
export {
  mockDefaultConfig,
  mockPrecheckBlocked,
  mockValidateConfig,
  normalizeConfigValidation,
} from './config';
export { mockCandidates, type MockCandidatesResult } from './candidates';

export type MockCase =
  | 'normal'
  | 'violation'
  | 'infeasible'
  | 'adjust'
  | 'clarify'
  | 'error'
  | 'blocked';

export const MOCK_CASES: Array<{ value: MockCase; label: string }> = [
  { value: 'normal', label: '正常态' },
  { value: 'violation', label: '违规态 (R-07)' },
  { value: 'infeasible', label: '无解态 (proven)' },
  { value: 'clarify', label: '澄清态 (clarify)' },
  { value: 'adjust', label: '重排 diff' },
  { value: 'error', label: '错误态 (5xx)' },
  // 后端在求解前跑配置自检、不通过返回 400。单独给个开关是因为这条分支光靠页面上的
  // 自检是走不到的：页面自检和 mock 自检同源，配置有问题时按钮早就被拦住了。
  // 而真后端算得比前端细，完全可能在前端一片绿的情况下给出 400，这个开关演示的就是那一刻。
  { value: 'blocked', label: '生成前被自检拦下 (400)' },
];

/** 依据 URL 覆盖参数与指令关键词决定返回哪份 mock */
export function resolveMockCase(
  instruction: string,
  baseSlots: Slot[] | null,
  override?: MockCase | null,
): MockCase {
  if (override) return override;
  const text = instruction ?? '';
  // 澄清判定优先于 base_slots：指令本身说不清时，后端不会走求解
  if (/小王|小李|某人|明天|后天|那个人|尽快|随便/.test(text)) return 'clarify';
  if (baseSlots) return 'adjust';
  if (/无解|冲突|培训|都请假|值守不足/.test(text)) return 'infeasible';
  if (/早班.*(想|希望)|违规|R-?07|间隔/.test(text)) return 'violation';
  if (/重排|最小扰动|临时请假/.test(text)) return 'adjust';
  return 'normal';
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/**
 * 这份配置是否还是「示例门店」原样。
 *
 * 只比场景维度与员工名单，不比规则：fixture 里的排班格子是按 7 天 × 2 班、20 人写死的，
 * 维度或人员一变就必须改用现场求解；而规则改动不影响格子形状，fixture 仍可复用
 * （校验结论会按新规则重算）。
 */
export function isDefaultShape(config: SchedulerConfig): boolean {
  const shape = (c: SchedulerConfig) =>
    configFingerprint({ ...c, skill_pool: [], role_pool: [], rules: [] });
  return shape(config) === shape(defaultConfig());
}

/**
 * 把 fixture 的格子对齐到配置的维度并按配置重算结论。
 *
 * fixture 用的是老的键（day="一"、shift="早班"），配置化之后维度键是 d1/s1。
 * 对齐放在 mock 边界做一次，下游（看板、换人、校验）就只需要认识一套键。
 */
function reframeFixture(res: GenerateResponse, config: SchedulerConfig): GenerateResponse {
  res.scenario = scenarioEchoFromConfig(config);
  const slots = res.solution?.slots;
  if (!res.solution || !slots?.length) {
    // 没有方案就没有校验结论（契约：solution=null → validation=null）。
    // mock 也必须守住这条，否则前端永远测不到真接口的无解态。
    res.validation = null;
    return res;
  }

  const aligned = alignSlotsToMeta(metaFromConfig(config), slots).map((s) => ({
    ...s,
    // min_required 一律按当前配置重算：规则参数改了，fixture 里的 4/6 就不再是真值
    min_required: minRequiredFor(config, s.day, s.shift),
  }));
  const { validation, soft_metrics } = validateSlotsWithConfig(config, aligned);
  res.solution.slots = aligned;
  res.solution.soft_metrics = soft_metrics;
  res.validation = validation;
  return res;
}

export function mockGenerate(
  instruction: string,
  baseSlots: Slot[] | null,
  override?: MockCase | null,
  model?: string | null,
  config?: SchedulerConfig | null,
): GenerateResponse {
  const which = resolveMockCase(instruction, baseSlots, override);

  // 自定义维度/人员 → 现场贪心求解。澄清态是「指令说不清」，与配置无关，仍走 fixture。
  if (config && which !== 'clarify' && !isDefaultShape(config)) {
    return mockGenerateWithConfig(config, instruction, baseSlots, model);
  }

  const payload =
    which === 'violation'
      ? mockViolation
      : which === 'infeasible'
        ? mockInfeasible
        : which === 'clarify'
          ? mockClarify
          : which === 'adjust'
            ? mockAdjust
            : mockNormal;
  const res = clone(payload);
  if (which === 'clarify' && res.clarification) {
    res.clarification.raw = instruction || res.clarification.raw;
  }
  if (baseSlots && res.mode !== 'adjust' && res.solution) {
    res.mode = 'adjust';
  }
  // 回显用户选中的模型：降级到规则解析时按契约恒为 null，不能被覆盖
  if (model && !res.intent.degraded) {
    res.intent.model_used = model;
  }
  return config ? reframeFixture(res, config) : res;
}
