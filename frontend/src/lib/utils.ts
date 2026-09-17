/** 极简 className 合并工具（替代 clsx，保持依赖精简） */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

/** 班次唯一键 */
export function slotKey(day: string, shift: string): string {
  return `${day}|${shift}`;
}

export function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

export function ms(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`;
}
