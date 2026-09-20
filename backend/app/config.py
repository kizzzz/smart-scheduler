"""排班配置模型与编译视图：维度、员工、规则的唯一入参形态。

与 models.py 的分界：models.py 描述「一次请求内的产物」（意图、排班表、校验报告），
本模块描述「求解与校验的前提」（几天几班、谁能排、按哪些规则）。前者随每条指令变化，
后者由门店配置决定；混在一起的结果就是「换一家门店」要改内核。

两条必须守住的边界：

1. **默认配置不是示例数据，是回归基线。** `default_config()` 逐字转换 data.py 的考题数据，
   省略 config 的请求（老前端）必须与配置化改造前的行为一致，因此这里的每个默认值都被
   测试锁住，不要「顺手优化」。
2. **规则是参数化模板，不是自由文本。** 8 类 type 各自有确定性 checker（见 rules.py），
   LLM 不参与合规判定。新增诉求要么进模板库，要么不做。
"""
from __future__ import annotations

import re
from collections import OrderedDict
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, Field, model_validator

from . import data

# ---------- 规则模板库 ----------

RULE_MIN_STAFF = "min_staff_per_shift"
RULE_REQUIRE_ATTRIBUTE = "require_attribute"
RULE_MAX_SHIFTS = "max_shifts_per_period"
RULE_MAX_CONSECUTIVE = "max_consecutive_days"
RULE_MIN_REST = "min_rest_hours"
RULE_ONE_SHIFT_PER_DAY = "one_shift_per_day"
RULE_RESPECT_UNAVAILABILITY = "respect_unavailability"
RULE_SKILL_INTEGRITY = "skill_source_integrity"

RULE_TYPES: Tuple[str, ...] = (
    RULE_MIN_STAFF,
    RULE_REQUIRE_ATTRIBUTE,
    RULE_MAX_SHIFTS,
    RULE_MAX_CONSECUTIVE,
    RULE_MIN_REST,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_RESPECT_UNAVAILABILITY,
    RULE_SKILL_INTEGRITY,
)

# 数据完整性，不是业务偏好：技能只能来自员工档案、不可用时段不得排班，
# 这两条即使用户在配置里 enabled=false 也照样执行，否则「校验器是唯一真相源」立刻失效。
LOCKED_RULE_TYPES: Tuple[str, ...] = (RULE_RESPECT_UNAVAILABILITY, RULE_SKILL_INTEGRITY)

# 维度上限：56 格是回溯搜索还能在秒级预算内收敛的量级（14 天 × 4 班）。
# 上限不是产品口味，而是「不给用户一个必然超时的选项」。
MAX_DAYS = 14
MAX_SHIFTS = 4
MAX_SLOTS = 56

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def minutes_of(clock: str) -> int:
    """"09:00" → 540。解析不了直接抛：班次时间是求解的度量单位，猜一个默认值只会静默排错班。"""
    m = _TIME_RE.match(str(clock or "").strip())
    if not m:
        raise ValueError(f"班次时间格式应为 HH:MM，收到「{clock}」")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise ValueError(f"班次时间超出范围：{clock}")
    return h * 60 + mi


# ---------- 配置数据模型 ----------


class DayDef(BaseModel):
    id: str
    label: str = ""
    # 高峰日：原「周末加人」的配置化形态。门店的高峰不一定在周末（写字楼店在工作日）
    peak: bool = False

    @model_validator(mode="after")
    def _fill_label(self) -> "DayDef":
        if not self.label:
            self.label = self.id
        return self


class ShiftDef(BaseModel):
    id: str
    name: str = ""
    start: str = "09:00"
    end: str = "17:00"
    # 前端只读：工时是 start/end 的函数，允许两边各自填一个数迟早会对不上
    hours: float = 0.0

    @model_validator(mode="after")
    def _derive(self) -> "ShiftDef":
        if not self.name:
            self.name = self.id
        s, e = minutes_of(self.start), minutes_of(self.end)
        # end <= start 视为跨夜。含 start == end：那是一个 24 小时班，不是 0 小时班
        span = e - s if e > s else e - s + 24 * 60
        self.hours = round(span / 60, 4)
        return self

    def start_minutes(self) -> int:
        return minutes_of(self.start)


