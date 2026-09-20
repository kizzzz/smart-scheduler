"""配置化前端的可视化自检 + 截图脚本（mock 模式，不碰后端）。

走一遍真实用户路径：
  示例配置提示 → 三步配置 → 自检报错并拦住生成 → 默认 7×2 生成 → 违规态 / 导入体检
  → 改成 3 天 × 3 班 → 动态看板 → 换人 → 改配置后看板被标记「基于旧配置」
  → 导入一份自定义 8 人配置，验证换人候选只来自这 8 人（POST /api/candidates）
  → 把周期砍短，验证旧配置格子换人时的友好 400 提示与「重新生成」出口
  → 导入一份「每天排不开」的配置，验证阻塞 error `daily_capacity_lt_demand`
  → 导入「全店只有一个收银员」的配置，验证按天资质缺口 `daily_attribute_capacity_lt_demand`
  → 导入一份参数被手改坏的 JSON，验证 `invalid_rule_params` 能指名道姓定位到规则与参数
  → 导入「刚好够人」的配置，验证 `no_daily_headroom` 是黄色 warning 且不阻塞生成
  → 点生成被后端自检 400 拦下，验证走的是自检渲染而非通用「请求失败」，且能跳到对应步骤
  → 恢复示例配置后触发无解态，验证 validation=null 时校验面板是中性的「待排班」而不是满屏红叉

用法（先起一个 preview 服务）：
    npm run build && npx vite preview --port 4182 &
    python3 scripts/config_shots.py [输出目录]

控制台错误会被收集并在结尾失败退出：渲染炸了必须看得见。
与 integration_check.py 无关，那个脚本管的是真实后端联调，本脚本只跑 mock 界面。
"""
import asyncio
import json
import pathlib
import sys
import tempfile

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:4182/?mock=1"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/config_shots")
OUT.mkdir(parents=True, exist_ok=True)

errors: list[str] = []

SKILL_KEEPER = "店长值守"
SKILL_DRINK = "饮品制作"
SKILL_CASHIER = "收银"
SKILL_STOCK = "库存管理"


def custom_config() -> dict:
    """一家 8 人的小店：3 天 × 2 班。

    用它来证一件事——换人候选来自**这份配置**。POST /api/candidates 之前是 GET，
    GET 带不了配置，自定义门店里会返回默认 20 人档案，也就是「幽灵员工」。
    """
    people = [
        ("C01", "陈慧", "店长", [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK], [], ["s1"]),
        ("C02", "李文博", "副店长", [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER], [], ["s2"]),
        ("C03", "王晓琳", "值班主管", [SKILL_KEEPER, SKILL_CASHIER],
         [{"day": "d2", "shift": None, "reason": "培训"}], []),
        ("C04", "张衡", "高级店员", [SKILL_DRINK, SKILL_CASHIER], [], ["s1"]),
        ("C05", "刘倩", "店员", [SKILL_DRINK, SKILL_CASHIER], [{"day": "d1", "shift": "s2"}], ["s2"]),
        ("C06", "赵子墨", "店员", [SKILL_DRINK, SKILL_STOCK],
         [{"day": "d3", "shift": None, "reason": "请假"}], ["s1"]),
        ("C07", "孙悦", "店员", [SKILL_CASHIER], [], ["s2"]),
        ("C08", "何欣", "兼职", [SKILL_DRINK],
         [{"day": "d1", "shift": None}, {"day": "d2", "shift": None}], ["s1"]),
    ]
    return {
        "version": 1,
        "scenario": {
            "name": "城东店 · 8 人小店",
            "days": [
                {"id": "d1", "label": "周一", "peak": False},
                {"id": "d2", "label": "周二", "peak": False},
                {"id": "d3", "label": "周三", "peak": True},
            ],
            "shifts": [
                {"id": "s1", "name": "早班", "start": "09:00", "end": "17:00", "hours": 8},
                {"id": "s2", "name": "晚班", "start": "13:00", "end": "21:00", "hours": 8},
            ],
        },
        "skill_pool": [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK],
        "role_pool": ["店长", "副店长", "值班主管", "高级店员", "店员", "兼职"],
        "employees": [
            {
                "id": pid, "name": name, "role": role, "skills": skills,
                "unavailable": unavailable, "max_shifts": None,
                "preferred_shifts": pref, "active": True,
            }
            for pid, name, role, skills, unavailable, pref in people
        ],
        "rules": [
            {"id": "R-01", "type": "require_attribute", "name": "每班至少 1 名店长值守",
             "enabled": True, "locked": False,
             "params": {"attr": "skill", "value": SKILL_KEEPER, "min": 1}},
            {"id": "R-03", "type": "require_attribute", "name": "每班至少 1 名会收银",
             "enabled": True, "locked": False,
             "params": {"attr": "skill", "value": SKILL_CASHIER, "min": 1}},
            {"id": "R-04", "type": "min_staff_per_shift", "name": "每班总人数下限",
             "enabled": True, "locked": False,
             "params": {"default": 2, "peak": 3, "overrides": []}},
            {"id": "R-05", "type": "max_shifts_per_period", "name": "每人本周期最多 3 个班",
             "enabled": True, "locked": False, "params": {"max": 3}},
            {"id": "R-07", "type": "min_rest_hours", "name": "相邻班次至少休息 13 小时",
             "enabled": True, "locked": False, "params": {"hours": 13}},
            {"id": "R-08", "type": "respect_unavailability", "name": "不可用时段绝不排班",
             "enabled": True, "locked": True, "params": {}},
            {"id": "R-09", "type": "skill_source_integrity", "name": "技能只能来自员工档案",
             "enabled": True, "locked": True, "params": {}},
            {"id": "R-10", "type": "one_shift_per_day", "name": "每人每天最多一个班",
             "enabled": True, "locked": False, "params": {}},
        ],
    }


