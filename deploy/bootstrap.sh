#!/usr/bin/env bash
# 腾讯云 Lighthouse（Ubuntu/Debian）一键初始化 + 启动
# 用法：sudo bash deploy/bootstrap.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "==> 项目目录：$ROOT"

if [[ ! -f .env ]]; then
  echo "!! 缺少 .env，请先执行：cp .env.example .env 并填入 GLM_API_KEY" >&2
  exit 1
fi
if ! grep -qE '^GLM_API_KEY=.+' .env; then
  echo "!! .env 中的 GLM_API_KEY 为空，后端会全程降级为规则解析" >&2
fi

# ---------- Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  echo "==> 安装 Docker"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  echo "==> Docker 已安装：$(docker -v)"
fi

# ---------- 启动 ----------
echo "==> 构建并启动"
docker compose up -d --build

echo "==> 等待健康检查"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1/api/health >/dev/null 2>&1; then
    echo "==> 后端就绪"
    break
  fi
  sleep 2
done

echo
echo "==> 容器状态"
docker compose ps
echo
echo "==> 自检"
curl -fsS "http://127.0.0.1/api/health?deep=1" || echo "!! 健康检查失败，请看 docker compose logs api"
echo
echo "完成。访问：http://$(curl -fsS4 https://ifconfig.me 2>/dev/null || echo '<服务器公网IP>')/"