class UnavailableSlot(BaseModel):
    day: str
    # None = 当天整日不可用
    shift: Optional[str] = None
    # 契约没有这一位，但旧文案区分「不在可工作日期」与「请假」，而店长看到的正是这句话。
    # 约定：空 = 结构性不可用（兼职只做周末这类），非空 = 具名原因（请假/培训）。
    # 求解器的「岗位灵活度」启发式只看结构性不可用——一次请假不改变一个人本来能上几天班。
    reason: Optional[str] = None


class EmployeeDef(BaseModel):
    id: str
    # 新增字段。默认配置里用工号占位：题面没有姓名，编中文姓名等于凭空造数据
    name: str = ""
    role: str = ""
    skills: List[str] = Field(default_factory=list)
    unavailable: List[UnavailableSlot] = Field(default_factory=list)
    # None = 用全局 max_shifts_per_period 规则
    max_shifts: Optional[int] = None
    preferred_shifts: List[str] = Field(default_factory=list)
    # 停用不删除：历史排班里出现过的人必须还能被读懂
    active: bool = True

    @model_validator(mode="after")
    def _fill_name(self) -> "EmployeeDef":
        if not self.name:
            self.name = self.id
        return self


class ScenarioDef(BaseModel):
    name: str = "门店周排班"
    days: List[DayDef] = Field(default_factory=list)
    shifts: List[ShiftDef] = Field(default_factory=list)


_FALLBACK_LOCKED_IDS = {
    RULE_RESPECT_UNAVAILABILITY: "R-08",
    RULE_SKILL_INTEGRITY: "R-09",
}
# 两条 locked 规则的文案沿用原题面：自动补齐时也要让店长看到熟悉的措辞
_FALLBACK_LOCKED_NAMES = {
    RULE_RESPECT_UNAVAILABILITY: "请假和不可工作日期绝对不得排班",
    RULE_SKILL_INTEGRITY: "技能必须来自员工数据，Agent 不得自行补技能",
}


def describe_rule(rule_type: str, params: dict) -> str:
    """按 type + params 生成一句可读规则文案，用作未命名规则的显示名。

    用户自建的规则常常懒得填 name，直接显示 "C-1" 会让校验面板变成一串代号，
    店长根本不知道自己违反了什么。默认配置的 9 条规则都有原题文案，不走这里。
    """
    p = params or {}
    if rule_type == RULE_MIN_STAFF:
        peak = p.get("peak")
        base = f"每班至少 {p.get('default', 0)} 人"
        return f"{base}（高峰日 {peak} 人）" if peak else base
    if rule_type == RULE_REQUIRE_ATTRIBUTE:
        value = p.get("value") or "指定属性"
        return f"每班至少 {p.get('min', 1)} 人具备「{value}」"
    if rule_type == RULE_MAX_SHIFTS:
        return f"每人本周期最多 {p.get('max', 0)} 个班"
    if rule_type == RULE_MAX_CONSECUTIVE:
        return f"每人最多连续工作 {p.get('max', 0)} 天"
    if rule_type == RULE_MIN_REST:
        return f"相邻班次至少间隔 {p.get('hours', 0)} 小时"
    if rule_type == RULE_ONE_SHIFT_PER_DAY:
        return "每人每天最多一个班"
    return _FALLBACK_LOCKED_NAMES.get(rule_type, "")


class RuleDef(BaseModel):
    id: str
    # 刻意不用 Literal：来自 localStorage 的旧配置可能带上一版的 type，
    # 那时该给出一条能看懂的自检 error（unknown_rule_type），而不是让整个配置页 422 白屏
    type: str
    name: str = ""
    enabled: bool = True
    # true = 系统内建，不可删除/禁用
    locked: bool = False
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill(self) -> "RuleDef":
        if not self.name:
            self.name = describe_rule(self.type, self.params) or self.id
        if self.type in LOCKED_RULE_TYPES:
            self.locked = True
        return self


