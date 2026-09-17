#!/usr/bin/env bash
# 从本地一条命令部署到远端服务器（腾讯云 Lighthouse 等）。
#
# 用法：
#   GLM_API_KEY=<你的智谱 Key> bash deploy/remote-deploy.sh root@43.134.136.29
#   # 有域名时额外指定，Caddy 会自动签发 HTTPS：
#   GLM_API_KEY=xxx SITE_ADDRESS=sched.example.com bash deploy/remote-deploy.sh root@1.2.3.4
#
# 前提：本机能免密 ssh 到目标机（已配好私钥或 ssh-agent）。
# 注意：Key 只通过 ssh 写进服务器上的 .env，不落地到仓库、不进镜像。
set -euo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "用法：GLM_API_KEY=xxx bash deploy/remote-deploy.sh user@host" >&2
  exit 1
fi
if [[ -z "${GLM_API_KEY:-}" ]]; then
  echo "!! 未设置 GLM_API_KEY 环境变量，部署后后端会全程降级为规则解析。" >&2
  read -r -p "仍要继续？(y/N) " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
fi

# 无域名时用 ":80"（纯 HTTP）；填域名则 Caddy 自动 HTTPS
SITE_ADDRESS="${SITE_ADDRESS:-:80}"
REMOTE_DIR="${REMOTE_DIR:-/opt/smart-scheduler}"
cd "$(dirname "$0")/.."

echo "==> 1/4 连通性检查：$TARGET"
ssh -o ConnectTimeout=10 "$TARGET" 'echo "  ok: $(uname -sr)"'

echo "==> 2/4 同步代码到 $TARGET:$REMOTE_DIR"
ssh "$TARGET" "mkdir -p '$REMOTE_DIR'"
if command -v rsync >/dev/null 2>&1; then
  rsync -az --delete \
    --exclude '.git' --exclude 'node_modules' --exclude 'dist' --exclude 'dist-demo' \
    --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.env' \
    ./ "$TARGET:$REMOTE_DIR/"
else
  echo "  rsync 不可用，改用 git archive + tar"
  git archive --format=tar HEAD | ssh "$TARGET" "tar -x -C '$REMOTE_DIR'"
fi

echo "==> 3/4 写入服务端 .env"
ssh "$TARGET" "cat > '$REMOTE_DIR/.env' && chmod 600 '$REMOTE_DIR/.env'" <<EOF
GLM_API_KEY=${GLM_API_KEY:-}
GLM_MODEL=${GLM_MODEL:-glm-4-flash}
SITE_ADDRESS=${SITE_ADDRESS}
ACME_EMAIL=${ACME_EMAIL:-}
EOF

echo "==> 4/4 安装 Docker 并启动"
ssh "$TARGET" "cd '$REMOTE_DIR' && sudo bash deploy/bootstrap.sh"

echo
echo "完成。若 SITE_ADDRESS=:80，访问 http://<公网IP>/ ；若填了域名，访问 https://\$SITE_ADDRESS/"
