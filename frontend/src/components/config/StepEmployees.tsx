import { useMemo, useState } from 'react';
import {
  ChevronDown,
  Search,
  ToggleLeft,
  ToggleRight,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react';
import type { EmployeeDef, SchedulerConfig } from '../../types';
import { globalMaxShifts } from '../../config/derive';
import {
  addEmployee,
  addRole,
  addSkill,
  clearUnavailable,
  removeEmployees,
  removeSkill,
  setEmployeesActive,
  skillUsage,
  toggleUnavailableCell,
  toggleUnavailableDay,
  updateEmployee,
} from '../../config/edit';
import { cn } from '../../lib/utils';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import {
  Checkbox,
  Field,
  NumberInput,
  Select,
  Switch,
  TextInput,
  toneOf,
  type FieldIssue,
} from '../ui/Form';
import { DictEditor, SectionTitle, ToggleChip, WhyNote } from './parts';
import { UnavailableGrid } from './UnavailableGrid';

/**
 * 第二步：员工名单。
 *
 * 版式取舍：一行一人 + 展开详情，而不是把不可排班网格、技能多选全部塞进行内。
 * 20 人的名单如果每行都铺开网格，页面会长到无法浏览；而「谁在名单里、启用没有」
 * 这类总览信息恰恰需要一屏能扫完。
 */

type Filter = 'all' | 'active' | 'inactive';

const COLS = '26px 88px 104px 104px minmax(150px,1fr) 92px 84px 48px 64px';

export function StepEmployees({
  config,
  errorOf,
  onUpdate,
}: {
  config: SchedulerConfig;
  errorOf: (target: string) => FieldIssue | null;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string[] | null>(null);

  const { days, shifts } = config.scenario;
  const globalCap = globalMaxShifts(config);
  const activeCount = config.employees.filter((e) => e.active).length;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return config.employees.filter((e) => {
      if (filter === 'active' && !e.active) return false;
      if (filter === 'inactive' && e.active) return false;
      if (!q) return true;
      return `${e.id} ${e.name} ${e.role} ${e.skills.join(' ')}`.toLowerCase().includes(q);
    });
  }, [config.employees, filter, query]);

  const visibleIds = visible.map((e) => e.id);
  const selectedVisible = visibleIds.filter((id) => selected.has(id));
  const allVisibleSelected = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;

  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const clearSelection = () => setSelected(new Set());

  const bulkActive = (active: boolean) => {
    onUpdate((c) => setEmployeesActive(c, selectedVisible, active));
    clearSelection();
  };

  return (
    <div className="space-y-4">
      <section>
        <SectionTitle
          title="角色与技能字典"
          hint="员工的角色、技能只能从字典里选。这就是「技能只能来自员工档案」这条内建规则的配置化形态"
        />
        <div className="grid gap-2 md:grid-cols-2">
          <DictEditor
            label="角色字典"
            items={config.role_pool}
            addPlaceholder="新角色，例如 夜班主管"
            onAdd={(v) => onUpdate((c) => addRole(c, v))}
            removeBlockReason={(role) => {
              const used = config.employees.filter((e) => e.role === role).length;
              return used > 0 ? `还有 ${used} 名员工是这个角色，先改掉他们的角色再删` : null;
            }}
            onRemove={(role) =>
              onUpdate((c) => ({ ...c, role_pool: c.role_pool.filter((r) => r !== role) }))
            }
          />
          <DictEditor
            label="技能字典"
            hint="删除前需要先解除员工与规则的引用，避免静默改变排班能力"
            items={config.skill_pool}
            addPlaceholder="新技能，例如 烘焙"
            onAdd={(v) => onUpdate((c) => addSkill(c, v))}
            removeBlockReason={(skill) => {
              const u = skillUsage(config, skill);
              if (u.employees > 0) return `还有 ${u.employees} 名员工具备该技能`;
              if (u.rules > 0) return `还有 ${u.rules} 条规则在引用它`;
              return null;
            }}
            onRemove={(skill) => onUpdate((c) => removeSkill(c, skill))}
          />
        </div>
      </section>

      <section>
        <SectionTitle
          title="员工名单"
          hint="停用不等于删除：停用的人不参与排班，但档案保留，历史排班里的工号仍读得懂"
          right={
            <Badge tone="teal" icon={<Users size={10} />}>
              启用 {activeCount} / 共 {config.employees.length} 人
            </Badge>
          }
        />

        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="flex h-8 min-w-[188px] flex-1 items-center gap-1.5 rounded-md border border-line bg-white px-2">
            <Search size={12} className="flex-none text-mut-2" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索工号 / 姓名 / 角色 / 技能"
              aria-label="搜索员工"
              className="w-full bg-transparent text-[12px] text-ink outline-none placeholder:text-mut-2"
            />
          </span>
          <span className="flex items-center gap-1">
            {(
              [
                ['all', `全部 ${config.employees.length}`],
                ['active', `启用 ${activeCount}`],
                ['inactive', `停用 ${config.employees.length - activeCount}`],
              ] as Array<[Filter, string]>
            ).map(([value, label]) => (
              <ToggleChip key={value} selected={filter === value} onClick={() => setFilter(value)}>
                {label}
              </ToggleChip>
            ))}
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              onUpdate((c) => {
                const { config: next, id } = addEmployee(c);
                setExpanded(id);
                return next;
              })
            }
          >
            <UserPlus size={12} />
            新增员工
          </Button>
        </div>

        {selectedVisible.length > 0 ? (
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-teal-200 bg-teal-50/70 px-2.5 py-1.5">
            <b className="text-[11.5px] font-semibold text-teal-900">
              已选 {selectedVisible.length} 人
            </b>
            <Button size="sm" variant="secondary" onClick={() => bulkActive(true)}>
              <ToggleRight size={12} />
              批量启用
            </Button>
            <Button size="sm" variant="secondary" onClick={() => bulkActive(false)}>
              <ToggleLeft size={12} />
              批量停用
            </Button>
            <Button size="sm" variant="danger" onClick={() => setConfirmDelete(selectedVisible)}>
              <Trash2 size={12} />
              批量删除
            </Button>
            <button
              type="button"
              onClick={clearSelection}
              className="ml-auto text-[11px] text-mut underline-offset-2 hover:underline"
            >
              取消选择
            </button>
          </div>
        ) : null}

        <div className="overflow-x-auto rounded-lg border border-line bg-white scrollbar-thin">
          <div className="min-w-[820px]">
            <div
              className="grid items-center gap-2 border-b border-line-2 bg-soft px-2.5 py-1.5 text-[10.5px] font-semibold text-mut"
              style={{ gridTemplateColumns: COLS }}
            >
              <Checkbox
                checked={allVisibleSelected}
                indeterminate={selectedVisible.length > 0 && !allVisibleSelected}
                label="全选当前筛选结果"
                onChange={(v) => setSelected(v ? new Set(visibleIds) : new Set())}
              />
              <span>工号</span>
              <span>姓名</span>
              <span>角色</span>
              <span>技能</span>
              <span>不可排班</span>
              <span title="留空表示跟随「每人周期内最多几个班」规则">个人上限</span>
              <span>启用</span>
              <span />
            </div>

            {visible.length === 0 ? (
              <p className="px-3 py-6 text-center text-[12px] text-mut">
                没有符合条件的员工。{query ? '试试清空搜索词，' : ''}或点右上角「新增员工」。
              </p>
            ) : null}

            {visible.map((e) => (
              <EmployeeRow
                key={e.id}
                employee={e}
                config={config}
                globalCap={globalCap}
                selected={selected.has(e.id)}
                expanded={expanded === e.id}
                errorOf={errorOf}
                onToggleSelect={() => toggleSelect(e.id)}
                onToggleExpand={() => setExpanded((prev) => (prev === e.id ? null : e.id))}
                onDelete={() => setConfirmDelete([e.id])}
                onUpdate={onUpdate}
              />
            ))}
          </div>
        </div>

        <WhyNote>
          「不可排班」把请假与固定不可用合并成了一个维度，并且精确到班次 ——
          原来只能表达「整天不可用」，三班制门店的「只能上早班」就写不出来。
          第 {days.length} 天 × {shifts.length} 班的网格在展开行里点两下就能设好。
        </WhyNote>
      </section>

      <Dialog
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        title={`删除 ${confirmDelete?.length ?? 0} 名员工？`}
        subtitle="删除后档案不再保留，历史排班里的这些工号会显示为「不在员工档案中」。如果只是暂时不排班，用「停用」更合适。"
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(null)}>
              取消
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                onUpdate((c) => setEmployeesActive(c, confirmDelete ?? [], false));
                clearSelection();
                setConfirmDelete(null);
              }}
            >
              改为停用
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                onUpdate((c) => removeEmployees(c, confirmDelete ?? []));
                clearSelection();
                setConfirmDelete(null);
              }}
            >
              <Trash2 size={12} />
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-[12px] leading-relaxed text-ink-2">
          即将删除：
          <span className="font-mono">{(confirmDelete ?? []).join('、')}</span>
        </p>
      </Dialog>
    </div>
  );
}