class SchedulerConfig(BaseModel):
    version: int = 1
    scenario: ScenarioDef = Field(default_factory=ScenarioDef)
    # 技能字典：员工技能只能从这里选（R-09 的配置化形态）
    skill_pool: List[str] = Field(default_factory=list)
    role_pool: List[str] = Field(default_factory=list)
    employees: List[EmployeeDef] = Field(default_factory=list)
    rules: List[RuleDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_locked_rules(self) -> "SchedulerConfig":
        """缺失的 locked 规则直接补齐。

        手写或旧版本的 JSON 很可能没有这两条。缺了不报错而是静默不检查，等价于
        「不可用时段可以排班」，这是最不该出现的失败方式。
        """
        present = {r.type for r in self.rules}
        for rtype in LOCKED_RULE_TYPES:
            if rtype not in present:
                self.rules.append(
                    RuleDef(id=_FALLBACK_LOCKED_IDS[rtype], type=rtype,
                            name=_FALLBACK_LOCKED_NAMES[rtype], locked=True)
                )
        return self

    def fingerprint(self) -> str:
        """内容指纹，用作编译缓存的键。

        刻意不缓存这个字符串：配置是可变对象（测试与 API 都会 model_copy 后就地改），
        一旦把指纹记在实例上，改完配置还会命中旧索引——这是最难查的一类不一致。
        """
        return self.model_dump_json()


# ---------- 默认配置（= 考题数据） ----------


def _default_shifts() -> List[ShiftDef]:
    out: List[ShiftDef] = []
    for name in data.SHIFTS:
        # 时间从 data.SHIFT_TIME 反解，避免同一组 09:00–17:00 在两个文件里各写一遍
        start, end = data.SHIFT_TIME[name].split("–")
        out.append(ShiftDef(id=name, name=name, start=start.strip(), end=end.strip()))
    return out


def _default_employees() -> List[EmployeeDef]:
    pool = [data.SKILL_KEEPER, data.SKILL_DRINK, data.SKILL_CASHIER, data.SKILL_STOCK]
    out: List[EmployeeDef] = []
    for e in data.EMPLOYEES.values():
        unavailable = [
            # 先结构性不可用、再请假：校验只报每格第一条命中的原因，顺序决定了店长看到哪句话，
            # 旧实现也是先判可工作日期再判请假
            UnavailableSlot(day=d) for d in data.DAYS if d not in e.available_days
        ]
        unavailable += [UnavailableSlot(day=d, reason="请假") for d in e.leave_days]
        out.append(EmployeeDef(
            id=e.id,
            name=e.id,          # 题面无姓名字段，用工号占位而不是编一个中文名
            role=e.role,
            skills=[s for s in pool if s in e.skills],
            unavailable=unavailable,
            max_shifts=None,
            preferred_shifts=[e.preference] if e.preference else [],
            active=True,
        ))
    return out


def _default_rules() -> List[RuleDef]:
    """R-01～R-10 逐条映射到模板库。

    R-10 one_shift_per_day 必须在这里显式出现，尽管默认两班（09–17 / 13–21）时间重叠、
    「一天两班」已经被引擎级不变式挡住。理由是可见性与可配置性：旧实现把「一人一天一个班」
    当作隐含假设，用户在配置页把班次改成 08–12 / 12–16 / 16–20（互不重叠）之后，这条假设
    会静默消失，而校验面板里从来没有一行代表它——用户既看不见它，也关不掉它。
    把它写成一条普通规则（enabled、非 locked）后，「一人一天一个班」变成可见、可关的选择，
    也让前端本地兜底配置（10 条）与后端默认配置对齐。
    """
    text = {r["id"]: r["text"] for r in data.RULES}
    return [
        RuleDef(id="R-01", type=RULE_REQUIRE_ATTRIBUTE, name=text["R-01"], params={
            "attr": "skill", "value": data.SKILL_KEEPER, "min": 1,
            # noun 是可选的文案位：默认文案是「{value}技能」，但值守说的是「资格」
            "noun": "店长值守资格",
        }),
        RuleDef(id="R-02", type=RULE_REQUIRE_ATTRIBUTE, name=text["R-02"], params={
            "attr": "skill", "value": data.SKILL_DRINK, "min": 2,
        }),
        RuleDef(id="R-03", type=RULE_REQUIRE_ATTRIBUTE, name=text["R-03"], params={
            "attr": "skill", "value": data.SKILL_CASHIER, "min": 1,
        }),
        RuleDef(id="R-04", type=RULE_MIN_STAFF, name=text["R-04"], params={
            "default": data.MIN_PER_SHIFT_WEEKDAY, "peak": data.MIN_PER_SHIFT_WEEKEND, "overrides": [],
        }),
        RuleDef(id="R-05", type=RULE_MAX_SHIFTS, name=text["R-05"], params={
            "max": data.MAX_SHIFTS_PER_WEEK,
        }),
        RuleDef(id="R-06", type=RULE_MAX_CONSECUTIVE, name=text["R-06"], params={
            "max": data.MAX_CONSECUTIVE_DAYS,
        }),
        # R-07 泛化：旧实现是字符串比较「晚班接次日早班」，只在两班制下成立。
        # 取 13 而不是 12：引擎按「gap < hours 才违规」判（参数叫「最少休息」，等于就该放行），
        # 而旧 R-07 要禁掉的晚班 21:00 → 次日早班 09:00 恰好只有 12 小时，阈值必须高于 12 才拦得住。
        # 13 同时不会误伤其它接班：默认两班制下次小的间隔是 16 小时（早→次日早 / 晚→次日晚）。
        RuleDef(id="R-07", type=RULE_MIN_REST, name=text["R-07"], params={"hours": 13}),
        RuleDef(id="R-08", type=RULE_RESPECT_UNAVAILABILITY, name=text["R-08"], locked=True),
        RuleDef(id="R-09", type=RULE_SKILL_INTEGRITY, name=text["R-09"], locked=True),
        # 排在两条 locked 规则之后：R-01～R-09 的编号和文案来自原题面，R-10 是本次显式化的
        # 新增项，追加在末尾才不会打乱老前端按顺序渲染的规则列表
        RuleDef(id="R-10", type=RULE_ONE_SHIFT_PER_DAY, name="每人每天最多一个班"),
    ]


@lru_cache(maxsize=1)
def _default_singleton() -> SchedulerConfig:
    return SchedulerConfig(
        version=1,
        scenario=ScenarioDef(
            name="门店周排班",
            days=[DayDef(id=d, label=data.DAY_LABELS[d], peak=d in data.WEEKEND) for d in data.DAYS],
            shifts=_default_shifts(),
        ),
        skill_pool=[data.SKILL_KEEPER, data.SKILL_DRINK, data.SKILL_CASHIER, data.SKILL_STOCK],
        role_pool=list(dict.fromkeys(e.role for e in data.EMPLOYEES.values())),
        employees=_default_employees(),
        rules=_default_rules(),
    )


def default_config() -> SchedulerConfig:
    """默认配置的独立副本。

    每次深拷贝是因为调用方（API 回显、测试构造非默认维度）几乎总要就地改它，
    共享单例会让「改一个用例污染下一个用例」这种最难查的 bug 变成常态。
    """
    return _default_singleton().model_copy(deep=True)


# ---------- 编译视图 ----------


class ConfigIndex:
    """把「每次求解/校验都要问的问题」预先算好。

    存在理由是性能：solver.optimize 一次请求里会跑几百遍 validate，如果每次都去遍历
    rules 与 unavailable 列表，配置化的代价会直接落在响应时间上。

    这里只做查询，不做判定：规则语义在 rules.py，索引不许偷偷夹带业务逻辑。
    """

    def __init__(self, config: SchedulerConfig) -> None:
        self.config = config

        self.day_ids: List[str] = [d.id for d in config.scenario.days]
        self.days: Dict[str, DayDef] = {d.id: d for d in config.scenario.days}
        self.day_pos: Dict[str, int] = {d: i for i, d in enumerate(self.day_ids)}
        self.shift_ids: List[str] = [s.id for s in config.scenario.shifts]
        self.shifts: Dict[str, ShiftDef] = {s.id: s for s in config.scenario.shifts}
        self.shift_pos: Dict[str, int] = {s: i for i, s in enumerate(self.shift_ids)}
        # 顺序固定为 天优先（周一早班、周一晚班、周二早班…），前端网格与 diff 都依赖它
        self.slots: List[Tuple[str, str]] = [(d, s) for d in self.day_ids for s in self.shift_ids]

        self.all_employees: Dict[str, EmployeeDef] = {e.id: e for e in config.employees}
        # 只有启用员工进求解池；停用的人仍留在 all_employees 里，历史排班才读得懂
        self.employee_ids: List[str] = [e.id for e in config.employees if e.active]
        self._skills: Dict[str, Set[str]] = {e.id: set(e.skills) for e in config.employees}

        self.rules: List[RuleDef] = list(config.rules)
        # locked 规则即使 enabled=false 也执行：数据完整性不是业务偏好
        self.active_rules: List[RuleDef] = [r for r in self.rules if r.enabled or r.locked]
        self._by_type: Dict[str, List[RuleDef]] = {}
        for r in self.active_rules:
            self._by_type.setdefault(r.type, []).append(r)

        self.one_shift_per_day: bool = bool(self._by_type.get(RULE_ONE_SHIFT_PER_DAY))
        # 三个聚合值都先过 valid_*：非法参数（0、负数、非数字）视为该规则不生效，
        # 这样 solver 与 validator 看到的是同一个 None，不会一个判死一个判合规。
        self.min_rest_hours: Optional[float] = None
        for r in self._by_type.get(RULE_MIN_REST, []):
            hours = valid_hours(r.params.get("hours"))
            if hours is None:
                continue
            self.min_rest_hours = hours if self.min_rest_hours is None else max(self.min_rest_hours, hours)
        self.max_consecutive_days: Optional[int] = None
        for r in self._by_type.get(RULE_MAX_CONSECUTIVE, []):
            mx = valid_limit(r.params.get("max"))
            if mx is None:
                continue
            self.max_consecutive_days = mx if self.max_consecutive_days is None else min(self.max_consecutive_days, mx)
        self._global_max_shifts: Optional[int] = None
        for r in self._by_type.get(RULE_MAX_SHIFTS, []):
            mx = valid_limit(r.params.get("max"))
            if mx is None:
                continue
            self._global_max_shifts = mx if self._global_max_shifts is None else min(self._global_max_shifts, mx)

        # 每格人数下限：多条 min_staff 规则取最严
        self._min_required: Dict[Tuple[str, str], int] = {}
        for day, shift in self.slots:
            need = 0
            for r in self._by_type.get(RULE_MIN_STAFF, []):
                need = max(need, rule_min_staff(r, self.days.get(day), day, shift))
            self._min_required[(day, shift)] = need

        # 不可用索引：整日与按班次分开存，查询时整日优先（它的原因对店长更有信息量）
        self._blocked_day: Dict[Tuple[str, str], UnavailableSlot] = {}
        self._blocked_cell: Dict[Tuple[str, str, str], UnavailableSlot] = {}
        for e in config.employees:
            for u in e.unavailable:
                if u.shift is None:
                    self._blocked_day.setdefault((e.id, u.day), u)
                else:
                    self._blocked_cell.setdefault((e.id, u.day, u.shift), u)

        # 班次绝对时间轴（分钟）：跨夜、跨天的休息间隔都靠它算，避免再出现字符串比较班次名的实现
        self._abs: Dict[Tuple[str, str], Tuple[int, int]] = {}
        for day, shift in self.slots:
            sd = self.shifts[shift]
            base = self.day_pos[day] * 24 * 60 + sd.start_minutes()
            self._abs[(day, shift)] = (base, base + int(round(sd.hours * 60)))

        self._require_rules: List[RuleDef] = list(self._by_type.get(RULE_REQUIRE_ATTRIBUTE, []))
        self._scarce_attr: Optional[Tuple[str, str]] = self._pick_scarce_attribute()
        self._max_shifts_per_day: Optional[int] = None
        self._min_day_gap: Optional[int] = None

    # ---- 维度 ----

    def slot_count(self) -> int:
        return len(self.slots)

    def day_label(self, day: str) -> str:
        d = self.days.get(day)
        return d.label if d else day

    def shift_name(self, shift: str) -> str:
        s = self.shifts.get(shift)
        return s.name if s else shift

    def shift_time_label(self, shift: str) -> str:
        s = self.shifts.get(shift)
        return f"{s.start}–{s.end}" if s else ""

    def slot_label(self, day: str, shift: str) -> str:
        return f"{self.day_label(day)}{self.shift_name(shift)}"

    def is_peak(self, day: str) -> bool:
        d = self.days.get(day)
        return bool(d and d.peak)

    def min_required(self, day: str, shift: str) -> int:
        return self._min_required.get((day, shift), 0)

    # ---- 员工 ----

    def employee(self, eid: str) -> Optional[EmployeeDef]:
        return self.all_employees.get(eid)

    def known(self, eid: str) -> bool:
        return eid in self.all_employees

    def has_skill(self, eid: str, skill: str) -> bool:
        """技能判断的唯一入口：只查员工档案（R-09）。"""
        return skill in self._skills.get(eid, ())

    def has_attribute(self, eid: str, attr: str, value: str) -> bool:
        if attr == "role":
            e = self.all_employees.get(eid)
            return bool(e and e.role == value)
        return self.has_skill(eid, value)

    def attribute_pool(self, attr: str, value: str) -> List[str]:
        return [e for e in self.employee_ids if self.has_attribute(e, attr, value)]

    def max_shifts(self, eid: str) -> Optional[int]:
        """个人上限优先于全局规则；两者都没有 = 不限。"""
        e = self.all_employees.get(eid)
        if e is not None and e.max_shifts is not None:
            return int(e.max_shifts)
        return self._global_max_shifts

    def blocked_by(self, eid: str, day: str, shift: Optional[str]) -> Optional[UnavailableSlot]:
        hit = self._blocked_day.get((eid, day))
        if hit is not None:
            return hit
        if shift is None:
            return None
        return self._blocked_cell.get((eid, day, shift))

    def can_work(self, eid: str, day: str, shift: Optional[str] = None) -> bool:
        """配置层面的可排班性：启用、且该格没有被不可用时段挡住。不含意图里的临时请假。"""
        e = self.all_employees.get(eid)
        if e is None or not e.active:
            return False
        return self.blocked_by(eid, day, shift) is None

    def available_days(self, eid: str) -> List[str]:
        return [d for d in self.day_ids if any(self.can_work(eid, d, s) for s in self.shift_ids)]

    def roster_days(self, eid: str) -> List[str]:
        """「本来能上几天班」：只看结构性不可用，忽略请假这类具名原因。

        求解器用它衡量岗位灵活度——兼职只做周末是长期属性，一次请假不是。
        """
        out: List[str] = []
        for d in self.day_ids:
            hit = self._blocked_day.get((eid, d))
            if hit is not None and not hit.reason:
                continue
            out.append(d)
        return out

    def min_day_gap(self) -> int:
        """两次上班之间至少要隔几天（1 = 可以连着两天上班）。

        min_rest_hours 不只约束「同一天连上两个班」：要求休息 24 小时以上时，隔天上班
        同样不可能，每人的可排天数直接被砍半。容量估算必须知道这件事——否则「全员上限
        之和」会高估供给，把一份求解器能证明无解的配置放行（这正是 daily capacity 那类
        漏洞的另一个面）。

        取**最大可能间隔**来判定：前一天挑最早收工的班，后一天挑最晚开工的班。所以
        「间隔小于 g 天一定违反休息规则」是充分条件，不会误拦一份其实可解的配置。
        """
        if self._min_day_gap is not None:
            return self._min_day_gap
        rest = self.min_rest_hours
        total = len(self.day_ids)
        gap = 1
        if rest and self.shift_ids:
            latest_start = max(self.shifts[s].start_minutes() for s in self.shift_ids)
            earliest_end = min(
                self.shifts[s].start_minutes() + int(round(self.shifts[s].hours * 60))
                for s in self.shift_ids
            )
            need = rest * 60
            # total + 1 = 整个周期内都排不了第二个班
            gap = next(
                (k for k in range(1, total + 1) if k * 24 * 60 + latest_start - earliest_end >= need),
                total + 1,
            )
        self._min_day_gap = gap
        return gap

    def max_work_days(self, eid: str) -> int:
        """该员工在本周期内最多能上几天班（可排天数 ∩ 连班上限 ∩ 休息间隔）。

        两个硬上界都要折进来，容量估算才不会高估供给：

        - **连班上限**：最多连上 k 天就必须歇 1 天，所以 D 天里至少有 ⌊D/(k+1)⌋ 个休息日
          （7 天 / 最多连 5 天 → 最多上 6 天）。
        - **休息间隔**：两次上班至少隔 g 天时，最多只能上 ⌊(D−1)/g⌋+1 天（见 min_day_gap）。

        不折进来时，「全员上限之和」会是一个谁都达不到的数字，把必然无解的配置放行。
        非法参数（max ≤ 0 等）已经在 ConfigIndex 构造时归零成「规则不生效」，这里看到的
        上限一定是有效值。
        """
        days = len(self.available_days(eid))
        total = len(self.day_ids)
        gap = self.min_day_gap()
        if gap > 1:
            days = min(days, (total - 1) // gap + 1)
        k = self.max_consecutive_days
        if k is not None:
            days = min(days, total - total // (k + 1))
        return days

    def assignable_cells(self, eid: str) -> int:
        """该员工在整个周期内最多能占几格（不含意图约束）。

        one_shift_per_day 打开时一天只算一格，否则按 max_shifts_per_day（受重叠与休息间隔限制）折算。
        """
        per_day = 1 if self.one_shift_per_day else self.max_shifts_per_day()
        return self.max_work_days(eid) * per_day

    # ---- 规则查询 ----

    def rules_of(self, rtype: str) -> List[RuleDef]:
        return list(self._by_type.get(rtype, []))

    def require_attribute_rules(self) -> List[RuleDef]:
        return list(self._require_rules)

    def scarce_attribute(self) -> Optional[Tuple[str, str]]:
        return self._scarce_attr

    def _pick_scarce_attribute(self) -> Optional[Tuple[str, str]]:
        """最紧的那一项资质（需求/供给最高）。

        求解器只保护这一项：同时给多项加权会把评分打成噪声，
        默认数据下它就是店长值守（供给 5 人、每班要 1 名）。
        """
        best: Optional[Tuple[float, str, str]] = None
        for r in self._require_rules:
            attr = str(r.params.get("attr") or "skill")
            value = str(r.params.get("value") or "")
            need = valid_limit(r.params.get("min"))
            if not value or need is None:
                continue
            supply = len(self.attribute_pool(attr, value))
            ratio = need / supply if supply else float("inf")
            cand = (ratio, attr, value)
            if best is None or cand[0] > best[0]:
                best = cand
        return (best[1], best[2]) if best else None

    # ---- 时间 ----

    def span(self, day: str, shift: str) -> Tuple[int, int]:
        return self._abs[(day, shift)]

    def rest_gap_hours(self, a: Tuple[str, str], b: Tuple[str, str]) -> float:
        """两格之间的休息小时数。负数 = 时间重叠（同一人物理上不可能都在岗）。"""
        (s1, e1), (s2, e2) = self._abs[a], self._abs[b]
        if s1 <= s2:
            return (s2 - e1) / 60.0
        return (s1 - e2) / 60.0

    def overlaps(self, a: Tuple[str, str], b: Tuple[str, str]) -> bool:
        return self.rest_gap_hours(a, b) < 0

    def max_shifts_per_day(self) -> int:
        """一个人一天最多能合法覆盖几个班（只看时间重叠与最小休息间隔）。

        用于无解诊断的下界推导：旧实现写死「两班制下一天要 2 名不同员工」，
        三班制门店必须按实际可覆盖数算，否则会误报无解。
        """
        if self._max_shifts_per_day is not None:
            return self._max_shifts_per_day
        if self.one_shift_per_day or not self.shift_ids or not self.day_ids:
            self._max_shifts_per_day = 1
            return 1
        day = self.day_ids[0]
        rest = self.min_rest_hours
        best = 1
        # 班次上限 4，2^4 子集穷举足够便宜，也避免贪心在跨夜班上出错
        for mask in range(1, 1 << len(self.shift_ids)):
            chosen = [self.shift_ids[i] for i in range(len(self.shift_ids)) if mask & (1 << i)]
            ok = True
            for i in range(len(chosen)):
                for j in range(i + 1, len(chosen)):
                    gap = self.rest_gap_hours((day, chosen[i]), (day, chosen[j]))
                    # 与 rules.check_min_rest_hours 同一个比较符：恰好等于阈值是合规的
                    if gap < 0 or (rest is not None and gap < rest):
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                best = max(best, len(chosen))
        self._max_shifts_per_day = best
        return best


def rule_min_staff(rule: RuleDef, day_def: Optional[DayDef], day: str, shift: str) -> int:
    """单条 min_staff_per_shift 规则在某一格上的下限。overrides 优先于 peak/default。"""
    params = rule.params or {}
    base = _as_int(params.get("peak"), _as_int(params.get("default"), 0)) if (day_def and day_def.peak) \
        else _as_int(params.get("default"), 0)
    for ov in params.get("overrides") or []:
        if not isinstance(ov, dict):
            continue
        if str(ov.get("day")) == day and str(ov.get("shift")) == shift:
            base = _as_int(ov.get("min"), base)
    return base


def attribute_noun(rule: RuleDef) -> str:
    """违规文案里「具备 X」的那个 X。"""
    params = rule.params or {}
    noun = params.get("noun")
    if isinstance(noun, str) and noun.strip():
        return noun.strip()
    value = str(params.get("value") or "")
    return f"{value}岗位" if params.get("attr") == "role" else f"{value}技能"


def valid_limit(value: object) -> Optional[int]:
    """「上限」类规则参数的唯一口径：>= 1 才算有效，其余一律当成「没配」。

    为什么要收敛到一个函数：0 和负数在语义上不是「不限制」，而是「谁都别排」。
    solver 如果按字面执行就会判死，validator 如果按 `<= 0 → 跳过` 兜底就会判合规，
    同一份配置得到两个答案。这里统一返回 None（该规则不生效），真正的拒绝交给
    config_check 的 invalid_rule_params —— 非法参数应该在入口被挡住，而不是由两个
    引擎各自解释。
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def valid_hours(value: object) -> Optional[float]:
    """min_rest_hours 的 hours：必须 > 0。理由同 valid_limit。"""
    try:
        h = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return h if h > 0 else None


def _as_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def slot_key(day: str, shift: str) -> str:
    return f"{day}|{shift}"


# ---------- 编译缓存 ----------

# 按配置指纹缓存，而不是挂在实例上：SchedulerConfig 会被 model_copy 出去改，
# 挂实例上的索引会跟着副本走，得到一个「和配置不一致的索引」——这类 bug 极难定位。
_INDEX_CACHE: "OrderedDict[str, ConfigIndex]" = OrderedDict()
_INDEX_CACHE_MAX = 8

ConfigLike = Union[None, SchedulerConfig, ConfigIndex]


def index_of(source: ConfigLike = None) -> ConfigIndex:
    """把 None / SchedulerConfig / 已编译索引统一成索引。

    允许三种入参是为了让内层函数（validator、repair）能把索引原样传下去，
    而不必在每层都写一遍「有 config 就编译」的样板。
    """
    if isinstance(source, ConfigIndex):
        return source
    cfg = source if source is not None else _default_singleton()
    key = cfg.fingerprint()
    hit = _INDEX_CACHE.get(key)
    if hit is not None:
        _INDEX_CACHE.move_to_end(key)
        return hit
    built = ConfigIndex(cfg)
    _INDEX_CACHE[key] = built
    if len(_INDEX_CACHE) > _INDEX_CACHE_MAX:
        _INDEX_CACHE.popitem(last=False)
    return built


def default_index() -> ConfigIndex:
    return index_of(None)


def config_of(source: ConfigLike = None) -> SchedulerConfig:
    return index_of(source).config


def as_config(source: ConfigLike = None) -> Optional[SchedulerConfig]:
    """把入参折回「可继续往下传的 config」。None 保持 None，省略即默认的语义不能被悄悄改写。"""
    if source is None:
        return None
    return index_of(source).config


def ordered_skills(index: ConfigIndex, skills: Sequence[str]) -> List[str]:
    """按 skill_pool 顺序输出技能，保证同一个人在任何接口里的技能顺序一致。"""
    pool = index.config.skill_pool
    known = [s for s in pool if s in set(skills)]
    extra = [s for s in skills if s not in set(pool)]
    return known + extra
