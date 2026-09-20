import {
  Ban,
  CheckCheck,
  CircleAlert,
  FileSpreadsheet,
  ScanEye,
  ShieldQuestion,
  TriangleAlert,
} from 'lucide-react';
import type { ImportResponse, Meta, Slot } from '../types';
import { cn, ms, slotKey } from '../lib/utils';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Card, CardBody, CardHeader } from './ui/Card';
import { EmployeeChip } from './EmployeeChip';

/**
 * 导入确认态。刻意做成主区域的一个状态而不是弹窗：
 * 预览要同时容纳整张网格（最多 14 天 × 4 班）、解析统计、未匹配明细和完整的规则体检结论，
 * 塞进 Dialog 会把「体检结论」压到滚动区外——而体检恰恰是导入功能的主角。
 *
 * 本组件只负责「解析结果」部分，体检结论由 App 紧随其后复用 ValidationPanel 渲染。
 */
export function ImportPreview({
  meta,
  result,
  fileName,
  slots,
  onApply,
  onDiscard,
}: {
  meta: Meta;
  result: ImportResponse;
  fileName: string | null;
  /** 已按 meta 补全的 slots，与「应用为基线」后进看板的数据完全一致 */
  slots: Slot[];
  onApply: () => void;
  onDiscard: () => void;
}) {
  const stats = result.stats;
  const unresolved = result.unresolved ?? [];
  const warnings = result.warnings ?? [];
  const isVision = result.extractor === 'vision_llm';
  const expected = stats?.slots_expected ?? meta.days.length * meta.shifts.length;
  const found = stats?.slots_found ?? slots.length;
  const missing = Math.max(0, expected - found);

  const actions = (
    <>
      <Button size="sm" variant="ghost" onClick={onDiscard}>
        <Ban size={12} />
        放弃
      </Button>
      <Button size="sm" variant="primary" onClick={onApply}>
        <CheckCheck size={12} />
        应用为基线
      </Button>
    </>
  );

  return (
    <Card tone={isVision ? 'pend' : 'teal'} className="overflow-hidden">
      <CardHeader
        icon={isVision ? <ScanEye size={15} /> : <FileSpreadsheet size={15} />}
        title="导入预览 · 待确认"
        subtitle={
          <>
            {fileName ? `${fileName} · ` : ''}
            解析结果尚未生效，确认无误后点「应用为基线」才会替换看板
          </>
        }
        right={actions}
      />

      <CardBody className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="neutral">来源：{sourceLabel(result.source)}</Badge>
          <Badge tone={isVision ? 'pend' : 'pass'}>
            {isVision ? '解析方式：视觉模型识别' : '解析方式：确定性解析（不过 LLM）'}
          </Badge>
          {result.model_used ? (
            <Badge tone="neutral">模型：{result.model_used}</Badge>
          ) : (
            <Badge tone="neutral">未调用模型</Badge>
          )}
          <Badge tone="neutral">布局：{layoutLabel(result.layout)}</Badge>
          <Badge tone={result.confidence >= 0.95 ? 'pass' : 'pend'}>
            置信度 {Math.round(result.confidence * 100)}%
          </Badge>
          {result.requires_confirmation ? <Badge tone="pend">需人工确认</Badge> : null}
          {result.timing_ms ? (
            <span className="text-[10.5px] text-mut-2">
              解析 {ms(result.timing_ms.extract_ms)} · 体检 {ms(result.timing_ms.validate_ms)}
            </span>
          ) : null}
        </div>

        {isVision ? (
          <div className="flex items-start gap-2 rounded-lg border border-pend-border bg-white px-2.5 py-2 text-[11.5px] leading-relaxed text-pend-deep">
            <ScanEye size={13} className="mt-[1px] flex-none text-pend" />
            <span>
              <b className="font-semibold">AI 识别结果，请核对。</b>
              图片识别由视觉模型完成，可能漏格或看错工号；下方每一格都请对照原表确认后再应用。
            </span>
          </div>
        ) : null}

        <div className="grid gap-2 sm:grid-cols-4">
          <StatBox
            label="解析格子"
            value={`${found}/${expected}`}
            tone={missing > 0 ? 'warn' : 'ok'}
            hint={missing > 0 ? `${missing} 格未解析到` : `${expected} 格齐全`}
          />
          <StatBox label="解析人次" value={String(stats?.assignments ?? '—')} tone="plain" hint="文件里读到的排班总人次" />
          <StatBox
            label="已匹配工号"
            value={String(stats?.resolved ?? '—')}
            tone="ok"
            hint="归一到配置里的员工工号并写入班表"
          />
          <StatBox
            label="未匹配"
            value={String(stats?.unresolved ?? unresolved.length)}
            tone={(stats?.unresolved ?? unresolved.length) > 0 ? 'warn' : 'ok'}
            hint="不猜映射，一律列出待人工处理"
          />
        </div>

        <PreviewGrid meta={meta} slots={slots} />

        {unresolved.length ? (
          <section className="rounded-lg border border-pend-border bg-white px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-[12px] font-semibold text-pend-deep">
              <ShieldQuestion size={13} className="text-pend" />
              {unresolved.length} 项无法匹配到工号，已从班表中剔除
              <span className="font-normal text-mut">（员工档案无姓名字段，系统绝不猜映射）</span>
            </p>
            <ul className="mt-1.5 space-y-1">
              {unresolved.map((u, i) => (
                <li
                  key={`${u.raw}-${u.where}-${i}`}
                  className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-dashed border-line-2 pb-1 last:border-0 last:pb-0 text-[11.5px] leading-relaxed"
                >
                  <code className="rounded bg-pend-bg px-1 font-mono text-[11px] font-semibold text-pend-deep">
                    {u.raw}
                  </code>
                  <b className="font-semibold text-ink-2">{u.where}</b>
                  <span className="text-mut">{u.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {warnings.length ? (
          <ul className="space-y-1 rounded-lg border border-line bg-white px-3 py-2">
            {warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-mut">
                <TriangleAlert size={12} className="mt-[2px] flex-none text-pend" />
                {w}
              </li>
            ))}
          </ul>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 border-t border-line-2 pt-2.5">
          <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-mut">
            应用后这张表成为基线：看板渲染它、校验面板标出违规，你可以继续用自然语言微调或点击 chip 手工换人。
          </p>
          {actions}
        </div>
      </CardBody>
    </Card>
  );
}

function sourceLabel(source: string): string {
  if (source === 'csv') return 'CSV';
  // 后端对 .xlsx/.xls 统一返回 'excel'；xlsx/xls 保留兜底，避免后端口径变化时显示原始值
  if (source === 'excel' || source === 'xlsx' || source === 'xls') return 'Excel';
  if (source === 'image') return '图片';
  return source;
}

function layoutLabel(layout: string): string {
  if (layout === 'long') return '长表（日期/班次/员工）';
  if (layout === 'matrix') return '矩阵表（日期×班次）';
  if (layout === 'image') return '图片版式';
  return layout;
}

function StatBox({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: 'ok' | 'warn' | 'plain';
}) {
  return (
    <div
      className={cn(
        'rounded-lg border bg-white px-2.5 py-1.5',
        tone === 'warn' ? 'border-pend-border' : 'border-line',
      )}
    >
      <p className="text-[10.5px] text-mut">{label}</p>
      <b
        className={cn(
          'block text-[16px] font-semibold leading-6 tabular-nums',
          tone === 'warn' ? 'text-pend-deep' : 'text-ink',
        )}
      >
        {value}
      </b>
      <p className="truncate text-[10px] text-mut-2" title={hint}>
        {hint}
      </p>
    </div>
  );
}

/** 预览网格：与看板同一套版式，列数随配置维度变化，缺失的格子高亮成待补状态 */
function PreviewGrid({ meta, slots }: { meta: Meta; slots: Slot[] }) {
  const bySlot = new Map(slots.map((s) => [slotKey(s.day, s.shift), s]));
  const empMap = new Map(meta.employees.map((e) => [e.id, e]));
  const cols = Math.max(meta.days.length, 1);

  return (
    <div className="-mx-1 overflow-x-auto px-1 pb-1 scrollbar-thin">
      <div
        className="grid gap-1"
        style={{
          gridTemplateColumns: `54px repeat(${cols}, minmax(0, 1fr))`,
          minWidth: 54 + cols * 108,
        }}
      >
        <div />
        {meta.days.map((d) => (
          <div
            key={d.key}
            className={cn(
              'rounded-md pb-1 pt-0.5 text-center text-[11px] font-semibold',
              d.is_weekend ? 'bg-teal-50 text-teal-800' : 'text-mut',
            )}
          >
            {d.label}
          </div>
        ))}

        {meta.shifts.map((sh) => (
          <PreviewRow key={sh.key} meta={meta} shift={sh} bySlot={bySlot} empMap={empMap} />
        ))}
      </div>
    </div>
  );
}

function PreviewRow({
  meta,
  shift,
  bySlot,
  empMap,
}: {
  meta: Meta;
  shift: Meta['shifts'][number];
  bySlot: Map<string, Slot>;
  empMap: Map<string, Meta['employees'][number]>;
}) {
  return (
    <>
      <div className="flex flex-col items-center justify-center rounded-lg border border-line-2 bg-white px-1 py-1.5">
        <b className="text-[11px] font-semibold text-ink-2">{shift.label}</b>
        <i className="text-[8.5px] not-italic leading-[1.25] text-mut-2">{shift.time}</i>
      </div>
      {meta.days.map((d) => {
        const slot = bySlot.get(slotKey(d.key, shift.key));
        if (!slot) {
          return (
            <div
              key={d.key}
              className="flex min-h-[52px] flex-col items-center justify-center rounded-lg border-[1.5px] border-dashed border-pend bg-pend-bg px-1 text-center"
            >
              <CircleAlert size={12} className="text-pend" />
              <span className="mt-0.5 text-[9.5px] font-semibold leading-tight text-pend-deep">未解析到</span>
            </div>
          );
        }
        const short = slot.employees.length < slot.min_required;
        return (
          <div key={d.key} className="rounded-lg border border-line bg-white p-1">
            <div className="mb-1 flex justify-end">
              <span
                className={cn(
                  'rounded px-1 text-[9.5px] font-semibold leading-[15px] tabular-nums',
                  short ? 'bg-fail-bg text-fail' : 'bg-pass-bg text-pass',
                )}
              >
                {slot.employees.length}/{slot.min_required}
              </span>
            </div>
            <div className="flex flex-wrap gap-1">
              {slot.employees.map((id) => (
                <EmployeeChip key={id} id={id} employee={empMap.get(id)} interactive={false} />
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}
