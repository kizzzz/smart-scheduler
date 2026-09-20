import type { SchedulerConfig } from '../types';
import { parseConfigJson } from './checks';

/**
 * 配置持久化：localStorage，后端无状态（契约 3.3）。
 *
 * key 里带 v1 是有意的：将来 version 升到 2 时新旧站点可以并存一段时间，
 * 而不是用同一个 key 互相覆盖。读取时校验 version，对不上就当没有配置——
 * 静默「尽力解析」一份旧结构，比让用户重新配一遍更危险。
 */
const KEY = 'smart-scheduler.config.v1';

export interface StoredConfig {
  config: SchedulerConfig;
  saved_at: string;
}

export function loadStoredConfig(): StoredConfig | null {
  if (typeof window === 'undefined') return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    // 隐私模式 / 禁用存储：功能降级为「本次会话内有效」，不弹错
    return null;
  }
  if (!raw) return null;
  const parsed = parseConfigJson(raw);
  if (!parsed.config) {
    console.warn('[config] 丢弃无法识别的本地配置：', parsed.error);
    return null;
  }
  let savedAt = '';
  try {
    savedAt = (JSON.parse(raw) as { saved_at?: string }).saved_at ?? '';
  } catch {
    savedAt = '';
  }
  return { config: parsed.config, saved_at: savedAt };
}

export function saveStoredConfig(config: SchedulerConfig): string {
  const saved_at = new Date().toISOString();
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ ...config, saved_at }));
  } catch (e) {
    console.warn('[config] 本地保存失败：', e);
  }
  return saved_at;
}

export function clearStoredConfig(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* 同上，存储不可用时无需处理 */
  }
}

/** 导出备份。文件名带日期，用户一次导好几份也能分得清 */
export function downloadConfig(config: SchedulerConfig): string {
  const stamp = new Date().toISOString().slice(0, 10);
  const fileName = `排班配置-${config.scenario.name || '未命名'}-${stamp}.json`;
  const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
  return fileName;
}

export async function readConfigFile(file: File): Promise<ReturnType<typeof parseConfigJson>> {
  const text = await file.text();
  return parseConfigJson(text);
}