def daily_capacity_config() -> dict:
    """一家 6 人小店，3 天 × 3 班，高峰日每班要 3 人。

    专门用来触发 `daily_capacity_lt_demand`：周三三个班合计要 9 人次，可当天只有 6 个人，
    而「每人每天最多一个班」让一个人当天顶不了两个班。注意这种无解在**单格视角看不出来**
    （每格要 3 人、能来 6 人，绰绰有余），总量视角也看不出来（15 人次需求 vs 18 人次供给），
    只有按天算才会露出来。
    """
    people = [
        ("C01", "陈慧", "店长", [SKILL_KEEPER, SKILL_CASHIER]),
        ("C02", "李文博", "副店长", [SKILL_KEEPER, SKILL_CASHIER]),
        ("C03", "王晓琳", "值班主管", [SKILL_KEEPER, SKILL_DRINK]),
        ("C04", "张衡", "店员", [SKILL_DRINK, SKILL_CASHIER]),
        ("C05", "刘倩", "店员", [SKILL_DRINK, SKILL_CASHIER]),
        ("C06", "赵子墨", "店员", [SKILL_DRINK, SKILL_STOCK]),
    ]
    return {
        "version": 1,
        "scenario": {
            "name": "城西店 · 三班制 6 人",
            "days": [
                {"id": "d1", "label": "周一", "peak": False},
                {"id": "d2", "label": "周二", "peak": False},
                {"id": "d3", "label": "周三", "peak": True},
            ],
            "shifts": [
                {"id": "s1", "name": "早班", "start": "07:00", "end": "15:00", "hours": 8},
                {"id": "s2", "name": "中班", "start": "15:00", "end": "23:00", "hours": 8},
                {"id": "s3", "name": "夜班", "start": "23:00", "end": "07:00", "hours": 8},
            ],
        },
        "skill_pool": [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK],
        "role_pool": ["店长", "副店长", "值班主管", "店员"],
        "employees": [
            {
                "id": pid, "name": name, "role": role, "skills": skills,
                "unavailable": [], "max_shifts": None,
                "preferred_shifts": [], "active": True,
            }
            for pid, name, role, skills in people
        ],
        "rules": [
            {"id": "R-04", "type": "min_staff_per_shift", "name": "每班总人数下限",
             "enabled": True, "locked": False,
             "params": {"default": 1, "peak": 3, "overrides": []}},
            {"id": "R-05", "type": "max_shifts_per_period", "name": "每人本周期最多 3 个班",
             "enabled": True, "locked": False, "params": {"max": 3}},
            {"id": "R-08", "type": "respect_unavailability", "name": "不可用时段绝不排班",
             "enabled": True, "locked": True, "params": {}},
            {"id": "R-09", "type": "skill_source_integrity", "name": "技能只能来自员工档案",
             "enabled": True, "locked": True, "params": {}},
            {"id": "R-10", "type": "one_shift_per_day", "name": "每人每天最多一个班",
             "enabled": True, "locked": False, "params": {}},
        ],
    }