function EmployeeRow({
  employee,
  config,
  globalCap,
  selected,
  expanded,
  errorOf,
  onToggleSelect,
  onToggleExpand,
  onDelete,
  onUpdate,
}: {
  employee: EmployeeDef;
  config: SchedulerConfig;
  globalCap: number | null;
  selected: boolean;
  expanded: boolean;
  errorOf: (target: string) => FieldIssue | null;
  onToggleSelect: () => void;
  onToggleExpand: () => void;
  onDelete: () => void;
  onUpdate: (fn: (c: SchedulerConfig) => SchedulerConfig) => void;
}) {
  const e = employee;
  const { days, shifts } = config.scenario;
  const blockedDays = new Set(e.unavailable.map((u) => u.day)).size;
  // 折叠态只展示一条：硬错误优先，避免「本周期只有 N 个班」这种提醒把整行标成红的
  const rowIssues = (
    ['id', 'name', 'role', 'skills', 'max_shifts', 'unavailable'] as const
  ).flatMap((f) => errorOf(`emp:${e.id}.${f}`) ?? []);
  const rowError = rowIssues.find((i) => i.level === 'error') ?? rowIssues[0] ?? null;
  const rowTone = rowError?.level === 'warn' ? 'text-pend-deep' : 'text-fail';
  const unknownSkills = e.skills.filter((s) => !config.skill_pool.includes(s));

  return (
    <div className={cn('border-b border-line-2 last:border-0', !e.active && 'bg-soft/70')}>
      <div className="grid items-center gap-2 px-2.5 py-1.5" style={{ gridTemplateColumns: COLS }}>
        <Checkbox checked={selected} label={`选择 ${e.id}`} onChange={onToggleSelect} />

        <TextInput
          value={e.id}
          aria-label={`${e.id} 工号`}
          size="sm"
          className="font-mono"
          invalid={toneOf(errorOf(`emp:${e.id}.id`))}
          onChange={(ev) => onUpdate((c) => updateEmployee(c, e.id, { id: ev.target.value }))}
        />

        <TextInput
          value={e.name}
          aria-label={`${e.id} 姓名`}
          placeholder="姓名"
          size="sm"
          invalid={toneOf(errorOf(`emp:${e.id}.name`))}
          onChange={(ev) => onUpdate((c) => updateEmployee(c, e.id, { name: ev.target.value }))}
        />

        <Select
          value={e.role}
          aria-label={`${e.id} 角色`}
          size="sm"
          invalid={toneOf(errorOf(`emp:${e.id}.role`))}
          onChange={(ev) => onUpdate((c) => updateEmployee(c, e.id, { role: ev.target.value }))}
        >
          <option value="">未选择</option>
          {config.role_pool.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
          {e.role && !config.role_pool.includes(e.role) ? (
            <option value={e.role}>{e.role}（不在字典）</option>
          ) : null}
        </Select>

        <button
          type="button"
          onClick={onToggleExpand}
          title="点击展开，勾选技能"
          className="flex min-w-0 flex-wrap items-center gap-1 rounded-md px-1 py-0.5 text-left transition-colors duration-150 hover:bg-teal-50 focus-ring"
        >
          {e.skills.length === 0 ? (
            <span className="text-[11px] text-mut-2">未设置技能</span>
          ) : (
            e.skills.map((s) => (
              <span
                key={s}
                className={cn(
                  'rounded-full border px-1.5 text-[10px] leading-[16px]',
                  config.skill_pool.includes(s)
                    ? 'border-line bg-soft text-ink-2'
                    : 'border-fail-border bg-fail-bg text-fail',
                )}
              >
                {s}
              </span>
            ))
          )}
        </button>

        <button
          type="button"
          onClick={onToggleExpand}
          className={cn(
            'rounded-md border px-1.5 py-[3px] text-[10.5px] font-semibold transition-colors duration-150 focus-ring',
            // 不可排班是正常事实（请假/固定不可用），不是错误：红色要留给「必须修」，
            // 否则一屏 20 行红标签，真正的重复工号反而看不见
            blockedDays > 0
              ? 'border-pend-border bg-pend-bg text-pend-deep hover:border-pend'
              : 'border-line bg-white text-mut hover:border-teal-300 hover:text-teal-800',
          )}
          title={blockedDays > 0 ? '展开查看/修改不可排班时段' : '点击设置不可排班时段'}
        >
          {blockedDays > 0 ? `${blockedDays} 天有限制` : '全周期可排'}
        </button>

        <NumberInput
          value={e.max_shifts}
          min={0}
          max={days.length * shifts.length}
          aria-label={`${e.id} 个人班次上限`}
          placeholder={globalCap === null ? '不限' : `全局 ${globalCap}`}
          invalid={toneOf(errorOf(`emp:${e.id}.max_shifts`))}
          size="sm"
          onChange={(v) => onUpdate((c) => updateEmployee(c, e.id, { max_shifts: v }))}
        />

        <Switch
          checked={e.active}
          size="sm"
          label={`${e.id} ${e.active ? '已启用，点击停用' : '已停用，点击启用'}`}
          onChange={(v) => onUpdate((c) => updateEmployee(c, e.id, { active: v }))}
        />

        <span className="flex items-center justify-end gap-0.5">
          <button
            type="button"
            onClick={onToggleExpand}
            aria-label={`${expanded ? '收起' : '展开'} ${e.id} 详情`}
            title={expanded ? '收起详情' : '展开详情（技能 / 偏好 / 不可排班）'}
            className="rounded-md p-1 text-mut-2 transition-colors duration-150 hover:bg-line-2 hover:text-ink-2 focus-ring"
          >
            <ChevronDown size={13} className={cn('transition-transform duration-150', expanded && 'rotate-180')} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={`删除 ${e.id}`}
            title="删除这名员工"
            className="rounded-md p-1 text-mut-2 transition-colors duration-150 hover:bg-fail-bg hover:text-fail focus-ring"
          >
            <Trash2 size={13} />
          </button>
        </span>
      </div>

      {rowError && !expanded ? (
        <p className={cn('px-2.5 pb-1.5 text-[10.5px]', rowTone)}>{rowError.message}</p>
      ) : null}

      {expanded ? (
        <div className="space-y-3 border-t border-dashed border-line-2 bg-soft/60 px-3 py-2.5">
          {rowError ? (
            <p className={cn('text-[11px] font-semibold', rowTone)}>{rowError.message}</p>
          ) : null}

          <div>
            <p className="mb-1 text-[11px] font-semibold text-mut">
              技能
              <span className="ml-1 font-normal text-mut-2">点击切换，只能从技能字典里选</span>
            </p>
            <div className="flex flex-wrap gap-1.5">
              {config.skill_pool.map((skill) => {
                const on = e.skills.includes(skill);
                return (
                  <ToggleChip
                    key={skill}
                    selected={on}
                    onClick={() =>
                      onUpdate((c) =>
                        updateEmployee(c, e.id, {
                          skills: on ? e.skills.filter((s) => s !== skill) : [...e.skills, skill],
                        }),
                      )
                    }
                  >
                    {skill}
                  </ToggleChip>
                );
              })}
              {unknownSkills.map((skill) => (
                <ToggleChip
                  key={skill}
                  tone="pend"
                  selected
                  title="这个技能不在字典里（可能来自导入的旧配置），点击移除"
                  onClick={() =>
                    onUpdate((c) =>
                      updateEmployee(c, e.id, { skills: e.skills.filter((s) => s !== skill) }),
                    )
                  }
                >
                  {skill} · 字典外
                </ToggleChip>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-start gap-4">
            <div>
              <p className="mb-1 text-[11px] font-semibold text-mut">
                偏好班次
                <span className="ml-1 font-normal text-mut-2">软偏好，不满足不算违规</span>
              </p>
              <div className="flex flex-wrap gap-1.5">
                {shifts.map((s) => {
                  const on = e.preferred_shifts.includes(s.id);
                  return (
                    <ToggleChip
                      key={s.id}
                      selected={on}
                      onClick={() =>
                        onUpdate((c) =>
                          updateEmployee(c, e.id, {
                            preferred_shifts: on
                              ? e.preferred_shifts.filter((x) => x !== s.id)
                              : [...e.preferred_shifts, s.id],
                          }),
                        )
                      }
                    >
                      {s.name}
                    </ToggleChip>
                  );
                })}
              </div>
            </div>

            <Field
              label="个人班次上限"
              hint={globalCap === null ? '留空 = 不限制' : `留空 = 跟随全局规则（${globalCap} 个班）`}
              error={errorOf(`emp:${e.id}.max_shifts`)}
              className="w-[168px]"
            >
              <NumberInput
                value={e.max_shifts}
                min={0}
                max={days.length * shifts.length}
                suffix="个班"
                aria-label={`${e.id} 个人班次上限（详情）`}
                placeholder={globalCap === null ? '不限' : `全局 ${globalCap}`}
                onChange={(v) => onUpdate((c) => updateEmployee(c, e.id, { max_shifts: v }))}
              />
            </Field>
          </div>

          <UnavailableGrid
            employee={e}
            days={days}
            shifts={shifts}
            onToggleCell={(day, shift) => onUpdate((c) => toggleUnavailableCell(c, e.id, day, shift))}
            onToggleDay={(day) => onUpdate((c) => toggleUnavailableDay(c, e.id, day))}
            onClear={() => onUpdate((c) => clearUnavailable(c, e.id))}
          />
        </div>
      ) : null}
    </div>
  );
}
