# 配置化改造 · 产品设计与 API 契约

> 这份文档是后端与前端的唯一契约来源。改任何字段先改这里。

## 1. 为什么要改

现状：员工（20 人）、规则（R-01～R-09）、维度（7 天 × 2 班）全部硬编码在 `backend/app/data.py`。
它能证明架构，但产品上只服务一家虚构门店——用户拿到手第一件事就是「这不是我的店」。

改造目标：**任何一家门店都能在 5 分钟内配好自己的排班场景并生成排班**，且零配置也能直接用（默认值 = 现有考题数据）。

## 2. 用户旅程（三步）

```
① 配置                      ② 生成                    ③ 查看 / 调整
场景 → 员工 → 规则     →    自然语言指令 + 模型   →   看板 + 校验报告 + 手工微调
（有默认值，可跳过）        （配置作为求解输入）      （改动实时过同一套校验器）
```

三个设计决定：

1. **配置可跳过**。首次进入直接落在「生成」页，顶部一行提示当前用的是示例门店配置。降低上手门槛优先于功能完整性——要求用户先填 20 个员工再体验，会在第一屏流失。
2. **配置改动不立刻重排**。改完配置回到生成页，已有排班保留但标记「配置已变更，当前排班基于旧配置」，由用户决定何时重排。理由：排班是有成本的决策产物，不能因为改了个人名就被静默清空。
3. **配置在生成前先自检**。员工池根本凑不出每班最低人数这类问题，必须在配置页就报出来，而不是让用户等 60 秒拿到一句「无解」。

## 3. 关键取舍（必须坚持的边界）

### 3.1 规则是「参数化模板」，不是自由文本

用户可以：启停规则、改参数、新增同类规则实例、改显示名、删除自建规则。
用户不能：用自然语言写一条新规则。

**理由**：本项目的架构基石是「排班正确性由确定性代码保证，LLM 只做理解与表达」。
自由文本规则的唯一实现路径是让 LLM 判断合规性，那么校验器就不再是唯一真相源，
也无法给出可复现、可定位到具体格子的违规结论。

**代价**：模板库外的诉求表达不了（如「E01 和 E02 不能同班」）。
处理方式：把高频诉求逐条收进模板库（该例已作为 `forbid_pair_same_shift` 进入 V1），
而不是开一个万能口子。

`待验证`：模板库 8 类能覆盖多少真实门店诉求。验证方式：上线后统计配置页「找不到我要的规则」反馈入口的提交内容。

### 3.2 MVP 只支持硬约束

规则只有 hard 一档。软约束（权重、偏好优先级）会引入调参界面和「为什么这次没满足我的偏好」的解释成本，MVP 不做。
现有的偏好满足率仍作为软指标展示，但不可配置。

### 3.3 配置存在浏览器，后端保持无状态

MVP 不做账号与数据库：配置存 `localStorage`，每次请求随 body 传给后端。

| | 收益 | 代价 |
|-|-|-|
| 无状态 + localStorage | 零登录成本、天然多用户隔离、后端不引入 DB 与迁移 | 换设备/清缓存即丢配置；无法多人协作 |

配套兜底：配置页提供「导出 JSON / 导入 JSON」，让用户能自己备份和迁移。
`待验证`：是否真有多人协作诉求。有的话 V1 加服务端存储 + 分享链接。

## 4. 数据模型

