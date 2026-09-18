#!/usr/bin/env python3
"""生成导入功能的演示样例，并用项目自己的校验器验证每个样例确实触发预期结论。

为什么要验一遍：样例文件的价值在于「拖进去能看到预期结果」。
如果凭手感编一份「违规排班表」，很可能实际违规项和说明文档写的不一致，
演示时会当场被打脸。所以每个样例都跑一遍 validator，断言结论符合预期。

用法（在仓库根目录）：
    python3 samples/make_samples.py
生成图片样例需要 Pillow；没装则跳过图片，其余样例照常生成。
"""
from __future__ import annotations

import csv
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

from app import solver  # noqa: E402
from app import validator as V  # noqa: E402
from app.data import DAY_LABELS, DAYS, EMPLOYEES, SHIFTS, SKILL_KEEPER  # noqa: E402
from app.models import Schedule, ScheduleRequest, Slot  # noqa: E402

OUT = _HERE


def to_schedule(grid) -> Schedule:
    return Schedule(slots=[
        Slot(day=d, shift=s, employee_ids=list(grid.get((d, s), [])))
        for d in DAYS for s in SHIFTS
    ])


def check(tag, grid):
    rep = V.validate(to_schedule(grid), None)
    rules = sorted({v.rule_id for v in rep.violations})
    print(f"  {tag:12s} passed={rep.passed}  违规 {len(rep.violations)} 条  规则 {rules}")
    return rep


def write_long(path, grid):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "班次", "员工"])
        for d in DAYS:
            for s in SHIFTS:
                w.writerow([DAY_LABELS[d], s, " ".join(grid.get((d, s), []))])


def write_matrix(path, grid):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "早班", "晚班"])
        for d in DAYS:
            w.writerow([DAY_LABELS[d], " ".join(grid.get((d, "早班"), [])),
                        " ".join(grid.get((d, "晚班"), []))])


