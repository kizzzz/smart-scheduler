import { BadgeCheck, Coins, Lightbulb, MapPin, Stamp } from 'lucide-react';
import type { Infeasible } from '../types';
import { Badge } from './ui/Badge';

export function InfeasibleCard({
  infeasible,
  ruleCount = null,
}: {
  infeasible: Infeasible;
  /** 当前生效的硬规则条数；缺省时不提数字，也好过提一个写死的 9 */
  ruleCount?: number | null;
}) {
  const title = infeasible.proven ? '本周期无可行解' : '本次未找到可行解';
  const claim = infeasible.proven
    ? `已用计数下界证明：在当前约束下不存在任何满足${ruleCount ? ` ${ruleCount} 条` : ''}硬规则的排班。`
    : '在本次搜索预算内没有找到可行解，但不能断言一定无解 —— 放宽任一约束或重试都可能找到方案。';

  return (
    <section className="space-y-3">
      <div className="rounded-card border-[1.5px] border-pend-border bg-pend-bg px-4 py-3.5">
        <div className="flex items-center gap-2">
          <Lightbulb size={16} className="flex-none text-pend" />
          <h3 className="text-[14.5px] font-semibold text-pend-deep">{title}</h3>
          <div className="ml-auto flex items-center gap-2">
            <Badge tone="pend">{infeasible.proven ? '已证明无解（proven）' : '未证明无解（预算内未找到）'}</Badge>
            <Badge tone="pend">最小冲突集已定位</Badge>
          </div>
        </div>

        <p className="mt-2 text-[12.5px] leading-relaxed text-[#7C4F0C]">{infeasible.summary}</p>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-[#8A6017]">{claim}</p>

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-[#8A6017]">最小冲突集</span>
          {infeasible.min_conflict_set.map((r) => (
            <code
              key={r}
              className="rounded border border-pend-border bg-white px-1.5 py-[1px] font-mono text-[11px] font-semibold text-pend-deep"
            >
              {r}
            </code>
          ))}
          {infeasible.conflict_slot ? (
            <span className="inline-flex items-center gap-1 rounded border border-pend-border bg-white px-1.5 py-[1px] text-[11px] font-semibold text-pend-deep">
              <MapPin size={10} />
              冲突位置：{infeasible.conflict_slot}
            </span>
          ) : null}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {infeasible.unlock_paths.map((p, i) => (
          <div
            key={p.title}
            className="flex flex-col rounded-card border border-line bg-white px-3.5 py-3 shadow-card transition-colors duration-150 hover:border-teal-200"
          >
            <div className="flex items-center gap-2">
              <span className="flex-none rounded border border-teal-200 bg-teal-50 px-1.5 text-[10px] font-semibold leading-[17px] text-teal-800">
                {['一', '二', '三'][i] ?? i + 1}
              </span>
              <b className="truncate text-[12.5px] font-semibold text-ink">{p.title}</b>
            </div>
            <p className="mt-1.5 flex-1 text-[11.5px] leading-relaxed text-mut">{p.detail}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {p.extra_cost ? (
                <Badge tone="pend" icon={<Coins size={10} />}>
                  {p.extra_cost}
                </Badge>
              ) : (
                <Badge tone="neutral">无额外成本</Badge>
              )}
              {p.needs_approval ? (
                <Badge tone="pend" icon={<Stamp size={10} />}>
                  需督导审批
                </Badge>
              ) : (
                <Badge tone="pass" icon={<BadgeCheck size={10} />}>
                  店长可自行决定
                </Badge>
              )}
            </div>
          </div>
        ))}
      </div>

      <p className="text-[11px] text-mut-2">无解时给出可执行的解锁路径，而不是让店长自己猜。</p>
    </section>
  );
}
