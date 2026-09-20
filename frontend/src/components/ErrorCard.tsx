import type { ReactNode } from 'react';
import { RefreshCw, ServerCrash, TriangleAlert } from 'lucide-react';
import type { ApiFailure } from '../types';
import { cn } from '../lib/utils';
import { Button } from './ui/Button';
import { Card } from './ui/Card';

/**
 * 统一的失败卡片。
 *
 * `tone` 存在的理由：不是所有失败都同等严重。生成失败是红色（流程断了），
 * 而「默认配置接口不可用、已回退到内置示例配置」只是降级（流程还能走完），
 * 用红色会让人以为界面不能用了。
 */
export function ErrorCard({
  failure,
  onRetry,
  retryLabel = '重试',
  title = '请求失败',
  tone = 'fail',
  hint,
}: {
  failure: ApiFailure;
  onRetry?: () => void;
  retryLabel?: string;
  title?: string;
  tone?: 'fail' | 'pend';
  /** 覆盖默认的排查建议；降级场景应说明「现在还能做什么」 */
  hint?: ReactNode;
}) {
  const warn = tone === 'pend';
  return (
    <Card tone={warn ? 'pend' : 'fail'} className="px-4 py-3.5">
      <div className="flex items-start gap-3">
        <span className={cn('mt-[1px] flex-none', warn ? 'text-pend' : 'text-fail')}>
          {warn ? <TriangleAlert size={17} /> : <ServerCrash size={17} />}
        </span>
        <div className="min-w-0 flex-1">
          <h3
            className={cn(
              'text-[13.5px] font-semibold',
              warn ? 'text-pend-deep' : 'text-fail-deep',
            )}
          >
            {title}
            {failure.status ? `（HTTP ${failure.status}）` : ''}
          </h3>
          <p
            className={cn(
              'mt-1 break-words text-[12px] leading-relaxed',
              warn ? 'text-pend-deep' : 'text-[#7F2727]',
            )}
          >
            {failure.message}
          </p>
          {failure.detail ? (
            <pre
              className={cn(
                'mt-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-md border bg-white px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-mut scrollbar-thin',
                warn ? 'border-pend-border' : 'border-fail-border',
              )}
            >
              {failure.detail}
            </pre>
          ) : null}
          <p className="mt-2 text-[11px] leading-relaxed text-mut">
            {hint ?? (
              <>
                排查建议：确认后端 <code className="font-mono">uvicorn</code> 已在 8000 端口运行；也可以在
                URL 后加 <code className="font-mono">?mock=1</code> 用 mock 数据浏览界面。
              </>
            )}
          </p>
        </div>
        {onRetry ? (
          <Button variant={warn ? 'secondary' : 'danger'} size="sm" onClick={onRetry} className="flex-none">
            <RefreshCw size={12} />
            {retryLabel}
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
