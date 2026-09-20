import { Crown } from 'lucide-react';
import type { Employee } from '../types';
import { cn } from '../lib/utils';
import { Tooltip } from './ui/Tooltip';

export const SKILL_MANAGER = '店长值守';
export const SKILL_DRINK = '饮品制作';
export const SKILL_CASHIER = '收银';

export function SkillDot({ kind }: { kind: 'drink' | 'cashier' }) {
  return (
    <span
      className={cn(
        'h-[4px] w-[4px] flex-none rounded-full',
        kind === 'drink' ? 'bg-teal-600' : 'bg-[#8B7BD8]',
      )}
    />
  );
}

function ChipInner({ employee, id }: { employee?: Employee; id: string }) {
  const isManager = employee?.skills.includes(SKILL_MANAGER) ?? false;
  return (
    <>
      {isManager ? <Crown size={9} strokeWidth={2.1} className="flex-none" /> : null}
      {employee?.skills.includes(SKILL_DRINK) ? <SkillDot kind="drink" /> : null}
      {employee?.skills.includes(SKILL_CASHIER) ? <SkillDot kind="cashier" /> : null}
      <span className="tabular-nums">{id}</span>
    </>
  );
}

export function EmployeeChip({
  id,
  employee,
  violating,
  onClick,
  interactive = true,
}: {
  id: string;
  employee?: Employee;
  violating?: boolean;
  onClick?: () => void;
  interactive?: boolean;
}) {
  const isManager = employee?.skills.includes(SKILL_MANAGER) ?? false;

  const tip = employee ? (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 font-semibold">
        {isManager ? <Crown size={10} className="text-teal-200" /> : null}
        {employee.name ? `${employee.name}（${employee.id}）` : employee.id} · {employee.role}
      </div>
      <div className="text-white/75">技能：{employee.skills.join(' / ') || '无'}</div>
      <div className="text-white/75">偏好：{employee.preference || '无特别偏好'}</div>
      <div className="text-white/75">
        可工作：{employee.available_days.join('、') || '（无可用时段）'}
        {employee.leave_days.length ? ` · 请假：${employee.leave_days.join('、')}` : ''}
      </div>
      {employee.max_shifts != null ? (
        <div className="text-white/75">个人班次上限：{employee.max_shifts}</div>
      ) : null}
      {interactive ? <div className="pt-0.5 text-teal-200">点击换人</div> : null}
    </div>
  ) : (
    <span>{id}：不在员工档案中</span>
  );

  return (
    <Tooltip content={tip}>
      <button
        type="button"
        onClick={onClick}
        disabled={!interactive}
        className={cn(
          'inline-flex items-center gap-[3px] rounded-[6px] px-[5px] py-[1px] text-[10.5px] font-semibold leading-[15px] transition-all duration-150',
          interactive ? 'cursor-pointer hover:-translate-y-[1px] hover:shadow-card' : 'cursor-default',
          violating
            ? 'border-[1.5px] border-fail bg-fail-bg text-fail'
            : isManager
              ? 'border-[1.5px] border-teal-800 bg-teal-50 text-teal-800'
              : 'border border-line bg-[#F8FAFA] text-ink-2 hover:border-teal-200',
        )}
      >
        <ChipInner employee={employee} id={id} />
      </button>
    </Tooltip>
  );
}
