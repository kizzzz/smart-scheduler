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
SHOTS = Path("/tmp/real_api_shots")
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
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)
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
        page.wait_for_timeout(6000)
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
        # 人数下限用的是 Stepper（+/- 按钮），没有可直接 fill 的输入框，所以这里只能连点。
        # 顺带说明一个可用性代价：从 4 调到 20 需要点 16 次，团队规模大的门店会很难受。
        page.get_by_role("button", name=re.compile("规则")).first.click()
        page.wait_for_timeout(1200)
        plus = page.get_by_role("button", name="普通日人数下限增加")
        if plus.count() > 0:
            clicks = 0
            while plus.first.is_enabled() and clicks < 30:
                plus.first.click()
                clicks += 1
            print(f"       （人数下限连点 {clicks} 次才到上限）")
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
