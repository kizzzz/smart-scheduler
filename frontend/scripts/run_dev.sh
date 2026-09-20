#!/usr/bin/env bash
# 联调用前端 dev server：代理 /api 到本地真后端（8099）。
# 写成脚本是因为内联的后台进程会被工具链连带收走。
cd "$(dirname "$0")/.." || exit 1
export VITE_API_TARGET="${VITE_API_TARGET:-http://127.0.0.1:8099}"
exec npx vite --port 5173 --host 127.0.0.1
