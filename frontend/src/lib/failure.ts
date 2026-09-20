import { ApiError } from '../api';
import type { ApiFailure } from '../types';

/**
 * 任意异常 → 界面能直接展示的失败信息。
 *
 * 统一在一处收口，是为了保证「网络断了」「后端 400」「代码抛错」三种来源
 * 在 UI 上的呈现口径一致：message 给人看，status/detail 给排查用。
 */
export function toFailure(e: unknown): ApiFailure {
  if (e instanceof ApiError) return { message: e.message, status: e.status, detail: e.detail };
  if (e instanceof Error) return { message: e.message };
  return { message: String(e) };
}
