#!/usr/bin/env bash
# 本地联调用：起一个不带 LLM key 的后端，配置契约相关的接口都不依赖模型。
# 放成脚本而不是内联命令，是因为内联的后台进程会被工具链连带收走。
cd "$(dirname "$0")" || exit 1
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8099
