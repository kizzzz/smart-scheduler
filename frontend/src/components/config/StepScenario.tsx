import { CalendarRange, Clock, Moon, Plus, Trash2 } from 'lucide-react';
import type { SchedulerConfig } from '../../types';
import { MAX_DAYS, MAX_SHIFTS } from '../../config/checks';
import { isOvernight, minRequiredFor, timeLabel } from '../../config/derive';
import { addShift, removeShift, setDayCount, updateDay, updateShift } from '../../config/edit';
import { cn } from '../../lib/utils';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Field, Slider, Stepper, TextInput, toneOf, type FieldIssue } from '../ui/Form';
import { SectionTitle, ToggleChip, WhyNote } from './parts';

/**
 * 第一步：排班场景（周期天数 / 每天是否高峰 / 班次时段）。
 *
 * 这一步的信息密度刻意压得很低——它是用户进入配置的第一屏，只要让人明白
 * 「排几天、每天几个班」就够了。人数下限之类的数字属于第三步的规则参数，
 * 放在这里会让第一屏就变成一张报表。
 */
export function StepScenario({
  config,
  errorOf,
  onUpdate,
}: {
  config: SchedulerConfig;
  errorOf: (target: string) => FieldIssue | null;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const { days, shifts } = config.scenario;
  const peakCount = days.filter((d) => d.peak).length;

  return (
    <div className="space-y-4">
      <section>
        <SectionTitle title="这份排班叫什么" hint="只用于界面与导出文件名，不参与求解" />
        <Field error={errorOf('scenario.name')} className="max-w-[340px]">
          <TextInput
            value={config.scenario.name}
            aria-label="场景名称"
            placeholder="例如：门店周排班"
            invalid={toneOf(errorOf('scenario.name'))}
            onChange={(e) =>
              onUpdate((c) => ({ ...c, scenario: { ...c.scenario, name: e.target.value } }))
            }
          />
        </Field>
      </section>

      <section>
        <SectionTitle
          title="排几天"
          hint={`1–${MAX_DAYS} 天。天数变化时看板列数随之变化，不需要你做别的事`}
          right={
            <Stepper
              value={days.length}
              min={1}
              max={MAX_DAYS}
              suffix="天"
              label="周期天数"
              onChange={(v) => onUpdate((c) => setDayCount(c, v))}
            />
          }
        />
        <div className="rounded-lg border border-line bg-white px-3 py-2.5">
          <Slider
            value={days.length}
            min={1}
            max={MAX_DAYS}
            ariaLabel="周期天数滑杆"
            onChange={(v) => onUpdate((c) => setDayCount(c, v))}
          />
          <div className="mt-1 flex justify-between text-[10px] text-mut-2">
            <span>1 天</span>
            <span>7 天（一周）</span>
            <span>{MAX_DAYS} 天（双周）</span>
          </div>

          <div
            className="mt-3 grid gap-1.5"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(126px, 1fr))' }}
          >
            {days.map((d, i) => (
              <div
                key={d.id}
                className={cn(
                  'rounded-lg border px-2 py-1.5',
                  d.peak ? 'border-teal-200 bg-teal-50/60' : 'border-line bg-soft',
                )}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-[10px] text-mut-2">第 {i + 1} 天</span>
                  <ToggleChip
                    selected={d.peak}
                    title={d.peak ? '取消高峰日' : '标为高峰日（人数下限走「高峰」那一档）'}
                    onClick={() => onUpdate((c) => updateDay(c, d.id, { peak: !d.peak }))}
                  >
                    高峰日
                  </ToggleChip>
                </div>
                <Field error={errorOf(`day:${d.id}.label`)}>
                  <TextInput
                    value={d.label}
                    aria-label={`第 ${i + 1} 天显示名`}
                    size="sm"
                    invalid={toneOf(errorOf(`day:${d.id}.label`))}
                    onChange={(e) => onUpdate((c) => updateDay(c, d.id, { label: e.target.value }))}
                  />
                </Field>
              </div>
            ))}
          </div>
          <IssueLine issue={errorOf('scenario.days')} />
        </div>
        <WhyNote>
          「高峰日」取代了原来写死的「周末加人」。是否加人、加到几个人由第三步的人数下限规则决定，
          这里只负责标出哪几天是高峰 —— 当前 {peakCount} 天。
        </WhyNote>
      </section>

      <section>
        <SectionTitle
          title="每天有哪些班"
          hint={`最多 ${MAX_SHIFTS} 个班次。结束时间 ≤ 开始时间视为跨夜到次日，工时自动回算`}
          right={
            <Button
              size="sm"
              variant="outline"
              disabled={shifts.length >= MAX_SHIFTS}
              title={shifts.length >= MAX_SHIFTS ? `最多 ${MAX_SHIFTS} 个班次` : '新增一个班次'}
              onClick={() => onUpdate((c) => addShift(c))}
            >
              <Plus size={12} />
              新增班次
            </Button>
          }
        />
        <div className="space-y-1.5">
          {shifts.map((s, i) => {
            const overnight = isOvernight(s);
            return (
              <div
                key={s.id}
                className="flex flex-wrap items-start gap-2 rounded-lg border border-line bg-white px-2.5 py-2"
              >
                <span className="mt-2 font-mono text-[10px] text-mut-2">{i + 1}</span>
                <Field label="班次名称" error={errorOf(`shift:${s.id}.name`)} className="w-[132px]">
                  <TextInput
                    value={s.name}
                    aria-label={`班次 ${i + 1} 名称`}
                    invalid={toneOf(errorOf(`shift:${s.id}.name`))}
                    onChange={(e) => onUpdate((c) => updateShift(c, s.id, { name: e.target.value }))}
                  />
                </Field>
                <Field label="开始" error={errorOf(`shift:${s.id}.start`)} className="w-[104px]">
                  <TextInput
                    type="time"
                    value={s.start}
                    aria-label={`${s.name || `班次 ${i + 1}`} 开始时间`}
                    invalid={toneOf(errorOf(`shift:${s.id}.start`))}
                    onChange={(e) => onUpdate((c) => updateShift(c, s.id, { start: e.target.value }))}
                  />
                </Field>
                <Field label="结束" error={errorOf(`shift:${s.id}.end`)} className="w-[104px]">
                  <TextInput
                    type="time"
                    value={s.end}
                    aria-label={`${s.name || `班次 ${i + 1}`} 结束时间`}
                    invalid={toneOf(errorOf(`shift:${s.id}.end`))}
                    onChange={(e) => onUpdate((c) => updateShift(c, s.id, { end: e.target.value }))}
                  />
                </Field>
                <div className="mt-[18px] flex items-center gap-1.5">
                  <Badge tone="neutral" icon={<Clock size={10} />}>
                    {s.hours} 小时
                  </Badge>
                  {overnight ? (
                    <Badge tone="pend" icon={<Moon size={10} />}>
                      跨夜到次日
                    </Badge>
                  ) : null}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto mt-[18px]"
                  disabled={shifts.length <= 1}
                  title={shifts.length <= 1 ? '至少保留 1 个班次' : `删除「${s.name}」`}
                  onClick={() => onUpdate((c) => removeShift(c, s.id))}
                >
                  <Trash2 size={12} />
                  删除
                </Button>
              </div>
            );
          })}
        </div>
        <IssueLine issue={errorOf('scenario.shifts')} />
      </section>

      <section>
        <SectionTitle
          title="维度预览"
          hint="格子里的数字是该班的人数下限（来自第三步的规则），看板会按这个形状渲染"
          right={
            <Badge tone="teal" icon={<CalendarRange size={10} />}>
              {days.length} 天 × {shifts.length} 班 = {days.length * shifts.length} 格
            </Badge>
          }
        />
        <div className="-mx-1 overflow-x-auto px-1 pb-1 scrollbar-thin">
          <div
            className="grid gap-1"
            style={{ gridTemplateColumns: `58px repeat(${days.length}, minmax(52px, 1fr))` }}
          >
            <div />
            {days.map((d) => (
              <div
                key={d.id}
                className={cn(
                  'truncate rounded-md px-1 pb-0.5 text-center text-[10.5px] font-semibold',
                  d.peak ? 'bg-teal-50 text-teal-800' : 'text-mut',
                )}
                title={d.peak ? `${d.label}（高峰日）` : d.label}
              >
                {d.label}
              </div>
            ))}
            {shifts.map((s) => (
              <div key={s.id} className="contents">
                <div
                  className="flex flex-col items-center justify-center rounded-md border border-line-2 bg-soft px-1 py-1"
                  title={timeLabel(s)}
                >
                  <b className="truncate text-[10.5px] font-semibold text-ink-2">{s.name}</b>
                  <i className="text-[8.5px] not-italic text-mut-2">{s.start}</i>
                </div>
                {days.map((d) => {
                  const min = minRequiredFor(config, d.id, s.id);
                  return (
                    <div
                      key={`${d.id}-${s.id}`}
                      className={cn(
                        'flex h-[30px] items-center justify-center rounded-md border text-[11px] font-semibold tabular-nums',
                        min === 0
                          ? 'border-dashed border-line text-mut-2'
                          : d.peak
                            ? 'border-teal-200 bg-white text-teal-800'
                            : 'border-line bg-white text-ink-2',
                      )}
                      title={`${d.label} ${s.name}：至少 ${min} 人`}
                    >
                      ≥{min}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

/** 不依附于单个输入框的问题（天数上限、班次数量）单独一行提示 */
function IssueLine({ issue }: { issue: FieldIssue | null }) {
  if (!issue) return null;
  return (
    <p
      className={cn(
        'mt-1.5 text-[10.5px]',
        issue.level === 'warn' ? 'text-pend-deep' : 'text-fail',
      )}
    >
      {issue.message}
    </p>
  );
}
