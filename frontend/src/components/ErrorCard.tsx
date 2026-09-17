import { RefreshCw, ServerCrash } from 'lucide-react';
import type { ApiFailure } from '../types';
import { Button } from './ui/Button';
import { Card } from './ui/Card';

export function ErrorCard({
  failure,
  onRetry,
  retryLabel = '重试',
  title = '请求失败',
}: {
  failure: ApiFailure;
  onRetry?: () => void;
  retryLabel?: string;
  title?: string;
}) {
  return (
    <Card tone="fail" className="px-4 py-3.5">
      <div className="flex items-start gap-3">
        <span className="mt-[1px] flex-none text-fail">
          <ServerCrash size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13.5px] font-semibold text-fail-deep">
            {title}
            {failure.status ? `（HTTP ${failure.status}）` : ''}
          </h3>
          <p className="mt-1 break-words text-[12px] leading-relaxed text-[#7F2727]">{failure.message}</p>
          {failure.detail ? (
            <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-md border border-fail-border bg-white px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-mut scrollbar-thin">
              {failure.detail}
            </pre>
          ) : null}
          <p className="mt-2 text-[11px] text-mut">
            排查建议：确认后端 <code className="font-mono">uvicorn</code> 已在 8000 端口运行；也可以在 URL 后加{' '}
            <code className="font-mono">?mock=1</code> 用 mock 数据浏览界面。
          </p>
        </div>
        {onRetry ? (
          <Button variant="danger" size="sm" onClick={onRetry} className="flex-none">
            <RefreshCw size={12} />
            {retryLabel}
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
