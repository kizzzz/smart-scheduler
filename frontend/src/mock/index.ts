import type { GenerateResponse, Meta, ModelCatalog, Scenario, Slot } from '../types';
import metaJson from './meta.json';
import modelsJson from './models.json';
import scenariosJson from './scenarios.json';
import normalJson from './generate_normal.json';
import violationJson from './generate_violation.json';
import adjustJson from './generate_adjust.json';
import infeasibleJson from './generate_infeasible.json';
import clarifyJson from './generate_clarify.json';

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

export type MockCase = 'normal' | 'violation' | 'infeasible' | 'adjust' | 'clarify' | 'error';

export const MOCK_CASES: Array<{ value: MockCase; label: string }> = [
  { value: 'normal', label: '正常态' },
  { value: 'violation', label: '违规态 (R-07)' },
  { value: 'infeasible', label: '无解态 (proven)' },
  { value: 'clarify', label: '澄清态 (clarify)' },
  { value: 'adjust', label: '重排 diff' },
  { value: 'error', label: '错误态 (5xx)' },
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

export function mockGenerate(
  instruction: string,
  baseSlots: Slot[] | null,
  override?: MockCase | null,
  model?: string | null,
): GenerateResponse {
  const which = resolveMockCase(instruction, baseSlots, override);
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
  return res;
}
