"""员工数据、规则定义、班次与工作日定义。

数据来源：考题题面所给的员工表与规则表，未做任何补充或推断。
R-09 要求技能必须来自员工数据，因此本模块是全系统唯一的技能真相源。
"""
from __future__ import annotations

from typing import Dict, List

# ---------- 班次与工作日 ----------

DAYS: List[str] = ["一", "二", "三", "四", "五", "六", "日"]
DAY_LABELS: Dict[str, str] = {d: f"周{d}" for d in DAYS}
WEEKEND: set[str] = {"六", "日"}

SHIFTS: List[str] = ["早班", "晚班"]
SHIFT_TIME: Dict[str, str] = {"早班": "09:00–17:00", "晚班": "13:00–21:00"}

# R-04：周一至周五每班至少 4 人；周六、周日每班至少 6 人
MIN_PER_SHIFT_WEEKDAY = 4
MIN_PER_SHIFT_WEEKEND = 6

# 技能常量
SKILL_KEEPER = "店长值守"
SKILL_DRINK = "饮品制作"
SKILL_CASHIER = "收银"
SKILL_STOCK = "库存管理"


def min_required(day: str) -> int:
    return MIN_PER_SHIFT_WEEKEND if day in WEEKEND else MIN_PER_SHIFT_WEEKDAY


def slot_key(day: str, shift: str) -> str:
    return f"{day}|{shift}"


def all_slots() -> List[tuple[str, str]]:
    """14 个班次，按 周一早班、周一晚班、周二早班 … 的顺序。"""
    return [(d, s) for d in DAYS for s in SHIFTS]


# ---------- 9 条硬规则 ----------

RULES: List[Dict[str, str]] = [
    {"id": "R-01", "text": "每个班至少 1 名具备店长值守资格的员工"},
    {"id": "R-02", "text": "每个班至少 2 名具备饮品制作技能的员工"},
    {"id": "R-03", "text": "每个班至少 1 名具备收银技能的员工"},
    {"id": "R-04", "text": "周一至周五每班至少 4 人；周六、周日每班至少 6 人"},
    {"id": "R-05", "text": "每人每周最多 40 小时，即最多 5 个班"},
    {"id": "R-06", "text": "不得连续工作超过 5 天"},
    {"id": "R-07", "text": "上一天晚班后不得安排次日早班"},
    {"id": "R-08", "text": "请假和不可工作日期绝对不得排班"},
    {"id": "R-09", "text": "技能必须来自员工数据，Agent 不得自行补技能"},
]

MAX_SHIFTS_PER_WEEK = 5      # R-05
MAX_CONSECUTIVE_DAYS = 5     # R-06


# ---------- 员工原始数据（逐字来自题面） ----------

def _days(spec: str) -> List[str]:
    """把「全周」「周一至周五」「周一、周三、周五」解析为工作日列表。"""
    if spec == "全周":
        return list(DAYS)
    if "至" in spec and "、" not in spec:
        a, b = spec.replace("周", "").split("至")
        return DAYS[DAYS.index(a): DAYS.index(b) + 1]
    return [x.replace("周", "") for x in spec.split("、")]


_RAW: List[tuple] = [
    # (id, 岗位, 技能, 可工作日期, 请假, 偏好)
    ("E01", "店长", [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], "全周", ["三"], "早班"),
    ("E02", "副店长", [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], "全周", [], "晚班"),
    ("E03", "值班主管", [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], "周一至周五", [], None),
    ("E04", "值班主管", [SKILL_KEEPER, SKILL_DRINK, SKILL_STOCK], "周三至周日", [], "早班"),
    ("E05", "高级店员", [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], "周五至周日", [], "晚班"),
    ("E06", "店员", [SKILL_DRINK, SKILL_CASHIER], "全周", ["二"], "早班"),
    ("E07", "店员", [SKILL_DRINK, SKILL_CASHIER], "全周", [], "晚班"),
    ("E08", "店员", [SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], "周一至周六", [], None),
    ("E09", "店员", [SKILL_DRINK], "周一、周三、周五、周六、周日", [], "早班"),
    ("E10", "店员", [SKILL_DRINK, SKILL_CASHIER], "周二至周日", [], None),
    ("E11", "店员", [SKILL_CASHIER, SKILL_STOCK], "全周", [], "晚班"),
    ("E12", "店员", [SKILL_DRINK, SKILL_CASHIER], "周一至周五", [], "早班"),
    ("E13", "兼职", [SKILL_DRINK], "周六、周日", [], "早班"),
    ("E14", "兼职", [SKILL_DRINK, SKILL_CASHIER], "周六、周日", [], "晚班"),
    ("E15", "兼职", [SKILL_CASHIER], "周五至周日", [], None),
    ("E16", "兼职", [SKILL_DRINK], "周三、周四、周六、周日", [], None),
    ("E17", "店员", [SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], "全周", ["一"], None),
    ("E18", "店员", [SKILL_DRINK, SKILL_CASHIER], "周一至周五", [], "晚班"),
    ("E19", "兼职", [SKILL_DRINK, SKILL_CASHIER], "周六、周日", [], None),
    ("E20", "店员", [SKILL_DRINK, SKILL_STOCK], "周二至周日", ["四"], "早班"),
]


class Employee:
    __slots__ = ("id", "role", "skills", "available_days", "leave_days", "preference")

    def __init__(self, eid, role, skills, avail_spec, leave, preference):
        self.id: str = eid
        self.role: str = role
        self.skills: set[str] = set(skills)
        self.available_days: List[str] = _days(avail_spec)
        self.leave_days: List[str] = list(leave)
        self.preference: str | None = preference

    def is_part_time(self) -> bool:
        return self.role == "兼职"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "skills": [s for s in (SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK) if s in self.skills],
            "available_days": self.available_days,
            "leave_days": self.leave_days,
            "preference": self.preference,
        }


EMPLOYEES: Dict[str, Employee] = {r[0]: Employee(*r) for r in _RAW}
EMPLOYEE_IDS: List[str] = list(EMPLOYEES.keys())


def base_available(eid: str, day: str) -> bool:
    """题面固有的可工作性：在可工作日期内、且不在固有请假日。不含临时约束。"""
    e = EMPLOYEES[eid]
    return day in e.available_days and day not in e.leave_days


def has_skill(eid: str, skill: str) -> bool:
    """R-09 的唯一入口：技能判断只能查员工数据。"""
    return skill in EMPLOYEES[eid].skills