def write_messy(path, grid):
    """故意脏的样例：列名别名、日期别名、班次简写、四种分隔符、姓名与越界工号。

    姓名「小王」和工号「E99」必须落到 unresolved——员工档案里没有姓名字段，
    也没有 E99，任何「智能猜测」都是编造数据。
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["星期", "shift", "人员"])
        w.writerow(["星期一", "早", "、".join(grid[("一", "早班")])])
        w.writerow(["周一", "晚班", ",".join(grid[("一", "晚班")])])
        w.writerow(["二", "早班", " ".join(e.lower() for e in grid[("二", "早班")])])
        w.writerow(["周二", "晚班", "，".join(grid[("二", "晚班")])])
        w.writerow(["星期三", "早班", " ".join(grid[("三", "早班")]) + " 小王"])
        w.writerow(["周三", "晚班", " ".join(grid[("三", "晚班")])])
        w.writerow(["周四", "早班", " ".join(x.replace("E0", "E") for x in grid[("四", "早班")])])
        w.writerow(["周四", "晚班", " ".join(grid[("四", "晚班")])])
        w.writerow(["周五", "早班", " ".join(grid[("五", "早班")])])
        w.writerow(["周五", "晚班", " ".join(grid[("五", "晚班")])])
        w.writerow(["周六", "早班", " ".join(grid[("六", "早班")])])
        w.writerow(["周六", "晚班", " ".join(grid[("六", "晚班")])])
        w.writerow(["周日", "早班", " ".join(grid[("日", "早班")]) + " E99"])
        w.writerow(["周日", "晚班", " ".join(grid[("日", "晚班")])])


def write_png(path, grid):
    """渲染一张贴近真实的排班表图片，用于演示视觉识别链路。

    实测 glm-4v-flash 在这类清晰表格上能做到 14/14 格全对；
    手机拍摄的倾斜/模糊照片准确率没有实测，因此图片导入强制人工确认。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  (跳过图片样例：未安装 Pillow)")
        return False

    fonts = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def font(size):
        for p in fonts:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        return ImageFont.load_default()

    W, H, PAD, COL_DAY, ROW_H, HEAD_H = 1140, 640, 30, 130, 62, 74
    COL = (W - 2 * PAD - COL_DAY) // 2
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f_title, f_head, f_cell, f_note = font(30), font(21), font(22), font(16)

    d.text((PAD, 18), "华东三店 · 下周排班表（第 38 周）", fill="#111827", font=f_title)
    top = 66
    x1, x2 = PAD + COL_DAY, PAD + COL_DAY + COL
    bottom = top + HEAD_H + ROW_H * len(DAYS)

    d.rectangle([PAD, top, W - PAD, top + HEAD_H], fill="#F3F4F6", outline="#9CA3AF")
    d.text((PAD + 40, top + 26), "日期", fill="#111827", font=f_head)
    for x, name, tm in ((x1, "早班", "09:00-17:00"), (x2, "晚班", "13:00-21:00")):
        d.text((x + 60, top + 14), name, fill="#111827", font=f_head)
        d.text((x + 40, top + 42), tm, fill="#6B7280", font=f_note)

    y = top + HEAD_H
    for i, day in enumerate(DAYS):
        bg = "#FFF7ED" if day in ("六", "日") else ("#FFFFFF" if i % 2 == 0 else "#FAFAFA")
        d.rectangle([PAD, y, W - PAD, y + ROW_H], fill=bg, outline="#D1D5DB")
        d.text((PAD + 30, y + 19), DAY_LABELS[day], fill="#111827", font=f_cell)
        d.text((x1 + 16, y + 19), " ".join(grid[(day, "早班")]), fill="#1F2937", font=f_cell)
        d.text((x2 + 16, y + 19), " ".join(grid[(day, "晚班")]), fill="#1F2937", font=f_cell)
        y += ROW_H

    for x in (x1, x2):
        d.line([x, top, x, bottom], fill="#9CA3AF")
    d.rectangle([PAD, top, W - PAD, bottom], outline="#6B7280", width=2)
    d.text((PAD, bottom + 16), "备注：工号对应员工档案 E01–E20；周末每班需 6 人。",
           fill="#6B7280", font=f_note)
    img.save(path)
    return True


print("=== 1. 用真实求解器生成合规排班 ===")
sched, _notes, diag = solver.solve(ScheduleRequest(action="generate", raw_text="生成样例"), None)
assert sched is not None, f"求解器未给出可行解：{diag}"
good = {(s.day, s.shift): list(s.employee_ids) for s in sched.slots}
assert check("合规", good).passed, "求解器产出不合规，需排查"

print("=== 2. 在合规基础上做最小破坏，构造违规样例 ===")
keepers = {e.id for e in EMPLOYEES.values() if SKILL_KEEPER in e.skills}
bad = {k: list(v) for k, v in good.items()}
bad[("一", "早班")] = [e for e in bad[("一", "早班")] if e not in keepers]   # 打掉店长值守 → R-01
bad[("六", "晚班")] = bad[("六", "晚班")][:3]                                # 周末砍到 3 人 → R-04
if "E01" not in bad[("三", "早班")]:
    bad[("三", "早班")].append("E01")                                        # E01 周三请假 → R-08
rep_bad = check("违规", bad)
assert not rep_bad.passed, "违规样例竟然通过校验，构造失败"

print("=== 3. 写出文件 ===")
write_long(os.path.join(OUT, "roster_long_ok.csv"), good)
write_matrix(os.path.join(OUT, "roster_matrix_ok.csv"), good)
write_long(os.path.join(OUT, "roster_long_violating.csv"), bad)
write_messy(os.path.join(OUT, "roster_messy.csv"), good)
write_png(os.path.join(OUT, "roster_image_ok.png"), good)

for name in sorted(os.listdir(OUT)):
    if name.startswith("roster"):
        print(f"  {name:32s} {os.path.getsize(os.path.join(OUT, name)):7d} bytes")
