import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getDefaultConfig, validateConfig } from '../api';
import type { ApiFailure, ConfigValidateResponse, SchedulerConfig } from '../types';
import { toFailure } from '../lib/failure';
import { blockingIssues, checkConfig, type FormIssue } from './checks';
import { defaultConfig } from './defaults';
import { configFingerprint } from './derive';
import {
  clearStoredConfig,
  downloadConfig,
  loadStoredConfig,
  readConfigFile,
  saveStoredConfig,
} from './storage';

/**
 * 配置的单一状态源。
 *
 * 三个设计点：
 * 1. **同步读 localStorage 作为初值**，只有在本地没有配置时才去请求 `/api/config/default`。
 *    老用户刷新页面不会先闪一下示例门店的数据再跳回自己的配置。
 * 2. **自动保存**（防抖 800ms）而不是让用户点保存按钮。配置页有三个步骤、几十个输入框，
 *    「填了半天忘了保存」的代价是重填一遍；而自动保存的代价只是 localStorage 多写几次。
 * 3. **自检防抖 400ms**，比自动保存更快，因为自检结果是用户改配置时的主要反馈。
 */

export type ConfigSource = 'sample' | 'local';

export interface ConfigStateApi {
  config: SchedulerConfig | null;
  loading: boolean;
  loadFailure: ApiFailure | null;
  /** true = 还在用示例门店配置（从未保存过），生成页据此提示「当前使用示例门店配置」 */
  isSample: boolean;
  savedAt: string | null;
  check: ConfigValidateResponse | null;
  checking: boolean;
  checkFailure: ApiFailure | null;
  /** 全部表单问题，含 level='warn' 的提醒 */
  formIssues: FormIssue[];
  /** 只含 level='error'：这些才是「生成」按钮该拦的东西 */
  formErrors: FormIssue[];
  fingerprint: string;
  /** 配置本身就有错（自检 error 或表单 error）→ 必须拦住「生成」 */
  blocked: boolean;
  update: (fn: (config: SchedulerConfig) => SchedulerConfig) => void;
  reload: () => void;
  resetToSample: () => void;
  revalidate: () => void;
  importFromFile: (file: File) => Promise<string | null>;
  exportToFile: () => string | null;
  flushSave: () => void;
}

export function useConfigState(): ConfigStateApi {
  const stored = useRef(loadStoredConfig());
  const [config, setConfig] = useState<SchedulerConfig | null>(stored.current?.config ?? null);
  const [source, setSource] = useState<ConfigSource>(stored.current ? 'local' : 'sample');
  const [savedAt, setSavedAt] = useState<string | null>(stored.current?.saved_at ?? null);
  const [loading, setLoading] = useState(!stored.current);
  const [loadFailure, setLoadFailure] = useState<ApiFailure | null>(null);

  const [check, setCheck] = useState<ConfigValidateResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkFailure, setCheckFailure] = useState<ApiFailure | null>(null);

  const saveTimer = useRef<number | null>(null);
  const checkTimer = useRef<number | null>(null);
  /** 自检是异步的，用序号丢弃过期响应，避免慢的那次覆盖新的结论 */
  const checkSeq = useRef(0);

  const fetchDefault = useCallback(async () => {
    setLoading(true);
    setLoadFailure(null);
    try {
      const c = await getDefaultConfig();
      setConfig(c);
      setSource('sample');
    } catch (e) {
      setLoadFailure(toFailure(e));
      // 兜底到本地那份示例配置：配置页是入口页，接口抖动不该让用户连界面都进不去
      setConfig(defaultConfig());
      setSource('sample');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!stored.current) void fetchDefault();
  }, [fetchDefault]);

  /* ---------------- 自检 ---------------- */

  const runCheck = useCallback(async (target: SchedulerConfig) => {
    const seq = (checkSeq.current += 1);
    setChecking(true);
    setCheckFailure(null);
    try {
      const res = await validateConfig(target);
      if (seq === checkSeq.current) setCheck(res);
    } catch (e) {
      if (seq === checkSeq.current) {
        setCheckFailure(toFailure(e));
        setCheck(null);
      }
    } finally {
      if (seq === checkSeq.current) setChecking(false);
    }
  }, []);

  useEffect(() => {
    if (!config) return undefined;
    if (checkTimer.current) window.clearTimeout(checkTimer.current);
    checkTimer.current = window.setTimeout(() => void runCheck(config), 400);
    return () => {
      if (checkTimer.current) window.clearTimeout(checkTimer.current);
    };
  }, [config, runCheck]);

  const revalidate = useCallback(() => {
    if (config) void runCheck(config);
  }, [config, runCheck]);

  /* ---------------- 编辑与保存 ---------------- */

  const persist = useCallback((next: SchedulerConfig) => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => setSavedAt(saveStoredConfig(next)), 800);
  }, []);

  const update = useCallback(
    (fn: (config: SchedulerConfig) => SchedulerConfig) => {
      setConfig((prev) => {
        if (!prev) return prev;
        const next = fn(prev);
        // 一旦动过，这份配置就是用户自己的了，示例配置的提示条要随之消失
        setSource('local');
        persist(next);
        return next;
      });
    },
    [persist],
  );

  const flushSave = useCallback(() => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    if (config && source === 'local') setSavedAt(saveStoredConfig(config));
  }, [config, source]);

  const resetToSample = useCallback(() => {
    clearStoredConfig();
    stored.current = null;
    setSavedAt(null);
    void fetchDefault();
  }, [fetchDefault]);

  const reload = useCallback(() => void fetchDefault(), [fetchDefault]);

  const importFromFile = useCallback(
    async (file: File): Promise<string | null> => {
      const parsed = await readConfigFile(file);
      if (!parsed.config) return parsed.error ?? '无法解析这份配置文件';
      setConfig(parsed.config);
      setSource('local');
      setSavedAt(saveStoredConfig(parsed.config));
      return null;
    },
    [],
  );

  const exportToFile = useCallback(() => (config ? downloadConfig(config) : null), [config]);

  const formIssues = useMemo(() => (config ? checkConfig(config) : []), [config]);
  const formErrors = useMemo(() => blockingIssues(formIssues), [formIssues]);
  const fingerprint = useMemo(() => (config ? configFingerprint(config) : ''), [config]);

  return {
    config,
    loading,
    loadFailure,
    isSample: source === 'sample',
    savedAt,
    check,
    checking,
    checkFailure,
    formIssues,
    formErrors,
    fingerprint,
    blocked: (check?.errors.length ?? 0) > 0 || formErrors.length > 0,
    update,
    reload,
    resetToSample,
    revalidate,
    importFromFile,
    exportToFile,
    flushSave,
  };
}
