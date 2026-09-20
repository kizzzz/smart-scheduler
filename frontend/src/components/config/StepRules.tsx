import { useMemo, useState } from 'react';
import { HelpCircle, Lock, Plus, Shield, Trash2 } from 'lucide-react';
import type { RuleDef, SchedulerConfig } from '../../types';
import {
  absoluteSpan,
  activeEmployees,
  hasAttribute,
  minRequiredFor,
  ruleSummary,
} from '../../config/derive';
import {
  addRule,
  removeMinStaffOverride,
  removeRule,
  setMinStaffOverride,
  updateRule,
  updateRuleParams,
} from '../../config/edit';
import { RULE_TEMPLATES, addability, templateOf } from '../../config/ruleTemplates';
import { cn } from '../../lib/utils';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import {
  Field,
  NumberInput,
  Select,
  Stepper,
  Switch,
  TextInput,
  toneOf,
  type FieldIssue,
} from '../ui/Form';
import { SectionTitle, WhyNote } from './parts';

/**
 * 第三步：规则。
 *
 * 每张卡片 = 一条规则实例。参数控件按 type 分发，而不是给一个通用的 JSON 编辑器 ——
 * 规则是这套系统的正确性来源，用户填错一个字段的代价是排班失真，所以每个参数
 * 都值得有自己的控件、单位和边界提示。
 */