```ts
interface SchedulerConfig {
  version: 1;
  scenario: ScenarioDef;
  skill_pool: string[];     // 技能字典，员工技能只能从这里选（R-09 的配置化形态）
  role_pool: string[];      // 角色字典
  employees: EmployeeDef[];
  rules: RuleDef[];
}

interface ScenarioDef {
  name: string;             // "门店周排班"
  days: DayDef[];           // 1..14 天
  shifts: ShiftDef[];       // 1..4 班
}

interface DayDef {
  id: string;               // "d1"，内部标识，不展示
  label: string;            // "周一"，用户可改
  peak: boolean;            // 高峰日。原「周末加人」的配置化形态
}

interface ShiftDef {
  id: string;               // "s1"
  name: string;             // "早班"
  start: string;            // "09:00"
  end: string;              // "17:00"；end <= start 视为跨夜到次日
  hours: number;            // 后端按 start/end 回算，前端只读
}

interface EmployeeDef {
  id: string;                       // "E01"，唯一，用户可改
  name: string;                     // "张三"。新增字段
  role: string;                     // 取自 role_pool
  skills: string[];                 // 取自 skill_pool
  unavailable: UnavailableSlot[];   // 不可排班时段
  max_shifts: number | null;        // 个人上限，null = 用全局规则
  preferred_shifts: string[];       // 软偏好，shift id
  active: boolean;                  // 停用不删除：保留历史排班可读
}

interface UnavailableSlot {
  day: string;                      // DayDef.id
  shift: string | null;             // null = 当天整日不可用
}

interface RuleDef {
  id: string;          // "R-01"，保留可读编号，用户自建的用 "C-1"
  type: RuleType;
  name: string;        // 用户可改的显示名
  enabled: boolean;
  locked: boolean;     // true = 系统内建，不可删除/禁用
  params: object;      // 按 type 定义，见下
}
```

### 规则模板库（8 类，覆盖原 R-01～R-09）

| type | 语义 | params | 原规则 | locked |
|-|-|-|-|-|
| `min_staff_per_shift` | 每班总人数下限 | `{default:int, peak:int, overrides:[{day,shift,min}]}` | R-04 | 否 |
| `require_attribute` | 每班至少 N 人具备某属性 | `{attr:"skill"\|"role", value:string, min:int}` | R-01/02/03 | 否 |
| `max_shifts_per_period` | 每人周期内最多几个班 | `{max:int}` | R-05 | 否 |
| `max_consecutive_days` | 最多连续工作天数 | `{max:int}` | R-06 | 否 |
| `min_rest_hours` | 相邻班次最小休息间隔 | `{hours:number}` | R-07 | 否 |
| `one_shift_per_day` | 每人每天最多一个班 | `{}` | 原隐式假设 | 否 |
| `respect_unavailability` | 不可用时段不得排班 | `{}` | R-08 | **是** |
| `skill_source_integrity` | 技能只能来自员工档案 | `{}` | R-09 | **是** |

两点说明：

- **R-07 泛化为 `min_rest_hours`**：原实现是字符串比较「晚班接次日早班」，只在两班制下成立。
  改成按 `ShiftDef.start/end` 计算实际间隔小时数，**间隔严格小于 `hours` 才违规，恰好等于视为合规**
  （参数语义是「至少休息 X 小时」）。默认 `hours: 13`：原 9-17/13-21 两班制下晚班 21:00 →
  次日早班 09:00 = 12h < 13，仍然违规，与旧行为等价；若默认取 12 则该接班恰好合规，旧 R-07 会失效。
- **`one_shift_per_day` 从隐式变显式**：旧 solver 隐含「每人每天一个班」，但从未作为规则暴露。
  三班制门店可能允许一天两班，所以必须可关。因此**默认配置带 10 条规则**：R-01～R-09 沿用原题面，
  末尾追加 `R-10 = one_shift_per_day`（`enabled: true`、`locked: false`、`params: {}`）。
  默认两班 09–17 / 13–21 时间重叠，一人一天本就上不了两个班，所以加上 R-10 不改变默认排班结果；
  它的作用是让这条隐含假设在校验面板里有一行、并且能被关掉。关掉之后重叠仍会被引擎级不变式
  拦住（归在重叠规则下），因为「同一人同时在两处」不是业务偏好。
  影响面：`validation.rules` 与 `GET /api/config/default` 的 `rules` 在默认配置下是 **10 条**
  （`GET /api/meta` 仍是原题面的 9 条，不变）。

## 5. API 契约

### 5.1 `GET /api/config/default`

