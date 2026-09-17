import type {
  GenerateResponse,
  Meta,
  Scenario,
  Slot,
  ValidateResponse,
} from './types';
import {
  MOCK_CASES,
  mockGenerate,
  mockMeta,
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

export function isMockMode(): boolean {
  const v = params().get('mock');
  return v === '1' || v === 'true';
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch (e) {
    throw new ApiError(
      '无法连接后端服务，请确认 FastAPI 已在 http://localhost:8000 启动。',
      undefined,
      e instanceof Error ? e.message : String(e),
    );
  }

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
): Promise<GenerateResponse> {
  if (isMockMode()) {
    const override = mockCaseOverride();
    if (resolveMockCase(instruction, baseSlots, override) === 'error') {
      await sleep(700);
      throw new ApiError('求解服务内部错误：solver worker exited unexpectedly', 500, 'mock 500');
    }
    await sleep(1600);
    return mockGenerate(instruction, baseSlots, override);
  }
  return request<GenerateResponse>('/api/generate', {
    method: 'POST',
    body: JSON.stringify({ instruction, base_slots: baseSlots }),
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
