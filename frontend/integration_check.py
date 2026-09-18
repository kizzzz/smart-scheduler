"""真接口联调自检：跑本地 vite(5199) + 本地 FastAPI(8099)，不用 mock。

为什么要单独跑一遍：前端子任务的视觉自检走的是 mock，能证明 UI 不崩，
但证明不了「后端真实返回的字段名/结构和前端读的是同一套」。
这个脚本专门抓那类联调 bug。

用法：
  cd backend && python -m uvicorn app.main:app --port 8099 &
  cd frontend && VITE_API_TARGET=http://127.0.0.1:8099 npx vite --port 5199 &
  python frontend/integration_check.py

验收线上环境：CHECK_BASE=https://typexx.work python frontend/integration_check.py
"""
import asyncio
import os
import pathlib
import sys

from playwright.async_api import async_playwright

BASE = os.getenv("CHECK_BASE", "http://127.0.0.1:5199")
SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
OUT = pathlib.Path("/tmp/integration_shots")
OUT.mkdir(exist_ok=True)

errors: list[str] = []


async def shot(page, name):
    await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print(f"  shot -> {name}.png")


async def upload(page, filename):
    """ImportPanel 的 input[type=file] 是隐藏的，set_input_files 不需要它可见。"""
    fi = page.locator('input[type="file"]').first
    await fi.set_input_files(str(SAMPLES / filename))
    try:
        await page.get_by_role("button", name="应用为基线").first.wait_for(timeout=20000)
    except Exception:
        errors.append(f"{filename}: 20s 内未进入导入预览")
        return False
    await page.wait_for_timeout(700)
    return True


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        print("[1] 打开首页")
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(800)
        await shot(page, "01-home")

        print("[2] 模型选择器读真实 /api/models")
        # 触发按钮的 title 是固定文案，比 class 稳
        trigger = page.locator('button[title*="选择解析与解释使用的模型"]')
        if await trigger.count() == 0:
            errors.append("模型选择器不可用：/api/models 没接上（按钮 title 落到了 fallback 文案）")
        else:
            await trigger.first.click()
            await page.wait_for_timeout(500)
            body = await page.content()
            for must in ["GLM-4.5-Flash", "GLM-4-Flash-250414", "GLM-4V-Flash"]:
                if must not in body:
                    errors.append(f"模型清单缺少后端返回的 {must}")
            if "glm-z1-flash" not in body:
                errors.append("locked 模型未展示（应灰显并给出 reason）")
            if "不影响排班正确性" not in body:
                errors.append("boundary_note 未展示")
            await shot(page, "02-model-picker")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

        print("[3] 导入违规排班表（真解析 + 真校验器）")
        if await upload(page, "roster_long_violating.csv"):
            body = await page.content()
            # 后端实测 violation_count=5，命中 R-01 / R-04 / R-07 / R-08
            for rid in ["R-01", "R-04", "R-07", "R-08"]:
                if rid not in body:
                    errors.append(f"导入预览未展示违规规则 {rid}")
            await shot(page, "03-import-preview-violation")

            print("[4] 应用为基线")
            await page.get_by_role("button", name="应用为基线").first.click()
            await page.wait_for_timeout(1500)
            await shot(page, "04-applied-board")
            body = await page.content()
            if "早班" not in body or "周一" not in body:
                errors.append("应用后看板未渲染出排班格")
            if await page.get_by_role("button", name="应用为基线").count() > 0:
                errors.append("应用后仍停留在预览态")

        print("[5] 导入脏数据样例，验证 unresolved 展示")
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(700)
        if await upload(page, "roster_messy.csv"):
            body = await page.content()
            for must in ["小王", "E99"]:
                if must not in body:
                    errors.append(f"unresolved 未展示 {must}")
            await shot(page, "05-import-messy-unresolved")

        print("[6] 矩阵表布局")
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(700)
        if await upload(page, "roster_matrix_ok.csv"):
            body = await page.content()
            if "矩阵" not in body:
                errors.append("矩阵表未标注布局类型")
            await shot(page, "06-import-matrix")

        await browser.close()

    print("\n=== 结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("FAIL:", e)
        sys.exit(1)
    print("全部通过，截图在", OUT)


asyncio.run(main())
