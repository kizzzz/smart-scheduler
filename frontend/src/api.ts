import type {
  GenerateResponse,
  ImportResponse,
  Meta,
  ModelCatalog,
  Scenario,
  Slot,
  ValidateResponse,
} from './types';
import {
  IMPORT_MAX_BYTES,
  MOCK_CASES,
  MOCK_TEMPLATE_CSV,
  isImageFile,
  isSupportedFile,
  mockGenerate,
  mockImport,
  mockMeta,
  mockModels,
  mockScenarios,
  resolveMockCase,
  type MockCase,
} from './mock';
import { mockValidate } from './mock/validator';

/** 统一的接口错误：网络失败 / 非 2xx / 后端 message */
export class ApiError extends Error {
  status?: number;
  detail?: string;

  constructor(message: string, status?: number, detail?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/* ---------------- mock 开关（?mock=1，可选 &case=xxx） ---------------- */

function params(): URLSearchParams {
  if (typeof window === 'undefined') return new URLSearchParams();
  return new URLSearchParams(window.location.search);
}

/**
 * mock 优先级：URL 参数 > 构建期开关。
 *
 * `VITE_DEMO_MOCK=1` 用于「无后端的纯静态演示站」构建：默认走 mock，
 * 但仍可用 `?mock=0` 强制打真实接口。正式 Docker 构建不设该变量，
 * 默认永远打真实后端。
 */
export function isMockMode(): boolean {
  const v = params().get('mock');
  if (v !== null) return v === '1' || v === 'true';
  return import.meta.env.VITE_DEMO_MOCK === '1';
}

export function mockCaseOverride(): MockCase | null {
  const v = params().get('case');
  const hit = MOCK_CASES.find((c) => c.value === v);
  return hit ? hit.value : null;
}

export function setMockCase(next: MockCase): void {
  const p = params();
  p.set('mock', '1');
  p.set('case', next);
  window.location.search = p.toString();
}

const sleep = (msec: number) => new Promise<void>((r) => window.setTimeout(r, msec));

/* ---------------- 真实请求 ---------------- */

/** 统一解析响应体：非 2xx 时优先取后端 message / detail，让 400 的原因能直接给用户看 */
async function unwrap<T>(res: Response): Promise<T> {
  const raw = await res.text();
  let body: unknown = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = null;
    }
  }

  if (!res.ok) {
    const rec = (body ?? {}) as Record<string, unknown>;
    const message =
      (typeof rec.message === 'string' && rec.message) ||
      (typeof rec.detail === 'string' && rec.detail) ||
      (typeof rec.error === 'string' && rec.error) ||
      `请求失败（HTTP ${res.status}）`;
    throw new ApiError(message, res.status, raw.slice(0, 400));
  }

  if (body === null) throw new ApiError('后端返回了空响应或非法 JSON。', res.status, raw.slice(0, 400));
  return body as T;
}

const OFFLINE_HINT = '无法连接后端服务，请确认 FastAPI 已在 http://127.0.0.1:8000 启动。';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch (e) {
    throw new ApiError(OFFLINE_HINT, undefined, e instanceof Error ? e.message : String(e));
  }
  return unwrap<T>(res);
}

/** multipart 上传：Content-Type 必须交给浏览器生成（要带 boundary），所以不能复用 request */
async function upload<T>(path: string, form: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { method: 'POST', body: form });
  } catch (e) {
    throw new ApiError(OFFLINE_HINT, undefined, e instanceof Error ? e.message : String(e));
  }
  return unwrap<T>(res);
}

/* ---------------- 对外 API ---------------- */

export async function getHealth(): Promise<{ ok: boolean }> {
  if (isMockMode()) {
    await sleep(180);
    return { ok: mockCaseOverride() !== 'error' };
  }
  return request<{ ok: boolean }>('/api/health');
}

export async function getMeta(): Promise<Meta> {
  if (isMockMode()) {
    await sleep(220);
    return mockMeta;
  }
  return request<Meta>('/api/meta');
}

export async function getModels(): Promise<ModelCatalog> {
  if (isMockMode()) {
    await sleep(240);
    return mockModels;
  }
  return request<ModelCatalog>('/api/models');
}

export async function getScenarios(): Promise<Scenario[]> {
  if (isMockMode()) {
    await sleep(160);
    return mockScenarios;
  }
  return request<Scenario[]>('/api/scenarios');
}

export async function generate(
  instruction: string,
  baseSlots: Slot[] | null,
  model?: string | null,
): Promise<GenerateResponse> {
  if (isMockMode()) {
    const override = mockCaseOverride();
    if (resolveMockCase(instruction, baseSlots, override) === 'error') {
      await sleep(700);
      throw new ApiError('求解服务内部错误：solver worker exited unexpectedly', 500, 'mock 500');
    }
    await sleep(1600);
    return mockGenerate(instruction, baseSlots, override, model);
  }
  return request<GenerateResponse>('/api/generate', {
    method: 'POST',
    body: JSON.stringify({ instruction, base_slots: baseSlots, model: model ?? null }),
  });
}

export async function validate(slots: Slot[], meta: Meta | null): Promise<ValidateResponse> {
  if (isMockMode()) {
    await sleep(260);
    if (!meta) throw new ApiError('mock 校验需要员工档案（/api/meta）先加载完成。');
    return mockValidate(meta, slots);
  }
  return request<ValidateResponse>('/api/validate', {
    method: 'POST',
    body: JSON.stringify({ slots }),
  });
}

/**
 * 导入已有排班表。前端先按契约第 3 节的限制做一次本地拦截：
 * 5MB / 扩展名两项都是确定性判断，本地拦掉能省一次注定 400 的往返，
 * 报错文案与后端保持同一口径。
 */
export async function importSchedule(file: File, visionModel?: string | null): Promise<ImportResponse> {
  if (!isSupportedFile(file.name)) {
    throw new ApiError(
      `不支持的文件类型：${file.name}。可导入 CSV / TSV / Excel / PNG / JPG / WEBP。`,
      400,
    );
  }
  if (file.size > IMPORT_MAX_BYTES) {
    throw new ApiError(
      `文件 ${(file.size / 1024 / 1024).toFixed(1)} MB，超过 5 MB 上限。请裁剪图片或拆分表格后重试。`,
      400,
    );
  }

  if (isMockMode()) {
    // 图片走视觉模型约 10 秒，mock 用 2.4 秒保留「明显更慢」的体感又不至于让演示卡住
    await sleep(isImageFile(file.name) ? 2400 : 420);
    return mockImport(file, visionModel);
  }

  const form = new FormData();
  form.append('file', file);
  if (visionModel && isImageFile(file.name)) form.append('vision_model', visionModel);
  return upload<ImportResponse>('/api/import', form);
}

/**
 * 下载 CSV 模板。真实模式直接跳 `GET /api/import/template`（由 Content-Disposition 触发下载）；
 * mock 模式没有后端，用同结构的本地常量生成 Blob，保证静态演示站的入口不是死链。
 */
export function downloadTemplate(): void {
  if (!isMockMode()) {
    window.location.href = '/api/import/template?fmt=csv';
    return;
  }
  const url = URL.createObjectURL(new Blob([`\uFEFF${MOCK_TEMPLATE_CSV}`], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'schedule-template.csv';
  a.click();
  URL.revokeObjectURL(url);
}