export function StepRules({
  config,
  errorOf,
  onUpdate,
}: {
  config: SchedulerConfig;
  errorOf: (target: string) => FieldIssue | null;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const enabledCount = config.rules.filter((r) => r.enabled || r.locked).length;

  return (
    <div className="space-y-4">
      <section>
        <SectionTitle
          title="生效的规则"
          hint="全部是硬约束：违反就算违规。求解器与校验器读的是同一份规则，所以这里改完，校验面板的结论也跟着变"
          right={
            <Badge tone="teal" icon={<Shield size={10} />}>
              生效 {enabledCount} / 共 {config.rules.length} 条
            </Badge>
          }
        />
        <div className="space-y-2">
          {config.rules.map((rule) => (
            <RuleCard
              key={rule.id}
              rule={rule}
              config={config}
              errorOf={errorOf}
              onUpdate={onUpdate}
            />
          ))}
        </div>
      </section>

      <section>
        <SectionTitle
          title="从模板库新增规则"
          hint="只能从模板新增，不能写自然语言规则 —— 那样校验器就不再是唯一真相源，也没法把违规定位到具体格子"
        />
        <div className="grid gap-1.5 md:grid-cols-2">
          {RULE_TEMPLATES.map((tpl) => {
            const can = addability(config, tpl);
            return (
              <div
                key={tpl.type}
                className={cn(
                  'flex items-start gap-2 rounded-lg border px-2.5 py-2',
                  can.ok ? 'border-line bg-white' : 'border-line-2 bg-soft',
                )}
              >
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
                    {tpl.label}
                    {tpl.locked ? (
                      <Badge tone="neutral" icon={<Lock size={9} />}>
                        内建
                      </Badge>
                    ) : null}
                    <span className="font-mono text-[10px] font-normal text-mut-2">{tpl.origin}</span>
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-mut">{tpl.summary}</p>
                  {!can.ok ? (
                    <p className="mt-0.5 text-[10.5px] text-mut-2">{can.reason}</p>
                  ) : null}
                </div>
                <Button
                  size="sm"
                  variant={can.ok ? 'outline' : 'ghost'}
                  disabled={!can.ok}
                  title={can.ok ? `新增「${tpl.label}」` : can.reason}
                  onClick={() => onUpdate((c) => addRule(c, tpl.type))}
                >
                  <Plus size={12} />
                  添加
                </Button>
              </div>
            );
          })}
        </div>
        <WhyNote>
          模板库外的诉求（例如「E01 和 E02 不能同班」）当前表达不了。这类高频诉求会逐条收进模板库，
          而不是开一个自由文本的口子。
        </WhyNote>
      </section>
    </div>
  );
}

function RuleCard({
  rule,
  config,
  errorOf,
  onUpdate,
}: {
  rule: RuleDef;
  config: SchedulerConfig;
  errorOf: (target: string) => FieldIssue | null;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const [showWhy, setShowWhy] = useState(false);
  const tpl = templateOf(rule.type);
  const live = rule.enabled || rule.locked;

  return (
    <div
      className={cn(
        'rounded-card border px-3 py-2.5 transition-colors duration-150',
        live ? 'border-line bg-white' : 'border-line-2 bg-soft',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <code
          className={cn(
            'flex-none rounded px-1 font-mono text-[10.5px] font-semibold leading-[18px]',
            live ? 'bg-teal-50 text-teal-800' : 'bg-[#F1F5F4] text-mut',
          )}
        >
          {rule.id}
        </code>
        <TextInput
          value={rule.name}
          size="sm"
          aria-label={`${rule.id} 规则显示名`}
          invalid={toneOf(errorOf(`rule:${rule.id}.name`))}
          className="max-w-[260px] flex-1 font-medium"
          onChange={(e) => onUpdate((c) => updateRule(c, rule.id, { name: e.target.value }))}
        />
        {rule.locked ? (
          <Badge tone="neutral" icon={<Lock size={10} />}>
            系统内建 · 不可关闭
          </Badge>
        ) : null}
        <button
          type="button"
          onClick={() => setShowWhy((v) => !v)}
          aria-label={`为什么有${rule.name}这条规则`}
          title="为什么有这条规则"
          className="rounded-md p-1 text-mut-2 transition-colors duration-150 hover:bg-line-2 hover:text-ink-2 focus-ring"
        >
          <HelpCircle size={13} />
        </button>
        <span className="ml-auto flex items-center gap-2">
          <span className="text-[10.5px] text-mut">{live ? '生效中' : '已停用'}</span>
          <Switch
            checked={live}
            disabled={rule.locked}
            label={
              rule.locked
                ? `${rule.name} 为系统内建规则，不可关闭`
                : `${live ? '停用' : '启用'} ${rule.name}`
            }
            onChange={(v) => onUpdate((c) => updateRule(c, rule.id, { enabled: v }))}
          />
          {rule.locked ? null : (
            <button
              type="button"
              onClick={() => onUpdate((c) => removeRule(c, rule.id))}
              aria-label={`删除规则 ${rule.id}`}
              title="删除这条规则"
              className="rounded-md p-1 text-mut-2 transition-colors duration-150 hover:bg-fail-bg hover:text-fail focus-ring"
            >
              <Trash2 size={13} />
            </button>
          )}
        </span>
      </div>

      <p className="mt-1 text-[11.5px] leading-relaxed text-mut">{ruleSummary(rule)}</p>
      {errorOf(`rule:${rule.id}.name`) ? (
        <p
          className={cn(
            'mt-0.5 text-[10.5px]',
            errorOf(`rule:${rule.id}.name`)?.level === 'warn' ? 'text-pend-deep' : 'text-fail',
          )}
        >
          {errorOf(`rule:${rule.id}.name`)?.message}
        </p>
      ) : null}

      {showWhy && tpl ? (
        <div className="mt-1.5">
          <WhyNote tone="teal">
            <b className="font-semibold">对应原规则 {tpl.origin}。</b> {tpl.why}
          </WhyNote>
        </div>
      ) : null}

      {live ? (
        <div className="mt-2 border-t border-dashed border-line-2 pt-2">
          <RuleParams rule={rule} config={config} errorOf={errorOf} onUpdate={onUpdate} />
        </div>
      ) : null}
    </div>
  );
}

function RuleParams({
  rule,
  config,
  errorOf,
  onUpdate,
}: {
  rule: RuleDef;
  config: SchedulerConfig;
  errorOf: (target: string) => FieldIssue | null;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const { days, shifts } = config.scenario;
  const slotTotal = days.length * shifts.length;
  const p = rule.params;
  const set = (patch: Parameters<typeof updateRuleParams>[2]) =>
    onUpdate((c) => updateRuleParams(c, rule.id, patch));

  switch (rule.type) {
    case 'min_staff_per_shift':
      return (
        <MinStaffParamsEditor rule={rule} config={config} errorOf={errorOf} onUpdate={onUpdate} />
      );

    case 'require_attribute': {
      const attr = p.attr ?? 'skill';
      const pool = attr === 'role' ? config.role_pool : config.skill_pool;
      const owners = activeEmployees(config).filter((e) => hasAttribute(e, attr, p.value ?? ''));
      return (
        <div className="flex flex-wrap items-start gap-3">
          <Field label="按什么要求" className="w-[112px]">
            <Select
              value={attr}
              size="sm"
              aria-label={`${rule.id} 属性类型`}
              onChange={(e) => {
                const next = e.target.value as 'skill' | 'role';
                const nextPool = next === 'role' ? config.role_pool : config.skill_pool;
                // 切换维度时顺手把 value 落到新字典的第一项，避免留下一个跨字典的悬空值
                set({ attr: next, value: nextPool.includes(p.value ?? '') ? p.value : nextPool[0] ?? '' });
              }}
            >
              <option value="skill">技能</option>
              <option value="role">角色</option>
            </Select>
          </Field>
          <Field
            label={attr === 'role' ? '角色' : '技能'}
            error={errorOf(`rule:${rule.id}.value`)}
            className="w-[148px]"
          >
            <Select
              value={p.value ?? ''}
              size="sm"
              aria-label={`${rule.id} 属性值`}
              invalid={toneOf(errorOf(`rule:${rule.id}.value`))}
              onChange={(e) => set({ value: e.target.value })}
            >
              <option value="">请选择</option>
              {pool.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
              {p.value && !pool.includes(p.value) ? (
                <option value={p.value}>{p.value}（不在字典）</option>
              ) : null}
            </Select>
          </Field>
          <Field label="每班至少" error={errorOf(`rule:${rule.id}.min`)} className="w-auto">
            <span className="flex h-7 items-center">
              <Stepper
                value={p.min ?? 1}
                min={1}
                max={Math.max(1, activeEmployees(config).length)}
                suffix="人"
                label={`${rule.id} 人数要求`}
                onChange={(v) => set({ min: v })}
              />
            </span>
          </Field>
          <p className="mt-[18px] text-[11px] leading-relaxed text-mut">
            当前有 <b className="font-semibold text-ink-2">{owners.length}</b> 名启用员工
            {attr === 'role' ? '担任' : '具备'}「{p.value || '—'}」
          </p>
        </div>
      );
    }

    case 'max_shifts_per_period':
      return (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="每人最多" error={errorOf(`rule:${rule.id}.max`)}>
            <span className="flex h-7 items-center">
              <Stepper
                value={p.max ?? 1}
                min={1}
                max={slotTotal}
                suffix="个班"
                label={`${rule.id} 班次上限`}
                onChange={(v) => set({ max: v })}
              />
            </span>
          </Field>
          <p className="text-[11px] leading-relaxed text-mut">
            本周期共 {slotTotal} 个班位。个别员工可在第二步单独覆盖这个上限。
          </p>
        </div>
      );

    case 'max_consecutive_days':
      return (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="最多连续" error={errorOf(`rule:${rule.id}.max`)}>
            <span className="flex h-7 items-center">
              <Stepper
                value={p.max ?? 1}
                min={1}
                max={Math.max(1, days.length)}
                suffix="天"
                label={`${rule.id} 连续天数上限`}
                onChange={(v) => set({ max: v })}
              />
            </span>
          </Field>
          <p className="text-[11px] leading-relaxed text-mut">
            周期共 {days.length} 天，填 {days.length} 及以上等于不限制。
          </p>
        </div>
      );

    case 'min_rest_hours':
      return (
        <div className="flex flex-wrap items-start gap-3">
          <Field label="至少休息" error={errorOf(`rule:${rule.id}.hours`)} className="w-[112px]">
            <NumberInput
              value={p.hours ?? null}
              size="sm"
              min={0}
              max={24}
              suffix="小时"
              aria-label={`${rule.id} 最小休息小时数`}
              invalid={toneOf(errorOf(`rule:${rule.id}.hours`))}
              onChange={(v) => set({ hours: v ?? 0 })}
            />
          </Field>
          <p className="mt-[18px] text-[11px] leading-relaxed text-mut">
            相邻两班间隔不足{' '}
            <b className="font-semibold text-ink-2">{p.hours ?? 0} 小时</b>
            视为违规（恰好 {p.hours ?? 0} 小时合规）。按班次起止时间实际计算，跨夜班也算得准：
            当前班次组合下最短的跨班间隔是{' '}
            <b className="font-semibold text-ink-2">{minGapHours(config)} 小时</b>
            ，填得比它大才会禁止这种接班，填 {minGapHours(config)} 则恰好放行。
          </p>
        </div>
      );

    case 'one_shift_per_day':
      return (
        <p className="text-[11px] leading-relaxed text-mut">
          关掉它就允许同一天给同一个人排两个班（三班制门店的连班）。
          当前每天有 {shifts.length} 个班次，
          {shifts.length > 1 ? '关掉后请配合「相邻班次最小休息间隔」一起用。' : '只有 1 个班次，关不关都一样。'}
        </p>
      );

    default:
      return (
        <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-mut">
          <Lock size={11} className="mt-[2px] flex-none text-mut-2" />
          这条规则没有可调参数。它是反幻觉与可执行性的地基，所以只能看、不能关。
        </p>
      );
  }
}

/** 相邻两个班次之间可能出现的最短间隔小时数，用来告诉用户「填多少才会生效」 */
function minGapHours(config: SchedulerConfig): number {
  const { shifts } = config.scenario;
  if (shifts.length === 0) return 0;
  let min = Infinity;
  for (const a of shifts) {
    for (const b of shifts) {
      // 同日 a→b 与次日 a→b 两种接法都要考虑；同一个班次自己接自己只看跨天
      for (const dayGap of a.id === b.id ? [1] : [0, 1]) {
        const first = absoluteSpan(0, a);
        const second = absoluteSpan(dayGap, b);
        const gap = second.start - first.end;
        if (gap >= 0) min = Math.min(min, gap / 60);
      }
    }
  }
  return Number.isFinite(min) ? Math.round(min * 10) / 10 : 0;
}

/**
 * 人数下限的参数编辑器。
 *
 * 例外（overrides）用「格子矩阵 + 占位符」表达：每格的占位符是规则算出来的默认值，
 * 填了数字就成为这一格的例外，清空即恢复。比「加一条例外 → 选日期 → 选班次 → 填人数」
 * 的表单流程少三步，而且能一眼看出整周的人数分布。
 */
function MinStaffParamsEditor({
  rule,
  config,
  errorOf,
  onUpdate,
}: {
  rule: RuleDef;
  config: SchedulerConfig;
  errorOf: (target: string) => FieldIssue | null;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const [openGrid, setOpenGrid] = useState(false);
  const { days, shifts } = config.scenario;
  const overrides = rule.params.overrides ?? [];
  const activeCount = activeEmployees(config).length;
  const overrideMap = useMemo(
    () => new Map(overrides.map((o) => [`${o.day}|${o.shift}`, o.min])),
    [overrides],
  );
  const hasPeak = days.some((d) => d.peak);
  const baseOf = (dayId: string) => {
    const peak = days.find((d) => d.id === dayId)?.peak ?? false;
    return (peak ? rule.params.peak : rule.params.default) ?? rule.params.default ?? 0;
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-start gap-4">
        <Field label="普通日每班至少" error={errorOf(`rule:${rule.id}.default`)}>
          <span className="flex h-7 items-center">
            <Stepper
              value={rule.params.default ?? 0}
              min={0}
              max={Math.max(activeCount, 1)}
              suffix="人"
              label="普通日人数下限"
              onChange={(v) => onUpdate((c) => updateRuleParams(c, rule.id, { default: v }))}
            />
          </span>
        </Field>
        <Field
          label="高峰日每班至少"
          hint={hasPeak ? undefined : '当前没有任何一天被标为高峰日，这个值暂时用不上'}
          error={errorOf(`rule:${rule.id}.peak`)}
        >
          <span className="flex h-7 items-center">
            <Stepper
              value={rule.params.peak ?? 0}
              min={0}
              max={Math.max(activeCount, 1)}
              suffix="人"
              label="高峰日人数下限"
              onChange={(v) => onUpdate((c) => updateRuleParams(c, rule.id, { peak: v }))}
            />
          </span>
        </Field>
        <div className="mt-[18px]">
          <Button size="sm" variant={openGrid ? 'primary' : 'secondary'} onClick={() => setOpenGrid((v) => !v)}>
            单格例外
            {overrides.length > 0 ? (
              <span className="rounded-full bg-white/20 px-1 text-[10px] tabular-nums">
                {overrides.length}
              </span>
            ) : null}
          </Button>
        </div>
      </div>

      {openGrid ? (
        <div className="rounded-lg border border-line bg-soft/60 p-2">
          <p className="mb-1.5 text-[10.5px] leading-relaxed text-mut">
            每格填「这一格至少要几个人」。留空 = 按上面两个档位自动取值（灰色数字即当前取值）；
            填了数字就是这一格的例外，边框会变成青色。
          </p>
          <div className="-mx-0.5 overflow-x-auto px-0.5 pb-1 scrollbar-thin">
            <div
              className="grid gap-1"
              style={{ gridTemplateColumns: `56px repeat(${days.length}, minmax(56px, 1fr))` }}
            >
              <div />
              {days.map((d) => (
                <div
                  key={d.id}
                  className={cn(
                    'truncate rounded-md px-1 text-center text-[10px] font-semibold',
                    d.peak ? 'bg-teal-50 text-teal-800' : 'text-mut',
                  )}
                >
                  {d.label}
                </div>
              ))}
              {shifts.map((s) => (
                <div key={s.id} className="contents">
                  <div className="flex items-center justify-center truncate rounded-md border border-line-2 bg-white px-1 text-[10px] font-semibold text-ink-2">
                    {s.name}
                  </div>
                  {days.map((d) => {
                    const key = `${d.id}|${s.id}`;
                    const value = overrideMap.get(key);
                    const isOverride = value !== undefined;
                    return (
                      <span key={key} className={cn('rounded-md', isOverride && 'ring-1 ring-teal-400')}>
                        <NumberInput
                          value={value ?? null}
                          size="sm"
                          min={0}
                          max={Math.max(activeCount, 1)}
                          placeholder={String(baseOf(d.id))}
                          aria-label={`${d.label} ${s.name} 人数下限例外`}
                          onChange={(v) =>
                            onUpdate((c) =>
                              v === null
                                ? removeMinStaffOverride(c, rule.id, d.id, s.id)
                                : setMinStaffOverride(c, rule.id, { day: d.id, shift: s.id, min: v }),
                            )
                          }
                        />
                      </span>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
          {overrides.length > 0 ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span className="text-[10.5px] text-mut">生效的例外：</span>
              {overrides.map((o) => (
                <span
                  key={`${o.day}|${o.shift}`}
                  className="inline-flex items-center gap-1 rounded-full border border-teal-200 bg-white px-1.5 text-[10px] leading-[16px] text-teal-800"
                >
                  {days.find((d) => d.id === o.day)?.label ?? o.day}{' '}
                  {shifts.find((s) => s.id === o.shift)?.name ?? o.shift} ≥{o.min}
                  <button
                    type="button"
                    aria-label="移除这条例外"
                    onClick={() => onUpdate((c) => removeMinStaffOverride(c, rule.id, o.day, o.shift))}
                    className="text-mut-2 transition-colors duration-150 hover:text-fail"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          {(rule.params.overrides ?? []).some((_, i) => errorOf(`rule:${rule.id}.overrides.${i}`)) ? (
            <p className="mt-1 text-[10.5px] text-fail">
              {(rule.params.overrides ?? [])
                .map((_, i) => errorOf(`rule:${rule.id}.overrides.${i}`))
                .filter(Boolean)
                .join('；')}
            </p>
          ) : null}
        </div>
      ) : null}

      <p className="text-[10.5px] leading-relaxed text-mut-2">
        当前生效结果：
        {days.slice(0, 4).map((d) => (
          <span key={d.id} className="ml-1.5">
            {d.label} {shifts.map((s) => minRequiredFor(config, d.id, s.id)).join('/')}
          </span>
        ))}
        {days.length > 4 ? ' …' : ''}
        （按班次顺序 {shifts.map((s) => s.name).join('/')}，共需{' '}
        {days.reduce((n, d) => n + shifts.reduce((m, s) => m + minRequiredFor(config, d.id, s.id), 0), 0)} 人次）
      </p>
    </div>
  );
}
