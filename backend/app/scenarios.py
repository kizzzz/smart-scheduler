"""预置演示场景：覆盖正常生成、最小扰动重排、锁定、无解诊断、无法判断五类。"""
from __future__ import annotations

from typing import Dict, List

SCENARIOS: List[Dict[str, str]] = [
    {
        "id": "base",
        "title": "生成本周基础排班",
        "text": "帮我排下周一到周日的班，尽量照顾大家的班次偏好，工时也别差太多。",
        "expect": "ok",
        "note": "正常态：9 条硬规则全部通过",
    },
    {
        "id": "temp_leave",
        "title": "临时请假重排",
        "text": "E06 周四家里有事来不了，重新排一下，其他人的班尽量别动。",
        "expect": "ok",
        "note": "修改类指令：最小扰动重排",
    },
    {
        "id": "pin",
        "title": "指定值守",
        "text": "周六早班让 E01 亲自盯着，其他照常排。",
        "expect": "ok",
        "note": "锁定约束：E01 固定在周六早班",
    },
    {
        "id": "infeasible",
        "title": "压力场景：无解诊断",
        "text": "E01 和 E02 这周都去总部培训，整周都不要排他们的班。",
        "expect": "infeasible",
        "note": "值守资格击穿，触发无解诊断与解锁选项",
    },
    {
        "id": "ambiguous",
        "title": "含糊指令：无法判断",
        "text": "小王明天来不了，你帮我调一下吧。",
        "expect": "needs_clarification",
        "note": "指代不明，必须反问而不是猜",
    },
]
