# ---------- 构建阶段 ----------
# 用 slim（glibc）而不是 alpine（musl）：alpine 上 npm ci 会稳定复现
# "Exit handler never called!"（npm 在 musl 下的已知缺陷），已在目标机实测。
FROM node:22-slim AS build
WORKDIR /app
ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

# 依赖层：仅在 package.json / lock 变化时失效
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# 源码层
COPY tsconfig.json vite.config.ts tailwind.config.js postcss.config.js index.html ./
COPY src ./src
RUN npm run build

# ---------- 运行阶段（Caddy 静态托管 + SPA fallback） ----------
FROM caddy:2-alpine AS runtime
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -q -O /dev/null http://localhost/ || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
