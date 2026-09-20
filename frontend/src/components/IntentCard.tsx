import { useEffect, useState } from 'react';
import { Check, Pencil, RefreshCw, ShieldCheck, TriangleAlert, Undo2, Wand2 } from 'lucide-react';
import type { Intent, IntentChip } from '../types';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Tooltip } from './ui/Tooltip';

export function IntentCard({
  intent,
  loading,
  modelLabel,
  onRegenerate,
}: {
  intent: Intent;
  loading: boolean;
  /** 由 App 用 /api/models 的 label 解析 intent.model_used 得到，缺清单时回退成模型 id */
  modelLabel?: string | null;
  onRegenerate: (chips: IntentChip[]) => void;
}) {
  const [chips, setChips] = useState<IntentChip[]>(intent.chips);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    setChips(intent.chips);
    setEditing(null);
  }, [intent]);

  const dirty = chips.some((c, i) => c.value !== intent.chips[i]?.value);
  const guardrail = intent.guardrail;
  // model_used 为 undefined 说明后端还是 v1.0，不显示模型徽标；为 null 则是明确降级到规则解析
  const showModel = intent.model_used !== undefined;

  const commit = (key: string) => {
    const value = draft.trim();
    if (value) setChips((prev) => prev.map((c) => (c.key === key ? { ...c, value } : c)));
    setEditing(null);
  };

  return (
    <div className="rounded-card border border-teal-200 bg-teal-50/70 px-4 py-3">
      {intent.degraded ? (
        <div className="mb-2.5 flex items-start gap-2 rounded-lg border border-pend-border bg-pend-bg px-2.5 py-2 text-[11.5px] leading-relaxed text-pend-deep">
          <TriangleAlert size={13} className="mt-[1px] flex-none text-pend" />
          <span>
            {intent.degrade_reason ?? '意图解析服务异常'}
            ：已降级为结构化解析，核心功能不受影响。建议核对下方理解，必要时直接修正。
          </span>
        </div>
      ) : null}

      {/*
        换模型不是故障，所以不用告警色；但也不能不说：用户是照着模型的准确率做选择的，
        默认模型超时后偷偷换成更快的模型，等于给了一个他没同意的质量档位。
      */}
      {!intent.degraded && intent.model_fallback_from ? (
        <div className="mb-2.5 flex items-start gap-2 rounded-lg border border-teal-200 bg-white px-2.5 py-2 text-[11.5px] leading-relaxed text-ink-2">
          <TriangleAlert size={13} className="mt-[1px] flex-none text-teal-700" />
          <span>
            {intent.degrade_reason ??
              `${intent.model_fallback_from} 本次超时，已自动改用更快的模型完成解析`}
            。解析仍由模型完成、护栏照常生效，建议核对下方理解。
          </span>
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <Wand2 size={13} className="flex-none text-teal-700" />
        <h3 className="text-[13px] font-semibold text-ink">已理解为</h3>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <Badge tone={intent.operation === 'unknown' ? 'pend' : 'teal'}>
            {intent.operation === 'adjust'
              ? '操作：最小扰动重排'
              : intent.operation === 'unknown'
                ? '操作：待澄清'
                : '操作：全新生成'}
          </Badge>
          {showModel ? (
            <Tooltip
              content={
                intent.model_used
                  ? '本次意图解析与解释实际使用的模型。模型只影响理解与措辞，排班可行性由求解器与校验器保证。'
                  : '本次未调用 LLM：意图由关键词 + 正则的结构化解析得出，排班与校验完全不受影响。'
              }
            >
              <Badge tone={intent.model_used ? 'neutral' : 'pend'}>
                {intent.model_used ? `模型：${modelLabel ?? intent.model_used}` : '模型：未调用（规则解析）'}
              </Badge>
            </Tooltip>
          ) : null}
          {intent.degraded ? (
            <Badge tone="pend">结构化降级解析</Badge>
          ) : intent.model_fallback_from ? (
            <Badge tone="pend">已自动换模型</Badge>
          ) : (
            <Badge tone="pass">LLM 解析成功</Badge>
          )}
        </div>
      </div>

      {guardrail?.triggered ? (
        <div className="mt-2.5 flex items-start gap-2 rounded-lg border border-teal-200 bg-white px-2.5 py-2">
          <ShieldCheck size={13} className="mt-[1px] flex-none text-teal-700" />
          <div className="min-w-0">
            <p className="text-[11.5px] font-semibold leading-relaxed text-teal-900">
              反幻觉护栏已生效：{guardrail.summary}
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-mut">
              这是系统正常工作的证据，不是故障：模型编造的约束在进入求解器前就被丢掉了，下面的理解与排班不含这些内容。
              {guardrail.invalid_employee_ids.length ? (
                <>
                  {' '}
                  被丢弃的非法工号：
                  {guardrail.invalid_employee_ids.map((id) => (
                    <code key={id} className="ml-1 rounded bg-teal-50 px-1 font-mono text-[10.5px] text-teal-800">
                      {id}
                    </code>
                  ))}
                </>
              ) : null}
            </p>
          </div>
        </div>
      ) : null}

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {chips.map((chip) => {
          const changed = chip.value !== intent.chips.find((c) => c.key === chip.key)?.value;
          if (editing === chip.key) {
            return (
              <span
                key={chip.key}
                className="inline-flex items-center gap-1 rounded-[7px] border border-teal-400 bg-white py-[3px] pl-2.5 pr-1 text-[12px]"
              >
                <span className="text-mut">{chip.label}：</span>
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commit(chip.key);
                    if (e.key === 'Escape') setEditing(null);
                  }}
                  className="w-[176px] border-none bg-transparent text-[12px] font-semibold text-teal-900 outline-none"
                />
                <button
                  type="button"
                  onClick={() => commit(chip.key)}
                  className="rounded-[5px] bg-teal-700 p-[3px] text-white transition-colors duration-150 hover:bg-teal-800 focus-ring"
                  aria-label="确认修正"
                >
                  <Check size={11} />
                </button>
              </span>
            );
          }
          return (
            <span
              key={chip.key}
              className="inline-flex items-center gap-1.5 rounded-[7px] border border-teal-200 bg-white py-[3px] pl-2.5 pr-1 text-[12px] text-ink-2 transition-colors duration-150"
            >
              <span className="text-mut">{chip.label}：</span>
              <b className="font-semibold text-teal-800">{chip.value}</b>
              {changed ? <Badge tone="pend">已修正</Badge> : null}
              {chip.editable ? (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(chip.key);
                    setDraft(chip.value);
                  }}
                  className="inline-flex items-center gap-[3px] rounded-[5px] bg-teal-50 px-1.5 py-[2px] text-[10.5px] font-semibold text-teal-700 transition-colors duration-150 hover:bg-teal-100 focus-ring"
                  aria-label={`修正${chip.label}`}
                >
                  <Pencil size={9} />
                  改
                </button>
              ) : null}
            </span>
          );
        })}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <p className="text-[11px] text-mut">
          {intent.temp_constraints.length
            ? `识别到 ${intent.temp_constraints.length} 条临时约束：${intent.temp_constraints
                .map((c) => c.raw)
                .join('；')}`
            : '未识别到临时约束，按员工档案的常规可用性排班'}
        </p>
        <div className="ml-auto flex items-center gap-2">
          {dirty ? (
            <>
              <Button size="sm" variant="ghost" onClick={() => setChips(intent.chips)} disabled={loading}>
                <Undo2 size={12} />
                撤销修正
              </Button>
              <Button size="sm" variant="primary" onClick={() => onRegenerate(chips)} disabled={loading}>
                <RefreshCw size={12} />
                用修正后的理解重新生成
              </Button>
            </>
          ) : (
            <span className="text-[11px] text-mut-2">理解有误？点击 chip 上的「改」修正后重新生成</span>
          )}
        </div>
      </div>
    </div>
  );
}