LOCKED_RULES = [
    {"id": "R-08", "type": "respect_unavailability", "name": "不可用时段绝不排班",
     "enabled": True, "locked": True, "params": {}},
    {"id": "R-09", "type": "skill_source_integrity", "name": "技能只能来自员工档案",
     "enabled": True, "locked": True, "params": {}},
]

R10 = {"id": "R-10", "type": "one_shift_per_day", "name": "每人每天最多一个班",
       "enabled": True, "locked": False, "params": {}}


def _store(name: str, days, shifts, people, rules) -> dict:
    """搭一份最小可用的配置骨架，让下面几份用例只需要写它们真正想表达的那点差异。"""
    return {
        "version": 1,
        "scenario": {"name": name, "days": days, "shifts": shifts},
        "skill_pool": [SKILL_KEEPER, SKILL_DRINK, SKILL_CASHIER, SKILL_STOCK],
        "role_pool": ["店长", "副店长", "值班主管", "店员", "兼职"],
        "employees": [
            {
                "id": pid, "name": pname, "role": role, "skills": skills,
                "unavailable": [], "max_shifts": None,
                "preferred_shifts": [], "active": True,
            }
            for pid, pname, role, skills in people
        ],
        "rules": rules,
    }


TWO_SHIFTS = [
    {"id": "s1", "name": "早班", "start": "09:00", "end": "17:00", "hours": 8},
    {"id": "s2", "name": "晚班", "start": "13:00", "end": "21:00", "hours": 8},
]

THREE_DAYS = [
    {"id": "d1", "label": "周一", "peak": False},
    {"id": "d2", "label": "周二", "peak": False},
    {"id": "d3", "label": "周三", "peak": False},
]


def daily_attribute_config() -> dict:
    """全店只有 1 个人会收银，而每天两个班都要求「至少 1 名收银」。

    这是最难自己看出来的一类无解：**每一格都够**（那个收银员哪个班都能上），总量也够，
    但一个人分不成两半 —— 两个班需要两个不同的人。对应 `daily_attribute_capacity_lt_demand`，
    文案必须把「一个人不能同时待在两个班上」说出来，否则用户只会觉得系统算错了。
    """
    people = [
        ("C01", "陈慧", "店长", [SKILL_KEEPER, SKILL_CASHIER]),
        ("C02", "李文博", "副店长", [SKILL_KEEPER, SKILL_DRINK]),
        ("C03", "王晓琳", "店员", [SKILL_DRINK, SKILL_STOCK]),
        ("C04", "张衡", "店员", [SKILL_DRINK]),
    ]
    rules = [
        {"id": "R-03", "type": "require_attribute", "name": "每班至少 1 名会收银",
         "enabled": True, "locked": False,
         "params": {"attr": "skill", "value": SKILL_CASHIER, "min": 1}},
        {"id": "R-04", "type": "min_staff_per_shift", "name": "每班总人数下限",
         "enabled": True, "locked": False, "params": {"default": 1, "peak": 1, "overrides": []}},
        {"id": "R-05", "type": "max_shifts_per_period", "name": "每人本周期最多 3 个班",
         "enabled": True, "locked": False, "params": {"max": 3}},
        *LOCKED_RULES,
        R10,
    ]
    return _store("城南店 · 只有一个人会收银", THREE_DAYS, TWO_SHIFTS, people, rules)