返回默认配置（= 现有考题数据）：7 天 × 2 班、20 名员工、**10 条规则（R-01～R-10）**，
前端首次加载用它预填。

```json
{ "config": { /* SchedulerConfig */ } }
```

### 5.2 `POST /api/config/validate`

配置自检。**在用户点「生成」之前就要能告诉他配置本身有没有解**。

请求：`{ "config": SchedulerConfig }`

响应：
```json
{
  "ok": false,
  "errors": [
    { "code": "supply_lt_demand", "message": "周六早班需要 6 人，但当天可排班的员工只有 4 人",
      "where": {"day":"d6","shift":"s1"}, "fix": "降低该班人数下限，或减少当天的不可用设置" }
  ],
  "warnings": [
    { "code": "no_headroom", "message": "总供给刚好等于总需求，任何一人请假都会导致无解", "fix": "建议至少留 10% 冗余" }
  ],
  "capacity": {
    "demand_person_shifts": 64,
    "supply_person_shifts": 100,
    "headroom_pct": 56.3,
    "per_slot": [ { "day":"d1","shift":"s1","min_required":4,"eligible":18 } ]
  }
}
```

必须检出的 error（这几类一定导致无解，提前拦掉才有意义）：

| code | 判定 |
|-|-|
| `no_employees` | 无启用员工 |
| `supply_lt_demand` | 某格「可排班人数」< 该格人数下限 |
| `attribute_absent` | `require_attribute` 要求的属性在员工池中不存在 |
| `attribute_supply_lt_demand` | 某格具备该属性的可用员工数 < 要求数 |
| `daily_capacity_lt_demand` | 某天各班人数下限之和 > 当天可排班人数 × 一人一天可上的班数 |
| `daily_attribute_capacity_lt_demand` | 某天需要的持证「不同员工数」（`⌈min × 班次数 / 一人一天可上的班数⌉`）> 当天具备该属性的可排班人数 |
| `capacity_lt_total_demand` | 总需求人次 > 全员上限之和 |
| `empty_scenario` | 天数或班次为 0 |
| `duplicate_employee_id` | 工号重复 |
| `unknown_skill` | 员工技能不在 `skill_pool` 内 |
| `unknown_rule_type` | 规则类型不在模板库中（旧版本残留配置） |
| `invalid_rule_params` | 规则参数不合法，见下表 |

判定分三层，从具体到笼统：**单格 → 单日 → 全周期**，与 `solver` 的无解证据一一对应。少任何一层，
配置页都会说「没问题」而生成返回一条求解器能证明的无解（例如 3 天 × 3 班 8 人、某天需 9 人次：
总量与单格都够，只有那一天凑不齐）。同一天已经报出格子级 error 时不再叠加按天 error，格子级那条更可操作。

两条按天判定里的「一人一天可上的班数」是算出来的上界，不是「`one_shift_per_day` 有没有开」：
该规则生效时它是 1；规则关掉时按班次时间实际能叠几个班算（互不重叠的三班 → 3，判定自动放宽），
但**班次两两重叠或最小休息间隔挡住时它仍然是 1**——默认的 09–17 / 13–21 就属于这种，
此时即使没有 `one_shift_per_day`，按天不足也是可证明的无解，仍报 error。
`message` 里会写明这个上界的来源，`fix` 只在 `one_shift_per_day` 确实存在时才建议停用它。

`capacity_lt_total_demand` 的「全员上限之和」是三个硬上界的交：可排天数、连班上限
（最多连上 k 天就要歇 1 天 → D 天里最多上 `D − ⌊D/(k+1)⌋` 天）、以及**最小休息间隔**
（要求休息的小时数跨过一整天时，两次上班至少隔 g 天 → 最多上 `⌊(D−1)/g⌋+1` 天）。
休息间隔那一项容易被忽略：2 天 × 1 班、休息 30 小时、1 名员工时，这个人两天里只能上 1 个班，
demand 2 > supply 1 是可证明的无解；不折进来就会放行，让用户白等一次求解。

