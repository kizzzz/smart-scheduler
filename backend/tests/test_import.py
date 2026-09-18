"""排班表导入测试（全部离线：视觉链路 mock 掉，不打网络）。

覆盖的重点是导入功能真正的风险点，而不是「能不能读出格子」：
- 任何来源的 slots 都必须过同一个校验器，否则导入只是搬数据，看不出违规；
- 无法归一的 token 必须原样进 unresolved，绝不猜测姓名到工号的映射；
- 图片来源必须强制人工确认：视觉识别读错基线会让整份体检结论失真。
"""
from __future__ import annotations

import io
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import importer, llm  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    os.environ.pop("GLM_API_KEY", None)
    return TestClient(app)


def post_csv(client, text: str, name: str = "roster.csv") -> dict:
    r = client.post("/api/import", files={"file": (name, text.encode("utf-8"), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def slot_of(body: dict, day: str, shift: str) -> list:
    return next(s["employees"] for s in body["slots"] if s["day"] == day and s["shift"] == shift)


# ---------- 布局与别名 ----------


def test_long_table(client):
    body = post_csv(client, "日期,班次,员工\n周一,早班,E01 E06 E09 E12\n周一,晚班,E02 E07 E11 E18\n")
    assert body["ok"] is True and body["layout"] == "long"
    assert body["extractor"] == "deterministic" and body["model_used"] is None
    assert body["confidence"] == 1.0
    assert slot_of(body, "一", "早班") == ["E01", "E06", "E09", "E12"]
    assert body["stats"] == {
        "slots_found": 2, "slots_expected": 14, "assignments": 8, "resolved": 8, "unresolved": 0
    }


def test_matrix_table(client):
    body = post_csv(client, "日期,早班,晚班\n周一,E01 E06,E02 E07\n周二,E03,E10\n")
    assert body["layout"] == "matrix" and body["ok"] is True
    assert slot_of(body, "一", "晚班") == ["E02", "E07"]
    assert slot_of(body, "二", "早班") == ["E03"]


def test_day_name_aliases(client):
    body = post_csv(
        client,
        "weekday,shift,staff\n周一,早班,E01\n星期二,上午,E03\nmon,晚班,E02\n礼拜天,pm,E05\n",
    )
    days = {(s["day"], s["shift"]) for s in body["slots"]}
    assert days == {("一", "早班"), ("二", "早班"), ("一", "晚班"), ("日", "晚班")}


def test_employee_id_tolerance(client):
    """E1 / e01 / 全角 / 纯数字都是店长手打表格里的真实写法，必须归一到 E01。"""
    body = post_csv(client, "日期,班次,员工\n周一,早班,E1 e02 Ｅ０３ 04 5\n")
    assert slot_of(body, "一", "早班") == ["E01", "E02", "E03", "E04", "E05"]
    assert body["stats"]["unresolved"] == 0


def test_separator_tolerance(client):
    for text, name in [
        ("日期\t班次\t员工\n周一\t早班\tE01 E06\n", "roster.tsv"),
        ("日期;班次;员工\n周一;早班;E01,E06\n", "roster.csv"),
        ("日期|班次|员工\n周一|早班|E01、E06\n", "roster.csv"),
        ("日期,班次,员工\n周一,早班,E01/E06\n", "roster.csv"),
    ]:
        body = post_csv(client, text, name)
        assert body["ok"] is True, (name, body["warnings"])
        assert slot_of(body, "一", "早班") == ["E01", "E06"], text


def test_headerless_long_table_is_inferred(client):
    body = post_csv(client, "周一,早班,E01 E06\n周一,晚班,E02 E07\n")
    assert body["ok"] is True and body["layout"] == "long"
    assert slot_of(body, "一", "晚班") == ["E02", "E07"]


def test_unknown_shift_row_is_skipped_with_warning(client):
    body = post_csv(client, "日期,班次,员工\n周一,早班,E01\n周一,中班,E02\n")
    assert any("中班" in w for w in body["warnings"])
    assert len(body["slots"]) == 1


# ---------- 绝不猜测映射 ----------


def test_names_and_illegal_ids_go_to_unresolved(client):
    body = post_csv(client, "日期,班次,员工\n周一,早班,小王 E01 E25 张三\n")
    assert slot_of(body, "一", "早班") == ["E01"]        # 只留能确定的
    raws = [u["raw"] for u in body["unresolved"]]
    assert raws == ["小王", "E25", "张三"]
    assert all(u["where"] == "周一早班" and u["reason"] for u in body["unresolved"])
    assert "无姓名字段" in body["unresolved"][0]["reason"]
    assert "E01" not in raws                            # 姓名没有被硬塞给任何工号
    assert body["requires_confirmation"] is True        # 有未匹配就必须人工确认


def test_out_of_range_id_is_not_wrapped_around(client):
    body = post_csv(client, "日期,班次,员工\n周一,早班,E21 E99 E00\n")
    assert slot_of(body, "一", "早班") == []
    assert [u["raw"] for u in body["unresolved"]] == ["E21", "E99", "E00"]


# ---------- 导入必须过校验器 ----------


def test_import_runs_the_same_validator(client):
    """导入功能的核心价值：把一张违规的表拖进来，体检结论必须立刻报出来。

    这张表周一早班只有 3 名兼职：无店长值守（R-01）、人数不足（R-04）、
    且兼职周一不可工作（R-08）。
    """
    body = post_csv(client, "日期,班次,员工\n周一,早班,E13 E14 E19\n")
    v = body["validation"]
    assert v["passed"] is False and v["violation_count"] >= 3
    broken = {r["id"] for r in v["rules"] if not r["passed"]}
    assert {"R-01", "R-04", "R-08"} <= broken
    assert len(v["rules"]) == 9                     # 结构与 /api/validate 完全一致


def test_compliant_import_passes_validation(client):
    """模板本身必须零违规，否则店长第一次导入就被一屏红叉劝退。"""
    csv_text = client.get("/api/import/template", params={"fmt": "csv"}).text
    body = post_csv(client, csv_text, "template.csv")
    assert body["ok"] is True
    assert body["stats"]["slots_found"] == 14 and body["stats"]["unresolved"] == 0
    assert body["validation"]["passed"] is True
    assert body["requires_confirmation"] is False
    assert body["warnings"] == []


# ---------- 响应契约 ----------


def test_import_response_contract(client):
    body = post_csv(client, "日期,班次,员工\n周一,早班,E01 E06 E09 E12\n")
    assert set(body) == {
        "ok", "source", "extractor", "model_used", "layout", "slots", "stats",
        "unresolved", "warnings", "confidence", "requires_confirmation",
        "validation", "soft_metrics", "timing_ms",
    }
    assert set(body["slots"][0]) == {"day", "shift", "employees"}
    assert set(body["timing_ms"]) == {"extract_ms", "validate_ms", "total_ms"}
    assert set(body["soft_metrics"]) == {"preference_rate", "balance_score", "skill_redundancy"}


def test_parse_failure_is_200_not_5xx(client):
    """读不出来是业务结果，不是服务故障：前端要能把原因原话显示给店长。"""
    r = client.post("/api/import", files={"file": ("x.csv", "随便写点什么\nfoo,bar\n".encode(), "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["slots"] == []
    assert body["warnings"] and "模板" in body["warnings"][0]
    assert body["validation"]["passed"] is False and len(body["validation"]["rules"]) == 9
    assert body["soft_metrics"]["balance_score"] == 0.0     # 空表不许算出满分均衡度


def test_rejects_bad_extension_and_oversize(client):
    bad = client.post("/api/import", files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and ".exe" in bad.json()["message"]
    big = client.post(
        "/api/import",
        files={"file": ("a.csv", b"x" * (importer.MAX_UPLOAD_BYTES + 1), "text/csv")},
    )
    assert big.status_code == 400 and "上限" in big.json()["message"]


def test_huge_upload_is_rejected_before_parsing(client):
    """明显超限的上传要在解析 multipart 之前就被中间件挡掉，不能先缓冲完再报错。"""
    r = client.post(
        "/api/import",
        files={"file": ("a.csv", b"x" * (importer.MAX_UPLOAD_BYTES * 2), "text/csv")},
    )
    assert r.status_code == 400 and "上限" in r.json()["message"]


def test_template_download_headers(client):
    r = client.get("/api/import/template", params={"fmt": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("#") and "日期,班次,员工" in r.text
    assert r.text.count("\n") >= 15                 # 注释 + 表头 + 14 行


def test_template_rejects_other_format(client):
    r = client.get("/api/import/template", params={"fmt": "xlsx"})
    assert r.status_code == 400 and "csv" in r.json()["message"]


# ---------- Excel ----------


def _xlsx(rows: list) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_matrix_import(client):
    # 第二行日期刻意写成数字 1：Excel 会把它存成 1.0，不处理就匹配不上日期别名
    data = _xlsx([["日期", "早班", "晚班"], ["周二", "E03 E10", "E02 E07"], [1, "E01", "E18"]])
    r = client.post(
        "/api/import",
        files={"file": ("roster.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["source"] == "excel" and body["extractor"] == "deterministic"
    assert slot_of(body, "一", "早班") == ["E01"]
    assert slot_of(body, "二", "早班") == ["E03", "E10"]


def test_legacy_xls_gives_actionable_message(client):
    """openpyxl 不支持 BIFF 老格式；不引 xlrd，但必须给出可执行的下一步。"""
    r = client.post("/api/import", files={"file": ("old.xls", b"\xd0\xcf\x11\xe0rubbish", "application/vnd.ms-excel")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["source"] == "excel"
    assert "xlsx" in body["warnings"][0]


# ---------- 图片（mock 视觉模型） ----------


def _fake_vision(rows):
    async def _call(data_url: str, model=None):
        assert data_url.startswith("data:image/png;base64,")     # 必须是 data URL 形式
        return rows, model or "glm-4v-flash"

    return _call


def test_image_import_requires_api_key(client):
    r = client.post("/api/import", files={"file": ("roster.png", b"\x89PNG-fake", "image/png")})
    assert r.status_code == 400
    assert r.json()["message"] == "未配置 LLM，图片识别不可用，请改用 CSV/Excel 导入"


def test_image_import_rejects_unknown_vision_model(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    r = TestClient(app).post(
        "/api/import",
        files={"file": ("roster.png", b"\x89PNG-fake", "image/png")},
        data={"vision_model": "gpt-4-vision"},
    )
    assert r.status_code == 400 and "gpt-4-vision" in r.json()["message"]


def test_image_import_always_requires_confirmation(monkeypatch):
    """视觉识别读错工号，会让整份体检结论失真且店长很难发现 → 强制人工确认。"""
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    rows = [
        {"day": "一", "shift": "早班", "employees": ["E01", "E06", "E09", "E12"]},
        {"day": "一", "shift": "晚班", "employees": ["E02", "E07", "E11", "E18"]},
    ]
    monkeypatch.setattr(llm, "read_schedule_image", _fake_vision(rows))
    body = TestClient(app).post(
        "/api/import", files={"file": ("roster.png", b"\x89PNG-fake", "image/png")}
    ).json()
    assert body["ok"] is True and body["source"] == "image"
    assert body["extractor"] == "vision_llm" and body["model_used"] == "glm-4v-flash"
    assert body["layout"] == "image"
    assert body["requires_confirmation"] is True
    assert 0 < body["confidence"] < 1                 # 只读到 2/14 格，置信度必须掉下来
    assert any("核对" in w for w in body["warnings"])
    assert body["validation"]["passed"] is False      # 缺 12 格 → 校验必然报违规


def test_image_import_does_not_guess_illegal_ids(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    rows = [{"day": "一", "shift": "早班", "employees": ["E01", "E42", "小李"]}]
    monkeypatch.setattr(llm, "read_schedule_image", _fake_vision(rows))
    body = TestClient(app).post(
        "/api/import", files={"file": ("roster.png", b"\x89PNG-fake", "image/png")}
    ).json()
    assert slot_of(body, "一", "早班") == ["E01"]
    assert [u["raw"] for u in body["unresolved"]] == ["E42", "小李"]


def test_image_import_failure_is_readable(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "test-key")

    async def _boom(data_url: str, model=None):
        raise ValueError("视觉模型未返回可解析的 JSON")

    monkeypatch.setattr(llm, "read_schedule_image", _boom)
    r = TestClient(app).post("/api/import", files={"file": ("roster.png", b"\x89PNG-fake", "image/png")})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is False
    assert body["slots"] == [] and body["confidence"] == 0.0
    assert "CSV/Excel" in body["warnings"][0]          # 失败要给替代路径


def test_image_confidence_full_grid(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    rows = [
        {"day": d, "shift": s, "employees": ["E01", "E06"]}
        for d in ["一", "二", "三", "四", "五", "六", "日"]
        for s in ["早班", "晚班"]
    ]
    monkeypatch.setattr(llm, "read_schedule_image", _fake_vision(rows))
    body = TestClient(app).post(
        "/api/import", files={"file": ("roster.png", b"\x89PNG-fake", "image/png")}
    ).json()
    assert body["stats"]["slots_found"] == 14 and body["confidence"] == 1.0
    assert body["requires_confirmation"] is True      # 满格也照样要人工确认