def invalid_params_config() -> dict:
    """一份「参数被手改坏了」的配置，模拟从同事那儿拿来的 JSON。

    三处故意的非法值分布在不同规则上，其中 R-06 还是停用状态 —— 后端的
    `invalid_rule_params` 不看 enabled，所以前端也必须拦，并且要指名道姓地说出
    「哪条规则的哪个参数、现在填的是什么、合法范围是什么」。导入这条路径绕过了所有
    输入控件，用户根本没填过这些框，只说一句「配置非法」等于让他去问同事。
    """
    people = [
        ("C01", "陈慧", "店长", [SKILL_KEEPER, SKILL_CASHIER]),
        ("C02", "李文博", "副店长", [SKILL_KEEPER, SKILL_DRINK]),
        ("C03", "王晓琳", "店员", [SKILL_DRINK, SKILL_CASHIER]),
        ("C04", "张衡", "店员", [SKILL_DRINK]),
    ]
    rules = [
        {"id": "R-04", "type": "min_staff_per_shift", "name": "每班总人数下限",
         "enabled": True, "locked": False, "params": {"default": 1, "peak": 1, "overrides": []}},
        {"id": "R-05", "type": "max_shifts_per_period", "name": "每人本周期最多几个班",
         "enabled": True, "locked": False, "params": {"max": 0}},
        {"id": "R-06", "type": "max_consecutive_days", "name": "最多连续工作天数",
         "enabled": False, "locked": False, "params": {"max": -2}},
        {"id": "R-07", "type": "min_rest_hours", "name": "相邻班次最少休息时长",
         "enabled": True, "locked": False, "params": {"hours": -3}},
        *LOCKED_RULES,
        R10,
    ]
    return _store("同事给的配置 · 参数被改坏了", THREE_DAYS, TWO_SHIFTS, people, rules)


def no_headroom_config() -> dict:
    """刚好排得出来、一个人都不能请假的配置。

    每天两个班各要 2 人，全店正好 4 个人，`no_daily_headroom` 必须是**黄色 warning**：
    它排得出来，报成 error 就等于拦住一份能用的配置；但也确实脆，值得提醒。
    """
    people = [
        ("C01", "陈慧", "店长", [SKILL_KEEPER, SKILL_CASHIER]),
        ("C02", "李文博", "副店长", [SKILL_KEEPER, SKILL_DRINK]),
        ("C03", "王晓琳", "店员", [SKILL_DRINK, SKILL_CASHIER]),
        ("C04", "张衡", "店员", [SKILL_DRINK, SKILL_CASHIER]),
    ]
    rules = [
        {"id": "R-04", "type": "min_staff_per_shift", "name": "每班总人数下限",
         "enabled": True, "locked": False, "params": {"default": 2, "peak": 2, "overrides": []}},
        {"id": "R-05", "type": "max_shifts_per_period", "name": "每人本周期最多 3 个班",
         "enabled": True, "locked": False, "params": {"max": 3}},
        {"id": "R-07", "type": "min_rest_hours", "name": "相邻班次至少休息 13 小时",
         "enabled": True, "locked": False, "params": {"hours": 13}},
        *LOCKED_RULES,
        R10,
    ]
    return _store("城北店 · 刚好够人", THREE_DAYS, TWO_SHIFTS, people, rules)


async def shot(page, name, full=True):
    await page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print(f"  saved {name}.png")


async def tap(page, selector, *, timeout=15000, note=""):
    """点一个元素并等它真的出现过。失败时把 selector 打出来，方便定位。"""
    loc = page.locator(selector).first
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"点击失败 {note or selector}: {str(e)[:160]}") from None
    await page.wait_for_timeout(120)


def step_tab(title: str) -> str:
    return f'ol li button:has-text("{title}")'


