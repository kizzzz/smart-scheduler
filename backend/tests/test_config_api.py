"""配置相关的 API 契约（契约 5.1–5.6，离线，不依赖 GLM）。

重点有两条：
1. 带 config 的请求要按配置回答（scenario 回显、min_required、slots_expected 都随配置变）；
2. 不带 config 的请求必须与改造前一字不差——老前端不改也能跑是这次改造的硬要求。
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import (  # noqa: E402
    RULE_MAX_CONSECUTIVE,
    RULE_MIN_STAFF,
    RULE_ONE_SHIFT_PER_DAY,
    RULE_REQUIRE_ATTRIBUTE,
    default_config,
)
from app.main import app  # noqa: E402
from config_factory import config, days, emp, emps, rule, shifts  # noqa: E402

THREE_SHIFTS = shifts(("早", "08:00", "12:00"), ("中", "12:00", "16:00"), ("晚", "16:00", "20:00"))


@pytest.fixture(scope="module")
def client():
    os.environ.pop("GLM_API_KEY", None)      # 强制走降级解析，测试不依赖外部网络
    return TestClient(app)


def custom_config() -> dict:
    cfg = config(
        day_defs=days(3, peak=["d3"]),
        shift_defs=THREE_SHIFTS,
        employees=[*emps(4, skills=["咖啡"]), *emps(4, start=5)],
        skill_pool=["咖啡"],
        rules=[
            rule("MS", RULE_MIN_STAFF, default=2, peak=3, name="每班至少 2 人，高峰日 3 人"),
            rule("A1", RULE_REQUIRE_ATTRIBUTE, attr="skill", value="咖啡", min=1, name="每班 1 名咖啡师"),
        ],
    )
    return cfg.model_dump()


# ---------- 5.1 GET /api/config/default ----------


def test_config_default_returns_full_config(client):
    body = client.get("/api/config/default").json()
    assert set(body) == {"config"}
    cfg = body["config"]
    assert set(cfg) == {"version", "scenario", "skill_pool", "role_pool", "employees", "rules"}
    assert len(cfg["employees"]) == 20 and len(cfg["rules"]) == 10
    assert len(cfg["scenario"]["days"]) == 7 and len(cfg["scenario"]["shifts"]) == 2
    # 题面没有姓名：占位必须是工号，编中文名等于凭空造数据
    assert all(e["name"] == e["id"] for e in cfg["employees"])
    assert cfg["scenario"]["shifts"][0]["hours"] == 8.0
    # 只有两条数据完整性规则是 locked；R-10（一人一天一个班）是业务选择，必须可关
    assert [r["id"] for r in cfg["rules"] if r["locked"]] == ["R-08", "R-09"]
    assert cfg["rules"][-1]["type"] == "one_shift_per_day" and cfg["rules"][-1]["enabled"] is True


def test_config_default_round_trips_through_generate(client):
    """默认配置原样回传必须仍然可解——否则前端「预填 + 直接生成」的首屏路径就是坏的。"""
    cfg = client.get("/api/config/default").json()["config"]
    r = client.post("/api/generate", json={"instruction": "", "config": cfg})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and len(body["solution"]["slots"]) == 14
    assert len(body["validation"]["rules"]) == 10


# ---------- 5.2 POST /api/config/validate ----------


def test_config_validate_default_is_ok(client):
    body = client.post("/api/config/validate", json={"config": None}).json()
    assert body["ok"] is True and body["errors"] == []
    assert set(body) == {"ok", "errors", "warnings", "capacity"}
    assert set(body["capacity"]) == {
        "demand_person_shifts", "supply_person_shifts", "headroom_pct", "per_slot",
    }
    assert set(body["capacity"]["per_slot"][0]) == {"day", "shift", "min_required", "eligible"}


def test_config_validate_reports_errors_with_where(client):
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=[emp("E01"), emp("E01")],
        rules=[rule("MS", RULE_MIN_STAFF, default=3)],
    ).model_dump()
    body = client.post("/api/config/validate", json={"config": cfg}).json()
    assert body["ok"] is False
    codes = {e["code"] for e in body["errors"]}
    assert "duplicate_employee_id" in codes
    for e in body["errors"]:
        assert e["message"] and e["fix"]        # 每条 error 都要能直接照着改
    # 自检失败仍是 200：ok=false 是业务结论，不是请求错误
    assert client.post("/api/config/validate", json={"config": cfg}).status_code == 200


def test_config_validate_rejects_malformed_config_without_422(client):
    """来自旧版本 localStorage 的配置可能字段对不上。422 白屏是最差的结果。"""
    broken = default_config().model_dump()
    broken["scenario"]["shifts"][0]["start"] = "上午九点"
    r = client.post("/api/config/validate", json={"config": broken})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["errors"][0]["code"] == "invalid_config"
    # 报错要指到字段并带上原因，不能只说「配置不合法」
    assert "scenario.shifts.0" in body["errors"][0]["message"]
    assert "HH:MM" in body["errors"][0]["message"] and body["errors"][0]["fix"]


def test_config_validate_rejects_non_object(client):
    """列表/字符串这类形状也要走同一条 invalid_config 路径，而不是 FastAPI 的 422。"""
    for junk in ([1, 2], "七天两班", 3):
        body = client.post("/api/config/validate", json={"config": junk}).json()
        assert body["errors"][0]["code"] == "invalid_config", junk


# ---------- 5.3 POST /api/generate ----------


def test_generate_with_custom_config(client):
    body = client.post("/api/generate", json={"instruction": "", "config": custom_config()}).json()
    assert body["ok"] is True
    assert [d["id"] for d in body["scenario"]["days"]] == ["d1", "d2", "d3"]
    assert [s["id"] for s in body["scenario"]["shifts"]] == ["早", "中", "晚"]
    assert body["scenario"]["shifts"][1]["time_label"] == "12:00–16:00"
    slots = body["solution"]["slots"]
    assert len(slots) == 9
    # 逐格下限来自配置，看板不再按「周末=6」自己算
    need = {(s["day"], s["shift"]): s["min_required"] for s in slots}
    assert need[("d1", "早")] == 2 and need[("d3", "早")] == 3
    assert all(len(s["employees"]) >= s["min_required"] for s in slots)
    assert [r["id"] for r in body["validation"]["rules"]] == ["MS", "A1", "R-08", "R-09"]
    assert body["validation"]["passed"] is True
    assert body["intent"]["period_label"].endswith("早/中/晚")


def test_generate_rejects_daily_capacity_gap_before_solving(client):
    """用户报的复现路径：3 天 × 3 班、8 人、某天要 9 人次。

    以前 /api/config/validate 说 ok，点生成却拿到求解器证明的无解。现在两端一致：
    自检直接给出那一天的证据，请求在求解之前就被挡下。
    """
    cfg = config(
        day_defs=days(3, peak=("d3",)), shift_defs=THREE_SHIFTS, employees=emps(8),
        rules=[rule("MS", RULE_MIN_STAFF, default=2, peak=3),
               rule("R-10", RULE_ONE_SHIFT_PER_DAY)],
    ).model_dump()
    check = client.post("/api/config/validate", json={"config": cfg}).json()
    assert check["ok"] is False
    assert "daily_capacity_lt_demand" in {e["code"] for e in check["errors"]}
    r = client.post("/api/generate", json={"instruction": "", "config": cfg})
    assert r.status_code == 400
    assert "daily_capacity_lt_demand" in {e["code"] for e in r.json()["errors"]}


def test_generate_infeasible_returns_null_validation(client):
    """无解时 validation 必须是 null：空报告等于报告一个没发生过的失败。"""
    # 配置本身可解（4 人 ≥ 每班 3 人），无解来自指令把两个人排除掉
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=emps(4),
        rules=[rule("MS", RULE_MIN_STAFF, default=3)],
    ).model_dump()
    body = client.post("/api/generate", json={
        "instruction": "E01 和 E02 整周都不排班", "config": cfg,
    }).json()
    if body["mode"] == "clarify":
        # 反问路径同样必须是 null，两条 solution=null 的路径给同一个答案
        assert body["validation"] is None and body["infeasible"] is None
        return
    assert body["ok"] is False and body["solution"] is None
    assert body["validation"] is None
    assert body["infeasible"] and body["infeasible"]["proven"] is True
    # scenario 仍在：前端还要按维度画空看板
    assert [d["id"] for d in body["scenario"]["days"]] == ["d1", "d2"]


def test_generate_rejects_unsolvable_config_with_400(client):
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=emps(1),
        rules=[rule("MS", RULE_MIN_STAFF, default=3)],
    ).model_dump()
    r = client.post("/api/generate", json={"instruction": "", "config": cfg})
    assert r.status_code == 400
    body = r.json()
    # body 与 /api/config/validate 同构，前端只需一个渲染器
    assert set(body) >= {"ok", "errors", "warnings", "capacity", "detail", "message"}
    assert body["ok"] is False
    assert "supply_lt_demand" in {e["code"] for e in body["errors"]}
    assert body["detail"] == body["message"] == body["errors"][0]["message"]


def test_generate_rejects_malformed_config_with_400(client):
    r = client.post("/api/generate", json={"instruction": "", "config": {"scenario": "七天"}})
    assert r.status_code == 400
    assert r.json()["errors"][0]["code"] == "invalid_config"


def test_generate_400_body_is_isomorphic_to_config_validate(client):
    """同一份坏配置，两个接口给的结论必须是同一份数据，前端只写一个渲染器。

    /api/generate 额外多出 detail/message 两个键（老前端两套读法），除此之外
    errors/warnings/capacity 必须逐字相同——否则「配置页说没问题、点生成又报别的错」
    这种矛盾迟早出现。
    """
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=emps(1),
        rules=[rule("MS", RULE_MIN_STAFF, default=3)],
    ).model_dump()
    gen = client.post("/api/generate", json={"instruction": "", "config": cfg})
    chk = client.post("/api/config/validate", json={"config": cfg})
    assert gen.status_code == 400 and chk.status_code == 200
    a, b = gen.json(), chk.json()
    assert set(a) - set(b) == {"detail", "message"}
    for key in ("ok", "errors", "warnings", "capacity"):
        assert a[key] == b[key]


def test_generate_rejects_illegal_rule_params_with_400(client):
    """非法规则参数走的是同一条 400 通道：绝不猜一种解释再去求解。"""
    cfg = config(
        day_defs=days(2), shift_defs=THREE_SHIFTS, employees=emps(6),
        rules=[rule("MS", RULE_MIN_STAFF, default=1),
               rule("MC", RULE_MAX_CONSECUTIVE, max=0, name="最多连续工作 0 天")],
    ).model_dump()
    r = client.post("/api/generate", json={"instruction": "", "config": cfg})
    assert r.status_code == 400
    body = r.json()
    hit = [e for e in body["errors"] if e["code"] == "invalid_rule_params"]
    assert hit and hit[0]["where"] == {"rule_id": "MC"}
    assert body["message"] == body["errors"][0]["message"]


def test_generate_without_config_keeps_default_behaviour(client):
    body = client.post("/api/generate", json={"instruction": "", "base_slots": None}).json()
    assert len(body["solution"]["slots"]) == 14
    assert [d["id"] for d in body["scenario"]["days"]] == ["一", "二", "三", "四", "五", "六", "日"]
    assert len(body["validation"]["rules"]) == 10


def test_generate_clarify_path_also_echoes_scenario(client):
    """反问路径也要带 scenario：前端在拿到追问时同样需要按维度渲染空看板。"""
    body = client.post("/api/generate", json={"instruction": "把那个谁挪一下", "config": custom_config()}).json()
    if body["mode"] != "clarify":
        pytest.skip("降级解析未把该指令判为需澄清")
    assert [d["id"] for d in body["scenario"]["days"]] == ["d1", "d2", "d3"]
    # 反问时没有排班表，validation 只能是 null（scenario 仍在，空看板照样画得出来）
    assert body["validation"] is None


# ---------- 5.4 POST /api/validate ----------


def test_validate_with_config(client):
    cfg = custom_config()
    slots = [
        {"day": "d1", "shift": "早", "employees": ["E01", "E05"]},
        {"day": "zz", "shift": "早", "employees": ["E02"]},        # 配置外的格子必须被丢掉
    ]
    body = client.post("/api/validate", json={"slots": slots, "config": cfg}).json()
    ids = [r["id"] for r in body["validation"]["rules"]]
    assert ids == ["MS", "A1", "R-08", "R-09"]
    # d1 早班有人且满足下限，其余 8 格空 → MS 报 8 条，不该出现 zz 这一格
    ms = next(r for r in body["validation"]["rules"] if r["id"] == "MS")
    assert len(ms["violations"]) == 8
    assert all(v["day"] != "zz" for v in ms["violations"])
    assert set(body["soft_metrics"]) == {"preference_rate", "balance_score", "skill_redundancy"}
    assert all(0.0 <= v <= 1.0 for v in body["soft_metrics"].values())


def test_validate_without_config_unchanged(client):
    body = client.post("/api/validate", json={"slots": [
        {"day": "一", "shift": "早班", "employees": ["E01"]},
    ]}).json()
    assert len(body["validation"]["rules"]) == 10


# ---------- 5.5 POST /api/candidates ----------


def test_candidates_post_pool_comes_from_config(client):
    """自定义配置下候选只能来自这份配置里的人。

    这是这个接口配置化的全部意义：改造前它读的是默认 20 人档案，在 8 人门店里
    会给出 E09–E20 这些不存在的员工，用户点一下就把幽灵排进班。
    """
    cfg = custom_config()
    pool = {e["id"] for e in cfg["employees"]}
    assert len(pool) == 8
    body = client.post("/api/candidates", json={"day": "d1", "shift": "早", "config": cfg}).json()
    ids = [c["id"] for c in body["candidates"]]
    assert body["day"] == "d1" and body["shift"] == "早"
    assert set(ids) <= pool and len(ids) == 8
    assert not [i for i in ids if i in {"E09", "E15", "E20"}]


def test_candidates_post_respects_taken_and_unavailability(client):
    cfg = config(
        day_defs=days(2),
        shift_defs=THREE_SHIFTS,
        employees=[emp("A1"), emp("A2"), emp("A3", unavailable=[{"day": "d1"}]), emp("A4", active=False)],
    ).model_dump()
    body = client.post(
        "/api/candidates",
        json={"day": "d1", "shift": "早", "taken": ["A1"], "config": cfg},
    ).json()
    # A1 已在格子里、A3 当天不可用、A4 已停用 → 只剩 A2
    assert [c["id"] for c in body["candidates"]] == ["A2"]


def test_candidates_post_rejects_stale_slot(client):
    """格子来自上一份配置时必须 400：静默返回空名单会被读成「没人能上」。"""
    cfg = custom_config()
    r = client.post("/api/candidates", json={"day": "六", "shift": "早", "config": cfg})
    assert r.status_code == 400
    assert "d1" in r.json()["detail"] and "d3" in r.json()["detail"]

    r = client.post("/api/candidates", json={"day": "d1", "shift": "早班", "config": cfg})
    assert r.status_code == 400
    assert "早" in r.json()["detail"] and "晚" in r.json()["detail"]


def test_candidates_get_rejects_unknown_slot(client):
    r = client.get("/api/candidates", params={"day": "zz", "shift": "早班"})
    assert r.status_code == 400 and "有效取值" in r.json()["message"]


def test_candidates_post_without_config_matches_get(client):
    """省略 config 必须与 GET 逐字节一致：GET 只是为既有调用方保留的同一条逻辑。"""
    got = client.get("/api/candidates", params={"day": "六", "shift": "早班", "taken": "E01,E02"}).json()
    posted = client.post(
        "/api/candidates",
        json={"day": "六", "shift": "早班", "taken": ["E01", "E02"]},
    ).json()
    assert posted == got


def test_candidates_employee_shape_matches_employee_def(client):
    """候选里的员工对象必须能和 /api/config/default 的 EmployeeDef 对上。

    否则同一个界面会拿到两种形状的员工，前端得写两套解析——配置化只做了一半。
    """
    default_emps = {e["id"]: e for e in client.get("/api/config/default").json()["config"]["employees"]}
    body = client.post("/api/candidates", json={"day": "六", "shift": "早班"}).json()
    one = body["candidates"][0]
    assert set(default_emps) and set(one) >= set(default_emps[one["id"]])
    for key, value in default_emps[one["id"]].items():
        assert one[key] == value, key
    # 老前端读的三个派生字段仍在，换成 POST 不用改解析
    assert set(one) - set(default_emps[one["id"]]) == {"available_days", "leave_days", "preference"}


def test_candidates_post_rejects_malformed_config(client):
    r = client.post("/api/candidates", json={"day": "d1", "shift": "早", "config": {"version": "x"}})
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "invalid_config"


def test_candidates_legacy_fields_unchanged(client):
    """默认配置下的老字段必须与改造前逐字段相同——GET 的既有调用方不该被这次扩展碰到。"""
    from app.data import EMPLOYEES

    body = client.get("/api/candidates", params={"day": "四", "shift": "晚班"}).json()
    assert body["candidates"]
    for e in body["candidates"]:
        assert {k: e[k] for k in EMPLOYEES[e["id"]].to_dict()} == EMPLOYEES[e["id"]].to_dict()


# ---------- 5.6 其他 ----------


def test_meta_stays_on_default_config(client):
    """契约明确 /api/meta 保持原样：老前端的技能标签与规则清单都指着它。"""
    m = client.get("/api/meta").json()
    assert set(m) == {"employees", "rules", "days", "shifts"}
    assert len(m["employees"]) == 20 and len(m["rules"]) == 9
    assert len(m["days"]) == 7 and len(m["shifts"]) == 2


def test_import_expects_slot_count_from_config(client):
    csv = "日期,班次,员工\nd1,早,E01 E05\nd1,中,E02 E06\n".encode()
    r = client.post(
        "/api/import",
        files={"file": ("roster.csv", csv, "text/csv")},
        data={"config": json.dumps(custom_config())},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # 期望格数由配置推导（3 天 × 3 班），写死 14 的话这张表会被判「缺失班次」
    assert body["stats"]["slots_expected"] == 9 and body["stats"]["slots_found"] == 2
    assert body["stats"]["resolved"] == 4 and body["stats"]["unresolved"] == 0
    assert [r["id"] for r in body["validation"]["rules"]] == ["MS", "A1", "R-08", "R-09"]
    assert any("完整周排班应有 9 个" in w for w in body["warnings"])


def test_import_without_config_still_expects_14(client):
    csv = "日期,班次,员工\n周一,早班,E01 E06\n".encode()
    body = client.post("/api/import", files={"file": ("r.csv", csv, "text/csv")}).json()
    assert body["stats"]["slots_expected"] == 14


def test_import_rejects_malformed_config_json(client):
    r = client.post(
        "/api/import",
        files={"file": ("r.csv", b"a,b\n", "text/csv")},
        data={"config": "{不是 JSON"},
    )
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "invalid_config"


def test_import_empty_config_field_is_treated_as_absent(client):
    """表单里的空字段是常态：当成一份空配置会把用户的门店换成 0 天 0 班。"""
    csv = "日期,班次,员工\n周一,早班,E01 E06\n".encode()
    body = client.post(
        "/api/import",
        files={"file": ("r.csv", csv, "text/csv")},
        data={"config": ""},
    ).json()
    assert body["stats"]["slots_expected"] == 14


def test_template_follows_config_dimensions(client):
    r = client.get("/api/import/template", params={"fmt": "csv", "config": json.dumps(custom_config())})
    assert r.status_code == 200
    lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    assert lines[0] == "日期,班次,员工"
    assert len(lines) == 1 + 9                       # 表头 + 3 天 × 3 班
    assert "9 行（3 天 × 3 班）" in r.text
    assert "早 / 中 / 晚" in r.text
    # 模板本身必须零违规：第一次导入就看到 passed，才不会被自造的红叉劝退。
    # 走 /api/import 而不是 /api/validate——模板的日期列写的是给人看的 label，
    # 由导入层的别名表折回 id，这一步也一起验了
    body = client.post(
        "/api/import",
        files={"file": ("template.csv", r.text.encode(), "text/csv")},
        data={"config": json.dumps(custom_config())},
    ).json()
    assert body["stats"]["slots_found"] == 9
    assert body["validation"]["passed"] is True, body["validation"]


def test_template_without_config_unchanged(client):
    text = client.get("/api/import/template", params={"fmt": "csv"}).text
    assert "14 行（7 天 × 2 班）" in text and "E01–E20" in text
    assert text.splitlines()[0].startswith("#")
