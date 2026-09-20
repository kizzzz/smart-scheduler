import type { ReactNode } from 'react';
import { Activity, CalendarCheck2, FlaskConical } from 'lucide-react';
import { cn } from '../lib/utils';

export type HealthState = 'checking' | 'ok' | 'down';

export function TopBar({
  health,
  mock,
  onRecheck,
  subtitle,
  nav,
  modelPicker,
}: {
  health: HealthState;
  mock: boolean;
  onRecheck: () => void;
  /**
   * 副标题由 App 按当前配置拼装。配置化之后「7 天 2 班 9 条规则」不再是常量，
   * 写死在这里就会和用户实际的配置对不上。
   */
  subtitle?: string;
  /** 页面切换（配置 / 排班） */
  nav?: ReactNode;
  /** 模型选择器由 App 组装后塞进来，TopBar 不感知模型状态 */
  modelPicker?: ReactNode;
}) {
  const dot = {
    checking: 'bg-mut-2',
    ok: 'bg-pass',
    down: 'bg-fail',
  }[health];
  const text = { checking: '检测中', ok: '后端正常', down: '后端不可用' }[health];

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-white/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1240px] items-center gap-3 px-5">
        <span className="flex h-8 w-8 flex-none items-center justify-center rounded-[10px] bg-teal-700 text-white shadow-card">
          <CalendarCheck2 size={17} strokeWidth={2} />
        </span>
        <div className="min-w-0">
          <h1 className="truncate text-[15px] font-semibold tracking-[0.2px] text-ink">智能排班助手</h1>
          <p className="truncate text-[11.5px] text-mut" title={subtitle}>
            {subtitle ?? '自然语言排班 · 硬规则零违规'}
          </p>
        </div>
        {nav ? <div className="ml-1 flex-none">{nav}</div> : null}
        <div className="ml-auto flex items-center gap-2.5">
          {mock ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-pend-border bg-pend-bg px-2 py-[2px] text-[10.5px] font-semibold text-pend">
              <FlaskConical size={10} />
              Mock 数据模式
            </span>
          ) : null}
          {modelPicker}
          <button
            type="button"
            onClick={onRecheck}
            title="重新检测 /api/health"
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-[3px] text-[11px] text-mut transition-colors duration-150 hover:border-teal-200 hover:text-teal-800 focus-ring"
          >
            <span className={cn('h-[6px] w-[6px] rounded-full transition-colors duration-150', dot)} />
            {text}
            <Activity size={11} className="text-mut-2" />
          </button>
        </div>
      </div>
    </header>
  );
}
