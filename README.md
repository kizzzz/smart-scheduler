# 智能排班助手

连锁门店周排班助手。店长说一句人话，系统给出**满足全部 9 条硬规则**的一周排班，并解释为什么这么排；改不动的时候，它会说清卡在哪、以及有哪几条解锁路径。

> FDE 考题 05 实现。核心不是"用大模型排班"，而是**把正确性交给确定性代码，把理解和表达交给大模型**。

## 为什么这样设计

排班是典型的约束满足问题（CSP）：规则可被机器客观校验，一次违规就是一次真实的合规风险。所以本项目刻意**不让模型决定谁上哪个班**：

| 层 | 职责 | 实现 | 出错代价 |
|---|---|---|---|
| L1 意图解析 | 自然语言 → 结构化约束 | GLM（`glm-4-flash`），失败降级为规则解析 | 可控：解析结果全部回显给店长确认 |
| L2 约束求解 | 生成候选排班 | 回溯搜索 + 前向检查 + 局部优化，**零模型参与** | 可控：结果必须过 L3 |
| L3 规则校验 | 判定合规，**唯一真相源** | 独立于求解器的纯函数 | 不允许出错，41 个测试覆盖 |
| L4 解释生成 | 结构化事实 → 人话 | GLM，且输出被确定性规则清洗 | 可控：与校验结论矛盾的句子直接丢弃 |

三条硬性设计决定：

1. **求解器和校验器解耦**。求解器负责剪枝构造，校验器负责独立验收；两者都实现同一套规则，互为交叉验证。
2. **校验器是唯一真相源**。AI 生成、店长手工换人、外部导入，走的都是同一个 `POST /api/validate`，不存在"两套标准"。
3. **模型失败必须降级，不能整体不可用**。没有 API Key 也能完整跑通：意图解析降级为规则匹配，解释降级为模板，**排班与校验完全不受影响**。

## 9 条硬规则

| ID | 规则 |
|---|---|
| R-01 | 每个班至少 1 名具备店长值守资格的员工 |
| R-02 | 每个班至少 2 名具备饮品制作技能的员工 |
| R-03 | 每个班至少 1 名具备收银技能的员工 |
| R-04 | 周一至周五每班至少 4 人；周六、周日每班至少 6 人 |
| R-05 | 每人每周最多 40 小时（5 个班） |
| R-06 | 不得连续工作超过 5 天 |
| R-07 | 上一天晚班后不得安排次日早班 |
| R-08 | 请假和不可工作日期绝对不得排班 |
| R-09 | 技能必须来自员工数据，不得自行补技能 |

补充：早班 09:00–17:00 与晚班 13:00–21:00 时间重叠，同一员工同日不可两班兼任 —— 归入 R-05 工时口径校验。

## 数据现实：瓶颈不是人手，是值守资格

按题面 20 名员工核算（`已知事实`，可由 `/api/meta` 复算）：

- 全周供给上限 **86 人班**，全周最低需求 **64 人班**，余量 22 人班 —— 总人力不紧。
- 具备店长值守资格的只有 E01–E05，且 E05 仅周五至周日可工作、E01 周三请假。每天早晚两班需要 **2 名不同的**值守员工，周一、周二的候选只有 3 人。

所以求解器**先排值守位**，再补技能位，最后补人数。这也是"无解"最常见的成因：抽掉两名值守员工，周一、周二立刻被证明无解。

## 三种状态

1. **生成态**：出方案 + 9 条规则逐条绿灯 + 软指标（偏好满足率 / 工时均衡度 / 技能冗余）。
2. **微调态**：店长点 chip 换人 → 实时重校验 → 违规格子标红，并给出**可一键执行的修复建议**（后端保证建议改完违规真的消失）。
3. **无解诊断态**：给出被证明无解的证据（哪天、哪条规则、可用人数差多少）+ 3 条解锁路径及其代价，而不是硬凑一个违规方案。

外加**澄清态**：指令指代不明（如"小王明天来不了"）时反问，绝不猜。

## 本地运行

后端：

```bash
cd backend
pip install -r requirements-dev.txt
export GLM_API_KEY=<你的智谱 Key>     # 可留空，会走规则兜底
uvicorn app.main:app --reload --port 8000
pytest -q                             # 41 个测试
```

