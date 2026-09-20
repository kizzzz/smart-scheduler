import { CalendarCheck2, Settings2 } from 'lucide-react';
import { cn } from '../lib/utils';

export type AppView = 'generate' | 'config';

/**
 * 顶部两段式导航。
 *
 * 为什么不做成侧边菜单或多级路由：这个产品只有两件事——**配置门店**和**排班**，
 * 用户绝大多数时间待在后者。两个 tab 既能表达「配置是个独立的地方」，
 * 又不会让人觉得要学一套导航。
 *
 * 配置 tab 上的角标是刻意的：配置错误会导致生成被拦，用户必须能在任何页面看到
 * 「配置那边有东西要修」，而不是点了生成才发现。
 */
export function ViewTabs({
  view,
  onChange,
  configIssues,
  configWarnings,
}: {
  view: AppView;
  onChange: (v: AppView) => void;
  /** 必须修的数量（自检 errors + 表单红字） */
  configIssues: number;
  /** 提醒但不拦截的数量 */
  configWarnings: number;
}) {
  const tabs: Array<{ key: AppView; label: string; icon: typeof Settings2; badge: number; tone: 'fail' | 'pend' | null }> =
    [
      {
        key: 'config',
        label: '配置',
        icon: Settings2,
        badge: configIssues || configWarnings,
        tone: configIssues > 0 ? 'fail' : configWarnings > 0 ? 'pend' : null,
      },
      { key: 'generate', label: '排班', icon: CalendarCheck2, badge: 0, tone: null },
    ];

  return (
    <nav className="flex items-center gap-0.5 rounded-full border border-line bg-soft p-[2px]">
      {tabs.map((t) => {
        const on = t.key === view;
        const Icon = t.icon;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            aria-current={on ? 'page' : undefined}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[11.5px] font-medium transition-colors duration-150 focus-ring',
              on ? 'bg-white text-teal-800 shadow-card' : 'text-mut hover:text-ink-2',
            )}
          >
            <Icon size={12} />
            {t.label}
            {t.tone && t.badge > 0 ? (
              <span
                className={cn(
                  'rounded-full px-1 text-[9.5px] font-semibold leading-[14px]',
                  t.tone === 'fail' ? 'bg-fail-bg text-fail' : 'bg-pend-bg text-pend',
                )}
                title={t.tone === 'fail' ? '配置有必须修复的问题' : '配置有提醒项'}
              >
                {t.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
