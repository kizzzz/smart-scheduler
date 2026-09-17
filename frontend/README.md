# 智能排班助手 · 前端

React 19 + TypeScript + Vite + Tailwind CSS v3 实现的连锁门店 AI 排班界面。
产品主张：**AI 出 0→80 的草案，店长做 80→100 的微调，微调时实时校验兜底。**

## 本地开发

```bash
npm install
npm run dev     # http://localhost:5173，/api 已代理到 http://localhost:8000
npm run build   # tsc --noEmit && vite build → dist/
npx vite preview --port 4173
```

前端所有请求都用相对路径 `/api/...`（开发走 Vite proxy，生产同域反代），代码里不写 host、不含任何密钥。

## Mock 自验（后端未就绪时）

URL 加 `?mock=1` 走 `src/mock/` 下符合 API 契约的样例数据，可选 `&case=` 指定状态：

| URL | 状态 |
| --- | --- |
| `?mock=1&case=normal` | 正常态（9/9 通过） |
| `?mock=1&case=violation` | 违规态（R-07 + 意图解析降级） |
| `?mock=1&case=infeasible` | 无解态（`proven: true` + 三条解锁路径） |
| `?mock=1&case=adjust` | 最小扰动重排（diff 提示条） |
| `?mock=1&case=error` | 错误态（HTTP 500 可重试卡片） |

mock 模式下换人 / 应用修复建议会走 `src/mock/validator.ts`（9 条规则的前端参考实现），
因此微调 → 实时校验的闭环可以离线自验。**线上一律由后端 `POST /api/validate` 裁决，前端不做规则判定。**

## 目录

```
src/
├── App.tsx                  # 状态编排：空/加载/正常/违规/无解/错误
├── api.ts                   # fetch 封装 + mock 开关 + ApiError
├── types.ts                 # API 契约类型（字段名与后端严格一致）
├── lib/{utils,schedule}.ts  # 工具 / 候选人筛选、slot 变更、违规索引
├── components/              # 业务组件（看板、校验面板、意图回显、无解诊断…）
│   └── ui/                  # 手写 shadcn 风格基础件（Button/Card/Badge/Dialog/Tooltip/Skeleton/MetricBar）
└── mock/                    # 契约样例 JSON + 前端参考校验器
```

## 容器化

```bash
docker build -t smart-scheduler-frontend .
docker run -p 8080:80 smart-scheduler-frontend
```

多阶段构建：`node:22-alpine` 构建 → `caddy:2-alpine` 托管 `dist`，监听 80，
`try_files {path} /index.html` 做 SPA fallback，`/assets/*` 长缓存、`index.html` 不缓存。

## 有意的范围收敛

- **不做拖拽**，只做「点击 chip 换人」（含「补一个人」「仅移出」）。
- 不引入 Redux/Zustand、不引入动画库；仅用 Tailwind `transition` 做校验状态翻转（150ms）与改动格子高亮（600ms）。