#### 规则参数合法性（`invalid_rule_params`）

参数不合法时**一律报 error，不替用户挑一种解释**。`max_consecutive_days.max = 0` 字面意思是
「谁都不许上班」，而用户想说的几乎一定是「不限制」——任何一方擅自兜底，都会出现 `solver` 判死、
`validator` 判合规的两个答案。要「不限制」就把规则 `enabled` 设为 `false`，要限制就填一个有效值。

| 规则类型 | 有效取值 |
|-|-|
| `min_staff_per_shift` | `default` / `peak`：≥0 的整数（`0` = 这一格不设下限；`null` 或缺省 = 不设这一项）；`overrides[i]` 必须是对象且含 ≥0 的 `min` |
| `require_attribute` | `attr` ∈ `skill` \| `role`；`value` 非空；`min` ≥1 |
| `max_shifts_per_period` | `max` ≥1，必填 |
| `max_consecutive_days` | `max` ≥1，必填 |
| `min_rest_hours` | `hours` > 0，必填 |
| `one_shift_per_day` / `respect_unavailability` / `skill_source_integrity` | 无参数 |

非整数、非数字（`"两天"`）、缺失必填项，都归到同一个 code；`where.rule_id` 指向出错的规则，
具体错在哪个参数由 `message` 承担，`fix` 给出「怎么写才对」（含「想表达不限制请停用这条规则」）。
**只检查生效中的规则**（`enabled` 或 locked）：停用的规则不参与求解，为它的参数拦住整份配置属于误拦。

两个引擎对非法参数的口径统一为「这条规则不生效」（`config.valid_limit` / `valid_hours`），
所以即使绕过自检（例如直接调 `/api/validate`），`solver` 与 `validator` 也不会给出互相矛盾的结论。
唯一的例外是**员工个人 `max_shifts = 0`**：那是「本周期不排这个人」的正当写法，两边都照字面执行。

warning（不拦生成，只提示脆弱）：`no_headroom`（全周期供给几乎等于需求）、
`no_daily_headroom`（某天可排人数恰好等于当天需求，这天一个人都不能请假）、
`rest_forces_day_gap`（休息小时数跨过一整天，每人两次上班至少隔 g 天，可排人次大幅减少）、
`unknown_override_ref`（`min_staff_per_shift.overrides` 指向不存在的日期/班次，这条覆盖会静默失效）、
`locked_rule_forced`、`unknown_unavailable_ref`、`unknown_preferred_shift`、
`duplicate_rule_id`、`no_active_business_rules`。

### 5.3 `POST /api/generate`（改）

```json
{ "instruction": "...", "base_slots": null, "model": null, "config": { /* 可选 */ } }
```

- `config` 省略 → 用默认配置，行为与改造前完全一致（向后兼容，老前端不改也能跑）。
- 配置自检为 error 时直接 `400`，不进求解。body **就是** `/api/config/validate` 的响应
  （`ok` / `errors` / `warnings` / `capacity` 逐字相同），额外多两个键：`detail` 与 `message`，
  两者都等于第一条 error 的 `message`（`/api/generate` 的既有错误读 `detail`，导入链路读 `message`，
  前端两套读法都能拿到同一句话）。配置解析失败（JSON 不合法、字段类型不对）走同一个形状，
  `errors[0].code = "invalid_config"`。

响应新增 `scenario` 回显，让看板能按实际维度渲染，而不必自己推：
```json
{ "scenario": { "days":[{"id":"d1","label":"周一","peak":false}],
                "shifts":[{"id":"s1","name":"早班","time_label":"09:00–17:00"}] } }
```
`solution.slots[i]` 新增 `min_required`，看板不再按「周末=6」自己算。

