#!/usr/bin/env bash
# 联调用：以生产构建产物（dist）起 preview，并把 /api 代理到本地真后端。
# 存在意义是验「上线后的那一份 bundle」，而不是 dev server。
cd "$(dirname "$0")/.." || exit 1
export VITE_API_TARGET="${VITE_API_TARGET:-http://127.0.0.1:8099}"
npm run build || exit 1
exec npx vite preview --port 4173 --host 127.0.0.1
