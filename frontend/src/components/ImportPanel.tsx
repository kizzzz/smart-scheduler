import { useEffect, useRef, useState } from 'react';
import { CircleAlert, Download, FileSpreadsheet, FlaskConical, Loader2, Upload } from 'lucide-react';
import type { ApiFailure, ImportKind } from '../types';
import { IMPORT_ACCEPT, MOCK_SAMPLE_FILES } from '../mock';
import { cn } from '../lib/utils';
import { Button } from './ui/Button';
import { Card } from './ui/Card';

/**
 * 导入入口：点击选择 + 拖拽，外加模板下载。
 *
 * 上传态的文案按文件类型分开：CSV 是毫秒级、图片要过视觉模型约 10 秒，
 * 用同一句「上传中」会让图片场景看起来像卡死，所以图片额外显示已等待秒数。
 */
export function ImportPanel({
  busy,
  busyKind,
  busyFileName,
  failure,
  mock,
  onFile,
  onDownloadTemplate,
  onDismissFailure,
}: {
  busy: boolean;
  busyKind: ImportKind | null;
  busyFileName: string | null;
  failure: ApiFailure | null;
  mock: boolean;
  onFile: (file: File) => void;
  onDownloadTemplate: () => void;
  onDismissFailure: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return undefined;
    }
    const t = window.setInterval(() => setElapsed((v) => v + 1), 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  const pick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onFile(file);
  };

  return (
    <Card className="px-4 py-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!busy) pick(e.dataTransfer.files);
        }}
        className={cn(
          'flex flex-wrap items-center gap-3 rounded-[10px] border-[1.5px] border-dashed px-3 py-2.5 transition-colors duration-150',
          dragging ? 'border-teal-500 bg-teal-50' : 'border-line bg-soft',
          busy && 'border-teal-200 bg-teal-50/50',
        )}
      >
        <span
          className={cn(
            'flex h-9 w-9 flex-none items-center justify-center rounded-[10px] border',
            busy ? 'border-teal-200 bg-white text-teal-700' : 'border-line bg-white text-mut',
          )}
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
        </span>

        <div className="min-w-0 flex-1">
          {busy ? (
            <>
              <p className="truncate text-[12.5px] font-semibold text-ink">
                {busyKind === 'image'
                  ? `正在用视觉模型识别图片中的排班表…通常约 10 秒${elapsed > 0 ? ` · 已等待 ${elapsed}s` : ''}`
                  : '正在解析表格文件…'}
              </p>
              <p className="mt-0.5 truncate text-[11px] text-mut">
                {busyFileName}
                {busyKind === 'image' ? ' · 识别完成后会先进入确认预览，不会直接覆盖看板' : ''}
              </p>
              <div className="mt-1.5 h-[3px] overflow-hidden rounded-full bg-line">
                <div className="h-full w-1/3 animate-progress-slide rounded-full bg-teal-600" />
              </div>
            </>
          ) : (
            <>
              <p className="text-[12.5px] font-semibold text-ink">导入已有排班表，一键体检</p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-mut">
                拖拽或选择 CSV / Excel / 排班表图片（≤5 MB）。解析后先进入确认预览，
                <b className="font-semibold text-ink-2">应用前不会动现有看板</b>。
              </p>
            </>
          )}
        </div>

        <div className="flex flex-none items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => inputRef.current?.click()} disabled={busy}>
            <FileSpreadsheet size={12} />
            选择文件
          </Button>
          <Button size="sm" variant="ghost" onClick={onDownloadTemplate} disabled={busy}>
            <Download size={12} />
            CSV 模板
          </Button>
        </div>

        <input
          ref={inputRef}
          type="file"
          accept={IMPORT_ACCEPT}
          className="hidden"
          onChange={(e) => {
            pick(e.target.files);
            // 清空 value，否则连续选同一个文件不会触发 change
            e.target.value = '';
          }}
        />
      </div>

      {mock && !busy ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1 text-[10.5px] font-semibold text-pend-deep">
            <FlaskConical size={10} />
            演示样例（无需真实文件）
          </span>
          {MOCK_SAMPLE_FILES.map((s) => (
            <button
              key={s.label}
              type="button"
              title={s.hint}
              onClick={() => onFile(s.build())}
              className="rounded-md border border-pend-border bg-pend-bg px-2 py-[3px] text-[11px] font-semibold text-pend-deep transition-colors duration-150 hover:bg-white focus-ring"
            >
              {s.label}
            </button>
          ))}
        </div>
      ) : null}

      {failure ? (
        <div className="mt-2 flex items-start gap-2 rounded-lg border border-fail-border bg-fail-bg px-2.5 py-2">
          <CircleAlert size={13} className="mt-[2px] flex-none text-fail" />
          <div className="min-w-0 flex-1">
            <p className="text-[12px] font-semibold leading-relaxed text-fail-deep">
              导入失败{failure.status ? `（HTTP ${failure.status}）` : ''}：{failure.message}
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-mut">
              长表（日期/班次/员工）与矩阵表（日期/早班/晚班）都支持；工号写 E01 或 1 都能识别。
              拿不准格式就先下载上面的 CSV 模板。
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={onDismissFailure} className="flex-none">
            知道了
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