**`solution` 为 `null` 时 `validation` 也是 `null`**（无解与需要反问两条路径都适用）。
没有排班表就没有被违反的规则；返回一份「每条规则 `passed:false`、`violations:[]`」的空报告
等于报告一个没发生过的失败，前端会画满红叉，用户会以为问题出在这些规则本身。
无解的信息只由 `infeasible` 承载（`scenario` 仍然回显，空看板照样画得出来），
需要反问的信息只由 `clarification` 承载。
`POST /api/import` 不受此影响：它的产物是「体检报告」，解析失败时仍返回规则清单骨架。

### 5.4 `POST /api/validate`（改）

`{ "slots": [...], "config": {...} }`，`config` 可选。

### 5.5 `POST /api/candidates`（新增）

换人对话框的候选名单。原来只有 `GET`，而 GET 带不了一份配置，导致自定义门店里返回的是默认 20 人档案（幽灵员工）。

```json
{ "day": "d1", "shift": "s1", "taken": ["E01","E02"], "config": { /* 可选 */ } }
```

- `taken`：该格已排的人，从候选里剔除。
- `config` 省略 → 默认配置，结果与 `GET /api/candidates` 逐字节一致。
- 过滤与求解器共用同一套 `solver.eligible`：不可用/请假、个人与全局班次上限、连班上限、最小休息间隔、班次时间重叠，全部按 `config` 判定；员工池取 `config.employees` 中 `active` 的人。

响应（与 GET 完全相同的结构）：
```json
{ "day": "d1", "shift": "s1",
  "candidates": [ { "id":"E03", "name":"E03", "role":"店员", "skills":["收银"],
                    "unavailable":[{"day":"d2","shift":null,"reason":"请假"}],
                    "max_shifts": null, "preferred_shifts":["s1"], "active": true,
                    "available_days":["d1","d2"], "leave_days":["d2"], "preference":"s1" } ] }
```
- `candidates[i]` 是 `EmployeeDef` 的全部字段 + 三个派生的兼容字段（`available_days` / `leave_days` / `preference`），后者由 `unavailable`、`preferred_shifts` 折算，供老前端继续解析；不存在两种员工形状。
- `day` 或 `shift` 不在 `config` 里 → `400`，`detail`/`message` 给出有效取值；不静默返回空名单（前端格子可能来自上一份配置）。
- 配置本身解析失败 → `400`，body 与 `/api/config/validate` 同构。这个接口不跑容量自检：一份还排不满的配置照样需要看候选。

`GET /api/candidates?day=&shift=&taken=E01,E02` 保留，只服务默认配置，仅为既有调用方兼容。

### 5.6 其他

- `GET /api/meta`：保持原样（默认配置的员工与规则），老前端不破。
- `POST /api/import`：`config` 可选；格子总数期望值由 `config` 推导，不再写死 14。

## 6. 分期

**MVP（本次）**：场景 / 员工 / 规则三页可视化配置、8 类规则模板、配置自检、localStorage 持久化 + JSON 导入导出、看板按配置维度动态渲染、solver 与 validator 泛化。

**MVP 明确不做**：
- 账号与服务端存储（无登录场景下做多用户会引入一堆权限问题，收益不明）
- 软约束权重调参（会把配置页变成调参面板，超出「5 分钟配好」目标）
- 多门店切换（先验证单店够不够用）
- 自定义规则 DSL（见 3.1，架构上不接受）

**V1**：`forbid_pair_same_shift`、服务端配置存储 + 分享链接、配置版本与回滚、按班次不同时长自动算工时上限。

## 7. 指标

| 层 | 指标 | 采集点 |
|-|-|-|
| 北极星 | 完成「配置 → 生成」全流程的会话占比 | 前端埋点：config_saved → generate_success |
| 过程 | 配置页各步骤完成率、平均配置耗时、自检 error 触发率与 code 分布 | `/api/config/validate` 响应打点 |
| 过程 | 默认配置直接生成的占比（衡量默认值是否够用） | `/api/generate` 是否携带 config |
| 反向 | 配置后生成失败率、无解率、配置保存后 1 分钟内回滚率 | 后端 400/无解计数 |
| 反向 | 求解超时率（维度变大后 solver 是否还收敛） | solver 耗时 P99 |
