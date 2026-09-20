"""L1/L4 与导入层的配置化：白名单、prompt、别名表都必须来自配置。

这一层最容易「看起来配置化了」但其实没有：prompt 里还写着 E01–E20，护栏白名单还认
默认工号。那样的后果不是报错，而是模型编出来的约束照单全收——排班被悄悄改掉，
店长看不出来。所以这里逐个入口验白名单与文案的来源。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import importer, llm  # noqa: E402
from app.config import RULE_MIN_STAFF, RULE_REQUIRE_ATTRIBUTE, default_config  # noqa: E402
from config_factory import config, days, emp, rule, shifts  # noqa: E402

THREE_SHIFTS = shifts(("早", "08:00", "12:00"), ("中", "12:00", "16:00"), ("晚", "16:00", "20:00"))


def custom():
    return config(
        day_defs=days(3, peak=["d3"]),
        shift_defs=THREE_SHIFTS,
        employees=[emp("S1", name="张三", skills=["咖啡"]), emp("S2", name="李四"), emp("S3", name="王五")],
        skill_pool=["咖啡"],
        rules=[
            rule("MS", RULE_MIN_STAFF, default=1, peak=2),
            rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1),
        ],
    )


# ---------- L1 prompt 与护栏白名单 ----------


def test_intent_prompt_lists_actual_pool_and_dimensions():
    text = llm.intent_system_prompt(custom())
    assert "S1" in text and "张三" in text
    assert "d1" in text and "d3" in text and "早" in text and "晚" in text
    # 默认配置的痕迹不能残留在自定义配置的 prompt 里，否则模型会照着编工号
    assert "E01" not in text and "周一" not in text

    default_text = llm.intent_system_prompt()
    assert "E01" in default_text and "E20" in default_text
    assert "周一" in default_text and "早班" in default_text


def test_mentioned_employees_whitelist_follows_config():
    cfg = custom()
    assert llm.mentioned_employee_ids("S1 明天不来", cfg) == {"S1"}
    # 默认工号在这份配置里不存在，不能被「认领」
    assert llm.mentioned_employee_ids("E01 明天不来", cfg) == set()
    assert llm.mentioned_employee_ids("E01 明天不来") == {"E01"}


def test_sanitize_drops_constraints_outside_config():
    """模型幻觉出的工号/日期/班次必须在进求解器之前被丢掉，并计入护栏统计。"""
    cfg = custom()
    raw = {
        "action": "adjust",
        "pins": [
            {"employee_id": "S1", "day": "d1", "shift": "早"},        # 合法
            {"employee_id": "E09", "day": "d1", "shift": "早"},       # 工号不在配置里
            {"employee_id": "S2", "day": "周一", "shift": "早"},      # 日期不在配置里
            {"employee_id": "S2", "day": "d1", "shift": "夜班"},      # 班次不在配置里
        ],
        "min_staff_override": {"d1|早": 3, "d9|早": 5},
    }
    intent = llm._sanitize(raw, "S1 d1 早班必须在岗，S2 也排上", "llm", config=cfg)
    assert [(p.employee_id, p.day, p.shift) for p in intent.pins] == [("S1", "d1", "早")]
    assert intent.min_staff_override == {"d1|早": 3}
    assert intent.guardrail.triggered and intent.guardrail.dropped_pins == 3
    assert "E09" in intent.guardrail.invalid_employee_ids


def test_fallback_parse_reads_configured_days_and_shifts():
    cfg = custom()
    leave = llm.fallback_parse("S2 d2 请假", cfg)
    assert leave.action == "adjust"
    assert [(t.employee_id, t.days) for t in leave.temp_leaves] == [("S2", ["d2"])]

    pin = llm.fallback_parse("S1 d3 晚 必须在岗", cfg)
    assert [(p.employee_id, p.day, p.shift) for p in pin.pins] == [("S1", "d3", "晚")]

    # 说不清是哪个班时要追问，而且候选班次来自配置
    ask = llm.fallback_parse("S1 d3 必须在岗", cfg)
    assert ask.clarification_needed and "早还是中还是晚" in ask.clarification_needed[0]

    # 默认配置行为不变
    assert llm.fallback_parse("E05 周六请假").temp_leaves[0].days == ["六"]


def test_fallback_parse_samples_come_from_config():
    ask = llm.fallback_parse("帮我把小王的班挪一下", custom())
    assert ask.clarification_needed and "S1" in ask.clarification_needed[0]


# ---------- L4 解释 ----------


def test_template_explanation_uses_configured_rule_count():
    cfg = custom()
    from app import solver
    from app import validator as V
    from app.models import Diagnosis, ScheduleRequest

    intent = ScheduleRequest(action="adjust", temp_leaves=[{"employee_id": "S3", "days": ["d1"]}])
    sch, _, _ = solver.solve(intent, None, config=cfg)
    assert sch is not None
    rep = V.validate(sch, intent, config=cfg)
    text = llm.template_explanation(rep, intent, Diagnosis(), config=cfg)
    assert "4 条" in text          # MS + A1 + 两条 locked
    assert "第1天" in text and "周一" not in text


def test_explain_prompt_lists_active_rules_only():
    cfg = default_config()
    for r in cfg.rules:
        if r.id in {"R-05", "R-06"}:
            r.enabled = False
    text = llm.explain_system_prompt(cfg)
    assert "R-01" in text and "R-05" not in text
    # locked 规则即使停用也在执行，因此必须出现在解释依据里
    assert "R-08" in text and "R-09" in text


# ---------- 视觉 prompt ----------


def test_vision_prompt_counts_slots_from_config():
    assert "一共应该有 14 个格子" in llm.vision_prompt()
    text = llm.vision_prompt(custom())
    assert "一共应该有 9 个格子" in text
    assert "d1 d2 d3" in text and "早 中 晚" in text
    assert "E01" not in text and "早班" not in text


def test_image_import_uses_config_dimensions(monkeypatch):
    rows = [{"day": "d1", "shift": "早", "employees": ["S1", "S2"]}]

    async def _fake(data_url, model=None, config=None):
        assert config is not None            # 配置必须一路传到视觉调用
        assert "9 个格子" in llm.vision_prompt(config)
        return rows, "glm-4v-flash"

    monkeypatch.setattr(llm, "read_schedule_image", _fake)
    out = asyncio.run(importer.parse_image(b"\x89PNG", ".png", None, custom()))
    assert out.ok is True and out.resolved == 2
    assert out.confidence == round(1 / 9, 2)     # 读到 1 格 / 应有 9 格
    assert out.requires_confirmation is True


def test_image_import_keeps_legacy_call_signature(monkeypatch):
    """省略 config 时不能给 read_schedule_image 多传关键字：这个边界被大量 mock。"""
    async def _legacy(data_url, model=None):
        return [{"day": "一", "shift": "早班", "employees": ["E01"]}], "glm-4v-flash"

    monkeypatch.setattr(llm, "read_schedule_image", _legacy)
    out = asyncio.run(importer.parse_image(b"\x89PNG", ".png"))
    assert out.ok is True and out.resolved == 1


# ---------- 导入层别名表 ----------


def test_importer_resolves_custom_ids_and_names():
    cfg = custom()
    assert importer.resolve_employee("S1", cfg) == ("S1", None)
    assert importer.resolve_employee("s1", cfg) == ("S1", None)
    assert importer.resolve_employee("张三", cfg) == ("S1", None)
    # 配置里没有的工号带原因进 unresolved，绝不猜测映射
    eid, reason = importer.resolve_employee("E01", cfg)
    assert eid is None and "S1" in reason


def test_importer_default_reasons_unchanged():
    assert importer.resolve_employee("E1") == ("E01", None)
    assert importer.resolve_employee("休") == (None, None)
    _, reason = importer.resolve_employee("小王")
    assert "E01–E20" in reason and "无姓名字段" in reason
    _, reason = importer.resolve_employee("E21")
    assert "超出员工档案范围 E01–E20" in reason


def test_importer_matrix_layout_with_custom_shifts():
    csv = "日期,早,中,晚\nd1,S1,S2,S3\n第2天,S2,S3,S1\n".encode()
    out = importer.parse_csv(csv, custom())
    assert out.ok is True and out.layout == "matrix"
    assert {(s.day, s.shift): s.employee_ids for s in out.slots}[("d2", "晚")] == ["S1"]
    assert out.resolved == 6 and not out.unresolved


def test_importer_rejects_shift_absent_from_config():
    csv = "日期,班次,员工\nd1,早,S1\nd1,夜班,S2\n".encode()
    out = importer.parse_csv(csv, custom())
    assert any("夜班" in w for w in out.warnings)
    assert len(out.slots) == 1


@pytest.mark.parametrize("cell", ["S1 S2", "S1,S2", "S1、S2", "S1/S2"])
def test_importer_separators_still_work_on_custom_ids(cell):
    csv = f"日期,班次,员工\nd1,早,{cell}\n".encode()
    out = importer.parse_csv(csv, custom())
    assert out.slots[0].employee_ids == ["S1", "S2"]
