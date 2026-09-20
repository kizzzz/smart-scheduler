#!/usr/bin/env python3
"""真后端联调：配置向导 → 自检 → 生成 → 换人，全程不走 mock。

为什么单独写一个脚本而不复用 config_shots.py：那个跑在 `?mock=1` 下，验的是前端自己的契约实现；
这个必须打真 FastAPI，验的是前后端两份实现对同一份契约的理解是否一致——历史上出问题的正是这里
（默认规则数 9 vs 10、无解时 validation 的形状、换人接口的员工池）。
"""
import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get("CHECK_BASE", "http://127.0.0.1:5173")
# 线上默认模型是 glm-4.5-flash，实测一次生成 45–140 秒（两趟 LLM）；本地无 Key 时走规则
# 解析只要几秒。所以等待预算必须可调，写死任何一个值都会让另一种环境误报失败。
GEN_WAIT_MS = int(os.environ.get("GEN_WAIT_MS", "6000"))
SHOTS = Path(os.environ.get("SHOTS_DIR", "/tmp/real_api_shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

failures: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label}" + (f" — {extra}" if extra else ""))
    if not cond:
        failures.append(f"{label} {extra}".strip())


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})

        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        # 真接口下的响应留痕：断言要基于后端真的回了什么，而不是页面上看起来像什么
        seen: dict[str, dict] = {}

        def on_response(resp):
            if "/api/" not in resp.url:
                return
            key = resp.url.split("/api/")[1].split("?")[0]
            try:
                seen[key] = {"status": resp.status, "body": resp.json()}
            except Exception:
                seen[key] = {"status": resp.status, "body": None}

        page.on("response", on_response)

        # ---------- 1. 首屏：不强制配置，直接落生成页 ----------
        page.set_default_timeout(60000)
        # 不用 networkidle：页面有后端健康轮询，线上环境永远等不到「网络安静」，
        # 会在首屏就假失败。改成 DOM ready + 固定缓冲，断言本身仍基于真实响应体。
        page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        check("首屏落在生成页（不强制先配置）", "排班需求" in body or "生成排班" in body)
        check("首屏提示当前用示例配置", "示例" in body, body[:120].replace("\n", " "))
        cfg_resp = seen.get("config/default")
        check("GET /api/config/default 200", bool(cfg_resp) and cfg_resp["status"] == 200)
        if cfg_resp and cfg_resp["body"]:
            cfg = cfg_resp["body"]["config"]
            check("默认配置 10 条规则", len(cfg["rules"]) == 10, f"实际 {len(cfg['rules'])}")
            check("默认 7 天 × 2 班", len(cfg["scenario"]["days"]) == 7 and len(cfg["scenario"]["shifts"]) == 2)
        page.screenshot(path=SHOTS / "01_first_load_real.png", full_page=True)

        # ---------- 2. 真接口生成（无 LLM key，走规则解析降级） ----------
        page.fill("textarea", "正常排一版")
        page.get_by_role("button", name=re.compile("生成排班|重新生成")).first.click()
        page.wait_for_timeout(GEN_WAIT_MS)
        gen = seen.get("generate")
        check("POST /api/generate 200", bool(gen) and gen["status"] == 200, str(gen and gen["status"]))
        if gen and gen["body"]:
            sol = gen["body"].get("solution") or {}
            val = gen["body"].get("validation") or {}
            check("真接口排出 14 格", len(sol.get("slots", [])) == 14, f"实际 {len(sol.get('slots', []))}")
            check("校验 10 条规则全过", val.get("passed") is True and len(val.get("rules", [])) == 10,
                  f"passed={val.get('passed')} rules={len(val.get('rules', []))}")
            check("每格带 min_required", all("min_required" in s for s in sol.get("slots", [])))
        page.screenshot(path=SHOTS / "02_generated_real.png", full_page=True)

        # ---------- 3. 换人：候选必须来自真后端 ----------
        chips = page.locator("[data-slot] button").filter(has_text=re.compile("E\\d+"))
        if chips.count() > 0:
            chips.first.click()
            page.wait_for_timeout(2500)
            cand = seen.get("candidates")
            check("POST /api/candidates 200", bool(cand) and cand["status"] == 200, str(cand and cand["status"]))
            if cand and cand["body"]:
                ids = [c["id"] for c in cand["body"].get("candidates", [])]
                check("候选非空且来自员工档案", len(ids) > 0 and all(i.startswith("E") for i in ids), str(ids[:6]))
            page.screenshot(path=SHOTS / "03_swap_real.png", full_page=True)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        else:
            check("找到可点击的员工 chip", False, "看板上没有 data-slot chip")

        # ---------- 4. 配置页：改成会无解的配置，验证自检在前端就拦住 ----------
        page.get_by_role("button", name=re.compile("配置")).first.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=SHOTS / "04_config_step1_real.png", full_page=True)

        # 把普通日人数下限顶到上限（20 人档案 + 每人每天最多一个班 → 按天必定不够）。
        # Stepper 现在支持直接输入，所以这里一次 fill 到位，同时顺手验掉三条交互约定：
        # 越界按边界收下、留空回退原值、输入期间不被 clamp 打断。
        page.get_by_role("button", name=re.compile("规则")).first.click()
        page.wait_for_timeout(1200)
        # exact=True：+/- 按钮的 aria-label 也含这几个字，非精确匹配会先命中按钮
        box = page.get_by_label("普通日人数下限", exact=True)
        if box.count() > 0:
            box = box.first
            before = box.input_value()

            # a) 留空失焦 → 回退原值，且给出说明
            box.fill("")
            box.blur()
            page.wait_for_timeout(400)
            check("Stepper 留空失焦回退原值", box.input_value() == before, f"{before!r} -> {box.input_value()!r}")
            check("回退时给出说明", "已恢复为" in page.inner_text("body"))

            # b) 直接输入中间值：一次到位，不再需要连点
            box.fill("12")
            box.blur()
            page.wait_for_timeout(600)
            check("Stepper 可直接输入", box.input_value() == "12", box.input_value())

            # c) 超上限 → 按 max 收下（20 人全启用 → max 20），并说明
            box.fill("999")
            box.blur()
            page.wait_for_timeout(600)
            clamped = box.input_value()
            check("Stepper 越界按边界处理", clamped.isdigit() and 0 < int(clamped) < 999, clamped)
            check("越界时给出说明", "已按" in page.inner_text("body"))
            print(f"       （直接输入一次到位：{before} -> {clamped}，旧版需连点 {int(clamped) - int(before)} 次）")
            page.wait_for_timeout(3000)
            vc = seen.get("config/validate")
            check("POST /api/config/validate 被调用", bool(vc), str(vc and vc["status"]))
            if vc and vc["body"]:
                codes = [e.get("code") for e in vc["body"].get("errors", [])]
                check("自检报出阻塞级 error", vc["body"].get("ok") is False and len(codes) > 0, str(codes[:4]))
                check("命中按天容量检查", "daily_capacity_lt_demand" in codes or "supply_lt_demand" in codes, str(set(codes)))
            page_text = page.inner_text("body")
            check("页面用人话展示错误（不露 code）", "daily_capacity_lt_demand" not in page_text)
            check("生成入口被拦住", "必须修" in page_text or "修完才能生成" in page_text, "")
            page.screenshot(path=SHOTS / "05_config_blocked_real.png", full_page=True)
        else:
            check("找到人数下限 Stepper", False)

        browser.close()

    print("\n控制台错误：", len(console_errors))
    for e in console_errors[:5]:
        print("  ", e[:160])
    print("截图目录：", SHOTS)
    if failures:
        print("\n失败项：")
        for f in failures:
            print("  -", f)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
