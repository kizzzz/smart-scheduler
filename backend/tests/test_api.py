"""API 契约测试：字段结构必须与前端约定一致（离线，不依赖 GLM）。"""
from __future__ import annotations

import copy
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client(monkeypatch_session=None):
    os.environ.pop("GLM_API_KEY", None)  # 强制走降级路径，测试不依赖外部网络
    return TestClient(app)


@pytest.fixture(scope="module")
def generated(client):
    r = client.post("/api/generate", json={"instruction": "", "base_slots": None})
    assert r.status_code == 200
    return r.json()


def test_meta_contract(client):
    m = client.get("/api/meta").json()
    assert set(m) == {"employees", "rules", "days", "shifts"}
    assert len(m["employees"]) == 20 and len(m["rules"]) == 9
    assert len(m["days"]) == 7 and len(m["shifts"]) == 2
    assert {d["key"]: d["min_required"] for d in m["days"]}["六"] == 6
    e = m["employees"][0]
    assert set(e) == {"id", "role", "skills", "available_days", "leave_days", "preference"}
    assert isinstance(e["preference"], str)   # 不能是 null，前端直接渲染


def test_scenarios_contract(client):
    arr = client.get("/api/scenarios").json()
    assert isinstance(arr, list) and len(arr) >= 4
    for s in arr:
        assert set(s) == {"id", "title", "description", "instruction", "base_required"}


def test_generate_contract(generated):
    r = generated
    assert set(r) == {
        "ok", "mode", "intent", "scenario", "solution", "validation",
        "explanation", "infeasible", "clarification", "diff", "timing",
    }
    assert r["ok"] is True and r["infeasible"] is None
    # scenario 回显（契约 5.3）：看板按它渲染维度，不再自己按「7 天 × 2 班、周末=6」推
    assert [d["id"] for d in r["scenario"]["days"]] == ["一", "二", "三", "四", "五", "六", "日"]
    assert [s["id"] for s in r["scenario"]["shifts"]] == ["早班", "晚班"]
    assert r["scenario"]["days"][5]["peak"] is True and r["scenario"]["days"][0]["peak"] is False
    assert r["scenario"]["shifts"][0]["time_label"] == "09:00–17:00"
    assert len(r["solution"]["slots"]) == 14
    slot = r["solution"]["slots"][0]
    assert set(slot) == {"day", "day_label", "shift", "shift_time", "min_required", "employees"}
    sm = r["solution"]["soft_metrics"]
    assert set(sm) == {"preference_rate", "balance_score", "skill_redundancy"}
    assert all(0 <= sm[k] <= 1 for k in sm), sm      # 三个指标必须是 0–1，前端直接画条
    # import_ms 在 generate 恒为 0，但字段必须常在（契约 v1.1），前端才不用做兼容判断
    assert set(r["timing"]) == {"parse_ms", "solve_ms", "validate_ms", "explain_ms", "import_ms", "total_ms"}
    assert r["timing"]["import_ms"] == 0
    assert len(r["validation"]["rules"]) == 10
    assert r["validation"]["passed"] and r["validation"]["violation_count"] == 0
    assert r["explanation"]["bullets"]


def test_generate_infeasible_contract(client):
    r = client.post("/api/generate", json={
        "instruction": "E01 和 E02 整周都不排班",
        "base_slots": None,
    }).json()
    if r["mode"] == "clarify":
        pytest.skip("降级解析未识别该指令，无解路径由 solver 单测覆盖")
    assert r["ok"] is False and r["solution"] is None
    # 无解时不给「所有规则都不通过」的空报告：没有排班表就没有被违反的规则
    assert r["validation"] is None
    inf = r["infeasible"]
    assert inf and inf["proven"] is True
    assert inf["summary"] and inf["min_conflict_set"]
    assert inf["unlock_paths"] and set(inf["unlock_paths"][0]) == {
        "title", "detail", "extra_cost", "needs_approval"
    }


def test_generate_clarification_contract(client):
    r = client.post("/api/generate", json={"instruction": "小王明天来不了", "base_slots": None}).json()
    assert r["ok"] is False and r["mode"] == "clarify"
    assert r["solution"] is None and r["infeasible"] is None
    assert r["clarification"]["questions"]
    # 没有排班表就没有校验结果：空报告会让每条规则显示红叉，报告一个没发生过的失败
    assert r["validation"] is None


def test_validate_and_suggestions(client, generated):
    slots = copy.deepcopy(generated["solution"]["slots"])
    # 把周一晚班的人塞进周二早班 → 必然触发 R-07（以及同日重叠的 R-05）
    victim = slots[1]["employees"][0]
    if victim not in slots[2]["employees"]:
        slots[2]["employees"].append(victim)
    body = client.post("/api/validate", json={"slots": slots}).json()
    v = body["validation"]
    assert v["passed"] is False and v["violation_count"] >= 1
    r07 = next(r for r in v["rules"] if r["id"] == "R-07")
    assert r07["passed"] is False and r07["violations"]
    vio = r07["violations"][0]
    assert set(vio) == {"day", "shift", "employees", "message", "suggestions"}
    assert vio["suggestions"], "违规必须给出可执行的修复建议"
    sug = vio["suggestions"][0]
    assert set(sug) == {"label", "day", "shift", "remove", "add"}
    assert sug["day"] == vio["day"] and sug["shift"] == vio["shift"]


def test_suggestion_actually_fixes(client, generated):
    """建议必须真的能修好：照建议改一遍，违规数下降。"""
    slots = copy.deepcopy(generated["solution"]["slots"])
    victim = slots[1]["employees"][0]
    if victim not in slots[2]["employees"]:
        slots[2]["employees"].append(victim)
    v = client.post("/api/validate", json={"slots": slots}).json()["validation"]
    before = v["violation_count"]
    sug = next(
        s for r in v["rules"] for vi in r["violations"] for s in vi["suggestions"]
    )
    for s in slots:
        if s["day"] == sug["day"] and s["shift"] == sug["shift"]:
            if sug["remove"]:
                s["employees"] = [e for e in s["employees"] if e != sug["remove"]]
            if sug["add"] and sug["add"] not in s["employees"]:
                s["employees"].append(sug["add"])
    after = client.post("/api/validate", json={"slots": slots}).json()["validation"]
    assert after["violation_count"] < before


def test_adjust_returns_diff(client, generated):
    r = client.post("/api/generate", json={
        "instruction": "E06 周四家里有事来不了",
        "base_slots": generated["solution"]["slots"],
    }).json()
    assert r["ok"] is True
    assert r["mode"] == "adjust"
    d = r["diff"]
    assert d and set(d) == {"changed_count", "changed_slots", "new_violations"}
    assert d["new_violations"] == 0
    assert 0 < d["changed_count"] <= 5
    assert set(d["changed_slots"][0]) == {"day", "shift", "added", "removed"}
    for s in r["solution"]["slots"]:
        if s["day"] == "四":
            assert "E06" not in s["employees"]


def test_candidates_endpoint(client):
    r = client.get("/api/candidates", params={"day": "六", "shift": "早班", "taken": "E01,E02"}).json()
    ids = [c["id"] for c in r["candidates"]]
    assert "E01" not in ids and "E02" not in ids
    assert "E13" in ids            # 兼职周六可用
    assert "E12" not in ids        # E12 只能周一至周五
