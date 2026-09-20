import type { Capacity, SchedulerConfig } from '../../types';
import { perDayCapacity } from '../../config/derive';
import { cn, slotKey } from '../../lib/utils';

/**
 * 供给 vs 需求矩阵。
 *
 * 这张表是配置自检里最有说服力的一块：把「这一格要几个人 / 这一格有几个人能来」并排放，
 * 瓶颈格子自己会跳出来。纯文字的 error 列表说不清「到底是哪几天排不开」。
 *
 * 一个人一天顶不了所有班时（`one_shift_per_day` 开着，或班次时间本来就重叠），
 * 最下面多一行**按天合计**：当天能排的人次上界是「人头数 × 一人一天最多几个班」，
 * 于是会出现「每格都够、当天却排不开」（error code `daily_capacity_lt_demand`）。
 * 那一行就是这条 error 的可视化解释。
 */
export function CapacityMatrix({
  config,
  capacity,
}: {
  config: SchedulerConfig;
  capacity: Capacity;
}) {
  const { days, shifts } = config.scenario;
  const bySlot = new Map(capacity.per_slot.map((s) => [slotKey(s.day, s.shift), s]));
  const perDay = perDayCapacity(config);
  const cover = perDay[0]?.cover ?? 1;
  // 一人一天能顶下所有班时这一行恒等于各格之和，只是噪音，不画
  const showDaily = cover < shifts.length;
  const byDay = new Map(perDay.map((d) => [d.day, d]));

  return (
    <div>
      <div className="-mx-0.5 overflow-x-auto px-0.5 pb-1 scrollbar-thin">
        <div
          className="grid gap-1"
          style={{ gridTemplateColumns: `58px repeat(${days.length}, minmax(50px, 1fr))` }}
        >
          <div />
          {days.map((d) => (
            <div
              key={d.id}
              className={cn(
                'truncate rounded-md px-1 text-center text-[10px] font-semibold',
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
                className="flex items-center justify-center truncate rounded-md border border-line-2 bg-soft px-1 py-1 text-[10px] font-semibold text-ink-2"
                title={`${s.name} ${s.start}–${s.end}`}
              >
                {s.name}
              </div>
              {days.map((d) => {
                const cell = bySlot.get(slotKey(d.id, s.id));
                const min = cell?.min_required ?? 0;
                const eligible = cell?.eligible ?? 0;
                const short = min > 0 && eligible < min;
                const tight = min > 0 && eligible === min;
                return (
                  <div
                    key={`${d.id}-${s.id}`}
                    title={`${d.label} ${s.name}：需要 ${min} 人，可排 ${eligible} 人${
                      short ? '（排不出来）' : tight ? '（零冗余）' : ''
                    }`}
                    className={cn(
                      'flex flex-col items-center justify-center rounded-md border py-0.5 text-[10.5px] tabular-nums',
                      short
                        ? 'border-fail-border bg-fail-bg text-fail'
                        : tight
                          ? 'border-pend-border bg-pend-bg text-pend-deep'
                          : 'border-line bg-white text-ink-2',
                    )}
                  >
                    <b className="font-semibold leading-4">{eligible}</b>
                    <i className="text-[9px] not-italic leading-3 text-mut-2">需 {min}</i>
                  </div>
                );
              })}
            </div>
          ))}

          {showDaily ? (
            <div className="contents">
              <div
                className="flex items-center justify-center truncate rounded-md border border-line-2 bg-soft px-1 py-1 text-[10px] font-semibold text-mut"
                title={`一人一天最多 ${cover} 个班，所以当天能排的人次上界 = 当天人头数 × ${cover}`}
              >
                当天合计
              </div>
              {days.map((d) => {
                const sum = byDay.get(d.id);
                const demand = sum?.demand ?? 0;
                const supply = sum?.capacity ?? 0;
                const short = demand > supply;
                const tight = demand > 0 && demand === supply;
                return (
                  <div
                    key={`total-${d.id}`}
                    title={`${d.label}：当天各班共需 ${demand} 人次，全天可排班 ${
                      sum?.available ?? 0
                    } 人 × 一人一天 ${cover} 个班 = 最多 ${supply} 人次${
                      short ? '（必定无解）' : tight ? '（一个人都不能请假）' : ''
                    }`}
                    className={cn(
                      'flex flex-col items-center justify-center rounded-md border border-dashed py-0.5 text-[10.5px] tabular-nums',
                      short
                        ? 'border-fail-border bg-fail-bg text-fail'
                        : tight
                          ? 'border-pend-border bg-pend-bg text-pend-deep'
                          : 'border-line bg-soft text-mut',
                    )}
                  >
                    <b className="font-semibold leading-4">{supply}</b>
                    <i className="text-[9px] not-italic leading-3 text-mut-2">需 {demand}</i>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-mut-2">
        <span>上方数字 = 该格可排班人数，下方 = 人数下限</span>
        <span className="inline-flex items-center gap-1">
          <span className="h-[9px] w-[9px] rounded-[3px] border border-fail-border bg-fail-bg" />
          可排人数不够，必定无解
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-[9px] w-[9px] rounded-[3px] border border-pend-border bg-pend-bg" />
          刚好卡住，没有替换余地
        </span>
        {showDaily ? (
          <span>末行 = 当天能排的人次上界（人头数 × 一人一天最多 {cover} 个班） vs 当天需求</span>
        ) : null}
      </div>
    </div>
  );
}