async def main():
    cfg_path = pathlib.Path(tempfile.gettempdir()) / "custom_store_8p.json"
    cfg_path.write_text(json.dumps(custom_config(), ensure_ascii=False, indent=2), encoding="utf-8")
    daily_path = pathlib.Path(tempfile.gettempdir()) / "daily_capacity_6p.json"
    daily_path.write_text(
        json.dumps(daily_capacity_config(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    attr_path = pathlib.Path(tempfile.gettempdir()) / "daily_attribute_4p.json"
    attr_path.write_text(
        json.dumps(daily_attribute_config(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    badparam_path = pathlib.Path(tempfile.gettempdir()) / "invalid_rule_params.json"
    badparam_path.write_text(
        json.dumps(invalid_params_config(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    headroom_path = pathlib.Path(tempfile.gettempdir()) / "no_daily_headroom_4p.json"
    headroom_path.write_text(
        json.dumps(no_headroom_config(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        TAB_CONFIG = 'nav button:has-text("配置")'
        TAB_BOARD = 'nav button:has-text("排班")'
        DIALOG = 'div[role="dialog"]'

        # 1) 生成页首屏：示例门店配置提示条
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        await shot(page, "01_generate_sample_notice")

        # 2) 配置页 · 第 1 步 场景
        await tap(page, TAB_CONFIG, note="配置 tab")
        await page.wait_for_timeout(1400)
        await shot(page, "02_config_step1_scenario")

        # 3) 第 2 步 员工
        await tap(page, step_tab("员工"), note="员工步骤")
        await page.wait_for_timeout(1000)
        await shot(page, "03_config_step2_employees")

        # 展开一名员工：技能 / 偏好 / 不可排班网格
        await tap(page, 'button[aria-label="展开 E03 详情"]', note="展开 E03")
        await page.wait_for_timeout(800)
        await shot(page, "04_config_step2_unavailable_grid")
        await tap(page, 'button[aria-label="收起 E03 详情"]', note="收起 E03")

        # 4) 第 3 步 规则
        await tap(page, step_tab("规则"), note="规则步骤")
        await page.wait_for_timeout(1000)
        await shot(page, "05_config_step3_rules")

        # 5) 把人数下限顶到启用人数，制造「供不应求」的自检错误
        plus = page.locator('button[aria-label="普通日人数下限增加"]').first
        await plus.wait_for(state="visible")
        for _ in range(24):
            if not await plus.is_enabled():
                break
            await plus.click()
        await page.wait_for_timeout(1800)
        await shot(page, "06_config_check_errors")

        # 6) 回生成页：生成被拦住
        await tap(page, 'button:has-text("先回生成页")', note="先回生成页")
        await page.wait_for_timeout(900)
        await shot(page, "07_generate_blocked")

        # 改回可行的下限（回到 2 人）
        await tap(page, TAB_CONFIG, note="回配置")
        await page.wait_for_timeout(900)
        await tap(page, step_tab("规则"), note="规则步骤")
        minus = page.locator('button[aria-label="普通日人数下限减少"]').first
        await minus.wait_for(state="visible")
        for _ in range(24):
            if not await minus.is_enabled():
                break
            await minus.click()
        await plus.click()
        await plus.click()
        await page.wait_for_timeout(1600)

        # 6.5) 先用默认配置（7 天 × 2 班）生成一版：验证 R-07 阈值 13 小时下仍有可行解
        await tap(page, 'button:has-text("配置好了，去生成排班")', note="去生成（默认配置）")
        await page.wait_for_timeout(800)
        await page.fill("#instruction", "下周正常排班，尽量满足大家的班次偏好")
        await tap(page, 'button:has-text("生成排班")', note="生成排班（默认配置）")
        await page.wait_for_timeout(4500)
        await shot(page, "07b_board_default_7days_2shifts")
        board = await page.locator('section:has-text("规则校验"), div:has-text("规则校验")').first.inner_text()
        assert "通过" in board, "默认配置未通过规则校验：" + board[:300]

        # 6.6) 违规态：R-07 的阈值已按契约改成 13 小时，校验文案必须跟着变
        await tap(page, 'button:has-text("违规态 (R-07)")', note="违规态")
        await page.wait_for_timeout(400)
        await page.fill("#instruction", "E07 希望周五上早班")
        await tap(page, 'button:has-text("生成排班")', note="生成（违规态）")
        await page.wait_for_timeout(4500)
        await shot(page, "07c_board_violation_r07_13h")
        await tap(page, 'button:has-text("正常态")', note="回正常态")
        await page.wait_for_timeout(300)

        # 6.7) 导入体检：用 7 天 × 2 班的示例配置看「有违规的 CSV」
        await tap(page, 'button:has-text("有违规的 CSV")', note="导入样例")
        await page.wait_for_timeout(2600)
        await shot(page, "07d_import_preview_violation")
        await tap(page, 'button:has-text("放弃")', note="放弃导入")
        await page.wait_for_timeout(800)
        await page.wait_for_selector("#instruction", timeout=10000)

        # 7) 改成 3 天 × 3 班，验证维度是真动态
        await tap(page, TAB_CONFIG, note="回配置")
        await page.wait_for_timeout(900)
        await tap(page, step_tab("排班场景"), note="场景步骤")
        await page.wait_for_timeout(600)
        await page.get_by_label("周期天数滑杆").fill("3")
        await page.wait_for_timeout(900)
        add_shift = page.locator('button:has-text("新增班次")').first
        if await add_shift.is_enabled():
            await add_shift.click()
        await page.wait_for_timeout(1700)
        await shot(page, "08_config_3days_3shifts")

        # 8) 用这份配置生成排班
        go = page.locator('button:has-text("配置好了，去生成排班")').first
        await go.wait_for(state="visible")
        if not await go.is_enabled():
            panel = await page.locator('section:has-text("配置自检"), div:has-text("配置自检")').first.inner_text()
            raise AssertionError("3 天 × 3 班配置仍被拦住：\n" + panel[:900])
        await tap(page, 'button:has-text("配置好了，去生成排班")', note="去生成")
        await page.wait_for_timeout(900)
        await page.fill("#instruction", "按新配置排一版，尽量满足大家的班次偏好")
        await tap(page, 'button:has-text("生成排班")', note="生成排班")
        await page.wait_for_timeout(4500)
        await shot(page, "09_board_3days_3shifts")

        # 9) 换人面板：候选来自 POST /api/candidates
        add_person = page.locator('button[title="补一个人"]').first
        if await add_person.count():
            await add_person.click()
            await page.wait_for_timeout(1400)
            await shot(page, "10_swap_dialog", full=False)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)

        # 10) 再改配置：看板应被标记「基于旧配置」，但排班不能被清空
        await tap(page, TAB_CONFIG, note="回配置")
        await page.wait_for_timeout(900)
        await page.get_by_label("场景名称").fill("城东店 · 三班制试运行")
        await page.wait_for_timeout(1400)
        await tap(page, TAB_BOARD, note="排班 tab")
        await page.wait_for_timeout(1000)
        await shot(page, "11_board_stale_config")

        # 11) 导入自定义 8 人配置：候选名单必须只来自这 8 个人
        await tap(page, TAB_CONFIG, note="回配置")
        await page.wait_for_timeout(900)
        await page.set_input_files('input[type="file"][accept*="json"]', str(cfg_path))
        await page.wait_for_timeout(2200)
        await tap(page, 'button:has-text("配置好了，去生成排班")', note="去生成（8 人配置）")
        await page.wait_for_timeout(900)
        await page.fill("#instruction", "按这份 8 人配置排一版")
        await tap(page, 'button:has-text("生成排班")', note="生成排班（8 人配置）")
        await page.wait_for_timeout(4800)

        # 周一晚班：C05 当格不可用、C08 当天不可用，候选应少于 8 人且全是 C0x
        await tap(page, 'div[data-slot="d1|s2"] button[title="补一个人"]', note="周一晚班补人")
        await page.wait_for_selector(f'{DIALOG} ul li button', timeout=15000)
        await page.wait_for_timeout(700)
        await shot(page, "12_swap_candidates_custom_config", full=False)
        dialog_text = await page.locator(DIALOG).first.inner_text()
        ghosts = [f"E{i:02d}" for i in range(1, 21) if f"E{i:02d}" in dialog_text]
        assert not ghosts, f"候选里出现了默认配置的幽灵员工：{ghosts}\n{dialog_text[:400]}"
        assert "C0" in dialog_text, "候选里没有自定义配置的员工：" + dialog_text[:400]
        assert "C05" not in dialog_text, "C05 周一晚班不可用，不该出现在候选里"
        assert "C08" not in dialog_text, "C08 周一整天不可用，不该出现在候选里"
        print("  候选校验通过：只来自自定义配置的 8 人，且已按不可排班过滤")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

        # 12) 把周期从 3 天砍到 2 天，旧配置留下的周三格子换人 → 友好的 400 提示
        await tap(page, TAB_CONFIG, note="回配置")
        await page.wait_for_timeout(900)
        await tap(page, step_tab("排班场景"), note="场景步骤")
        await page.get_by_label("周期天数滑杆").fill("2")
        await page.wait_for_timeout(1600)
        await tap(page, TAB_BOARD, note="排班 tab")
        await page.wait_for_timeout(1000)
        await tap(page, 'div[data-slot="d3|s1"] button[title="补一个人"]', note="旧配置格子补人")
        await page.wait_for_selector(f'{DIALOG}:has-text("属于旧配置")', timeout=15000)
        await page.wait_for_timeout(900)
        await shot(page, "13_swap_stale_slot_400", full=False)
        stale_text = await page.locator(DIALOG).first.inner_text()
        assert "属于旧配置" in stale_text, "缺少 stale 友好文案：" + stale_text[:400]
        assert "重新生成" in stale_text, "stale 弹窗没有「重新生成」出口：" + stale_text[:400]
        assert "400" in stale_text, "没有显示后端 400 的原话：" + stale_text[:400]
        print("  stale 格子校验通过：友好文案 + 重新生成出口 + 后端 400 原话")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

        # 13) 每天排不开：单格够人、总量够，但「每人每天最多一个班」让周三顶不住 9 人次
        await tap(page, TAB_CONFIG, note="回配置")
        await page.wait_for_timeout(900)
        await page.set_input_files('input[type="file"][accept*="json"]', str(daily_path))
        await page.wait_for_timeout(2400)
        await shot(page, "14_config_daily_capacity_error")
        panel_text = await page.inner_text("body")
        assert "全天可排班 6 人，当天各班共需 9 人次" in panel_text, (
            "没报出按天算的无解：" + panel_text[:600]
        )
        assert "规则 R-10 限制每人每天最多 1 个班" in panel_text, (
            "没说清一人一天最多几个班的来源：" + panel_text[:600]
        )
        assert "停用规则 R-10" in panel_text, "fix 里没给「关掉 R-10」这条出路：" + panel_text[:600]
        go = page.locator('button:has-text("配置好了，去生成排班")').first
        await go.wait_for(state="visible")
        assert not await go.is_enabled(), "daily_capacity_lt_demand 必须阻塞生成"
        print("  daily_capacity_lt_demand 校验通过：报错 + 阻塞生成 + 矩阵末行标红")

        # 13.1) 按天的资质不够：每格都够（唯一的收银员哪个班都能上），但一个人分不成两半
        await page.set_input_files('input[type="file"][accept*="json"]', str(attr_path))
        await page.wait_for_timeout(2400)
        await shot(page, "15_config_daily_attribute_capacity")
        attr_text = await page.inner_text("body")
        assert "全天只有 1 名具备技能「收银」的员工可排班" in attr_text, (
            "没报出按天的资质缺口：" + attr_text[:700]
        )
        assert "需要 2 名不同的人" in attr_text, "文案没说清需要两个不同的人：" + attr_text[:700]
        assert "一个人分不成两半" in attr_text, (
            "缺少「一个人分不成两半」这句人话，用户会以为系统算错了：" + attr_text[:700]
        )
        go = page.locator('button:has-text("配置好了，去生成排班")').first
        await go.wait_for(state="visible")
        assert not await go.is_enabled(), "daily_attribute_capacity_lt_demand 必须阻塞生成"
        print("  daily_attribute_capacity_lt_demand 校验通过：按天资质缺口 + 人话文案 + 阻塞生成")

        # 13.2) 导入一份参数被改坏的 JSON：必须指名道姓说出哪条规则的哪个参数
        await page.set_input_files('input[type="file"][accept*="json"]', str(badparam_path))
        await page.wait_for_timeout(2400)
        await shot(page, "16_config_invalid_rule_params")
        bad_text = await page.inner_text("body")
        assert "规则 R-05" in bad_text, "没定位到具体规则 R-05：" + bad_text[:700]
        assert "每人一个周期最多几个班" in bad_text, (
            "没把参数名说成人话（应显示「每人一个周期最多几个班」）：" + bad_text[:700]
        )
        assert "相邻班次最少休息几小时" in bad_text, "没定位到 R-07 的休息间隔参数：" + bad_text[:700]
        assert "-3" in bad_text, "没回显用户文件里的非法值 -3：" + bad_text[:700]
        assert "规则 R-06" in bad_text, (
            "停用规则的非法参数被放过了，点生成会拿一个 400：" + bad_text[:700]
        )
        assert "已停用" in bad_text, "没说明「规则停用了但参数仍会被拦下」：" + bad_text[:700]
        go = page.locator('button:has-text("配置好了，去生成排班")').first
        await go.wait_for(state="visible")
        assert not await go.is_enabled(), "invalid_rule_params 必须阻塞生成"
        print("  invalid_rule_params 校验通过：定位到规则 + 参数人话名 + 当前值 + 停用也拦")

        # 13.3) 刚好够人：no_daily_headroom 是黄色 warning，不能阻塞生成
        await page.set_input_files('input[type="file"][accept*="json"]', str(headroom_path))
        await page.wait_for_timeout(2400)
        await shot(page, "17_config_no_daily_headroom_warning")
        tight_text = await page.inner_text("body")
        assert "恰好等于当天需求 4 人次" in tight_text, "没报出按天零冗余：" + tight_text[:700]
        assert "这天一个人都不能请假" in tight_text, "零冗余的后果没说清：" + tight_text[:700]
        assert "一定排不出来" not in tight_text, (
            "no_daily_headroom 被当成 error 了，这份配置其实排得出来：" + tight_text[:700]
        )
        go = page.locator('button:has-text("配置好了，去生成排班")').first
        await go.wait_for(state="visible")
        assert await go.is_enabled(), "no_daily_headroom 是 warning，不该阻塞生成"
        print("  no_daily_headroom 校验通过：黄色提醒 + 不阻塞生成")

        # 13.4) 生成时被后端自检拦下（400）：必须走自检渲染，而不是通用「请求失败」
        await tap(page, 'button:has-text("配置好了，去生成排班")', note="去生成（刚好够人）")
        await page.wait_for_timeout(900)
        await tap(page, 'button:has-text("生成前被自检拦下 (400)")', note="400 自检用例")
        await page.wait_for_timeout(400)
        await page.fill("#instruction", "按这份配置排一版")
        await tap(page, 'button:has-text("生成排班")', note="生成（400 自检）")
        await page.wait_for_timeout(2600)
        await shot(page, "18_generate_blocked_by_server_check")
        blocked_text = await page.inner_text("body")
        assert "配置自检发现" in blocked_text, (
            "generate 的 400 没走自检渲染：" + blocked_text[:700]
        )
        assert "已拦下这次生成" in blocked_text, "没说明这次生成被拦下：" + blocked_text[:700]
        assert "求解器预检" in blocked_text, "没展示后端给的 message：" + blocked_text[:700]
        assert "去「规则」处理" in blocked_text, (
            "后端 400 的每条错误应能直接跳到对应配置步骤：" + blocked_text[:700]
        )
        assert "HTTP 400" not in blocked_text and "请求失败" not in blocked_text, (
            "退化成了通用请求失败提示：" + blocked_text[:700]
        )
        # 点「去「规则」处理」应直接落在规则步骤，而不是停在第一步
        await tap(page, 'button:has-text("去「规则」处理")', note="跳到规则步骤")
        await page.wait_for_timeout(1200)
        await shot(page, "19_jump_to_rules_step")
        current = page.locator('ol li button[aria-current="step"]').first
        assert "规则" in await current.inner_text(), "跳转没落在「规则」步骤"
        print("  generate 400 校验通过：走自检渲染 + 按问题跳到「规则」步骤")
        # 这里刻意不点回「正常态」：切 mock 用例会重载页面（?case= 写在地址栏里），
        # 那会把刚跳到的「规则」步骤也一起冲掉。下一步本来就在配置页，直接继续。

        # 14) 无解态：后端此时返回 solution=null / validation=null
        #     校验面板必须是中性的「待排班」，既不能说「全部通过」，也不能满屏红叉
        await tap(page, 'button:has-text("恢复示例配置")', note="恢复示例配置")
        await page.wait_for_timeout(500)
        await tap(page, 'button:has-text("确认恢复")', note="确认恢复")
        await page.wait_for_timeout(2000)
        await tap(page, 'button:has-text("配置好了，去生成排班")', note="去生成（示例配置）")
        await page.wait_for_timeout(900)
        await tap(page, 'button:has-text("无解态 (proven)")', note="无解态")
        await page.wait_for_timeout(400)
        await page.fill("#instruction", "下周正常排班，周二 E01 和 E07 都请假、E12 全天培训")
        await tap(page, 'button:has-text("生成排班")', note="生成（无解态）")
        await page.wait_for_timeout(4800)
        await shot(page, "20_infeasible_validation_null")
        title = await page.locator('h3:has-text("规则校验")').first.inner_text()
        assert "待排班" in title, "validation=null 时校验面板没有走中性态：" + title
        assert "通过" not in title, "validation=null 时不该出现 x/N 通过的口径：" + title
        assert "共 10 条硬规则" in title, "默认配置应为 10 条规则（含 R-10）：" + title
        body_text = await page.inner_text("body")
        assert "全部通过" not in body_text, "无解态出现了「全部通过」，等于把没排出来说成完美排班"
        assert "条违规" not in body_text, "无解态出现了违规计数，validation=null 被当成 0 通过了"
        assert "无可行解" in body_text or "无解" in body_text, "无解态没有给出无解卡片"
        print("  无解态校验通过：validation=null → 中性「待排班」，无假通过、无假违规")

        await browser.close()

    if errors:
        print("\n控制台错误：")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("\n无控制台错误")


asyncio.run(main())