前端：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，已配好 /api 代理
```

前端支持 `?mock=1` 纯前端联调（含 `&case=` 切换正常/违规/无解/澄清态），不依赖后端。

构建纯静态演示站（默认 mock，无需后端，仍可用 `?mock=0` 打真实接口）：

```bash
cd frontend
VITE_DEMO_MOCK=1 npx vite build --outDir dist-demo
```

## 部署

线上环境：**https://typexx.work/** （`www.typexx.work` 同样可用，两个域名各自持有 Let's Encrypt 证书，HTTP 自动 308 跳 HTTPS）。
IP 直连 http://43.134.136.29/ 作为回退通道保留。腾讯云 Lighthouse，ap-singapore-2，Ubuntu 24.04，`/opt/smart-scheduler`。

单台服务器 + Docker Compose + Caddy，前后端同域，`/api` 反代，前端只用相对路径。

```bash
cp .env.example .env      # 填入 GLM_API_KEY；有域名时把 SITE_ADDRESS 改成域名
sudo bash deploy/bootstrap.sh
```

或者从本地一条命令部署（同步代码 → 写 .env → 装 Docker → 起服务 → 自检）：

```bash
GLM_API_KEY=<你的智谱 Key> bash deploy/remote-deploy.sh root@<服务器公网IP>
# 有域名：
GLM_API_KEY=xxx SITE_ADDRESS=sched.example.com bash deploy/remote-deploy.sh root@<IP>
```

- `SITE_ADDRESS=:80`：纯 HTTP，适用于只有公网 IP 的情况。
- `SITE_ADDRESS=your.domain`：Caddy 自动申请并续期 Let's Encrypt 证书，自动跳 HTTPS。**只有 IP 没有域名时无法签发公网信任证书**，这是 CA 的限制，不是配置问题。
- `SITE_ADDRESS=your.domain, www.your.domain`：多域名逗号分隔，Caddy 为每个域名各自签发并续期证书。改完这个变量要用 `docker compose up -d` 重建容器，`restart` 不会重新读取环境变量。

API Key 只存在于服务端环境变量，不进镜像、不进仓库、不下发前端。

### 域名解析生效后自动签发证书

Caddy 在 ACME 连续失败后会指数退避到几十分钟一次，域名刚配好那一刻它不会立刻重试。
`deploy/smart-scheduler-tls.service` 就是替人守着：轮询 A 记录，一生效就重启 web 触发签发，然后自检 HTTPS。

```bash
sudo cp deploy/smart-scheduler-tls.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart-scheduler-tls.service
sudo systemctl start --no-block smart-scheduler-tls.service   # ExecStart 是长等待，别用 --now
tail -f /var/log/smart-scheduler-tls.log
```

做成 systemd 单元而不是 `nohup` 后台进程，是为了两件事：服务器重启后自动接着守；一轮等待超时后自动重来。
签发成功后脚本 `exit 0`，`Restart=on-failure` 不会再拉起它。

### 推送到 GitHub

```bash
GITHUB_TOKEN=<你的 token> bash deploy/publish-github.sh
```

脚本会依次校验身份 → 建仓（已存在则跳过）→ 推代码 → 自检，凭证走请求头不落盘到 `.git/config`。
token 权限二选一：classic token 勾 `repo`；或 fine-grained token 设 Repository access = All repositories，
并打开 `Administration = Read and write`（建仓）与 `Contents = Read and write`（推代码）。
只有读权限会在第 2 步明确报 `Resource not accessible by personal access token`。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health?deep=1` | 健康检查，`deep=1` 时实测 GLM 连通性 |
| GET | `/api/meta` | 员工档案、9 条规则、班次定义（前端技能标签的唯一来源） |
| GET | `/api/scenarios` | 5 个预置演示场景 |
| POST | `/api/generate` | 主链路：`{instruction, base_slots}` → 方案 / 无解诊断 / 澄清 |
| POST | `/api/validate` | 手工微调后的实时校验，与生成路径共用校验器 |
| GET | `/api/candidates` | 换人候选名单（已按 R-05/R-06/R-07/R-08 过滤） |

交互式文档：`/api/docs`。

## 目录

```
backend/
  app/data.py         员工数据与规则常量（技能唯一真相源，R-09）
  app/models.py       内部数据模型
  app/solver.py       L2 回溯求解 + 前向检查 + 无解诊断
  app/validator.py    L3 校验器（唯一真相源）
  app/repair.py       修复建议（自证有效后才返回）
  app/llm.py          L1/L4 GLM 接入 + 降级 + 防幻觉清洗
  app/serializers.py  内部模型 → 前端契约
  app/main.py         FastAPI 路由
  tests/              48 个测试
frontend/             React 19 + TS + Vite + Tailwind
deploy/               Caddyfile + 服务器初始化 / 一键部署 / TLS 守护 / GitHub 发布脚本
```

## 已知边界

- 求解时间预算 4s / 6 万节点。超时会明确返回"未在预算内找到可行解（未证明无解）"，与"已证明无解"严格区分。
- 排班周期固定为一周 14 个班次，跨周连续工作天数（R-06）不跨周累计。
- 软约束（偏好、均衡、兼职周末）只做局部优化，不保证全局最优；硬约束保证 100%。
