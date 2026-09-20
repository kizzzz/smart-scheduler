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
  # 不再直接判死刑：第 3 步会沿用服务端 .env 里已有的 Key。只有服务端也没有时才真的降级。
  echo "!! 未设置 GLM_API_KEY，将沿用服务端 .env 里已有的 Key（若服务端也没有，则后端全程降级为规则解析）。" >&2
  if [[ -z "${ASSUME_YES:-}" ]]; then
    read -r -p "继续？(Y/n) " ans
    [[ -z "$ans" || "$ans" == "y" || "$ans" == "Y" ]] || exit 1
  fi
fi

# 无域名时用 ":80"（纯 HTTP）；填域名则 Caddy 自动 HTTPS。
# 先把「用户这次到底有没有显式指定」记下来：下面合并 .env 时，没指定就沿用服务端现值，
# 否则一次漏传参数就会把已签发 HTTPS 的站点打回 :80。
SITE_ADDRESS_EXPLICIT="${SITE_ADDRESS:-}"
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

echo "==> 3/4 合并服务端 .env（只覆盖本次显式传入的项）"
# 这里刻意做「合并」而不是「覆盖」。早期版本无条件重写 .env，一旦忘记带
# GLM_API_KEY，线上 Key 就被抹成空值，站点还会照常 200——只是所有解析静默降级
# 成规则解析，要等用户抱怨「怎么变笨了」才发现。同类事故在同机另一个项目上真
# 实发生过一次（rsync --delete 误删 .env），所以这里宁可啰嗦。
REMOTE_ENV_TMP="$(mktemp)"
ssh "$TARGET" "cat '$REMOTE_DIR/.env' 2>/dev/null || true" > "$REMOTE_ENV_TMP"
if [[ -s "$REMOTE_ENV_TMP" ]]; then
  echo "  已读到服务端现有 .env（$(grep -c '=' "$REMOTE_ENV_TMP") 项），未显式传入的项将原样保留"
else
  echo "  服务端尚无 .env，按默认值新建"
fi

# 取值优先级：本次环境变量 > 服务端现有值 > 默认值
env_of() {
  local key="$1" default="$2" from_env="${3-}"
  if [[ -n "$from_env" ]]; then printf '%s' "$from_env"; return; fi
  local existing
  existing="$(grep -E "^${key}=" "$REMOTE_ENV_TMP" 2>/dev/null | tail -1 | cut -d= -f2-)"
  if [[ -n "$existing" ]]; then printf '%s' "$existing"; return; fi
  printf '%s' "$default"
}

FINAL_KEY="$(env_of GLM_API_KEY '' "${GLM_API_KEY:-}")"

# 白名单之外的键也必须带过去。真实服务器上就有 GLM_BASE_URL 这种脚本没预见的配置项，
# 只保留白名单等于悄悄删掉它——后端会改打默认 endpoint，症状是「部署成功但模型不通」。
# 宁可原样搬运陌生键，也不要替用户做删除决定。
KNOWN_KEYS='GLM_API_KEY|GLM_MODEL|GLM_TIMEOUT|GLM_RETRIES|GLM_VISION_TIMEOUT|GLM_EXTRA_MODELS|SITE_ADDRESS|SERVER_IP|ACME_EMAIL|CORS_ORIGINS'
carry_over_unknown() {
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$REMOTE_ENV_TMP" 2>/dev/null \
    | grep -vE "^($KNOWN_KEYS)=" || true
}
EXTRA_COUNT="$(carry_over_unknown | grep -c '=' || true)"
if [[ "${EXTRA_COUNT:-0}" -gt 0 ]]; then
  echo "  另有 $EXTRA_COUNT 个非标准配置项将原样保留：$(carry_over_unknown | cut -d= -f1 | tr '\n' ' ')"
fi
if [[ -z "$FINAL_KEY" ]]; then
  echo "  !! 服务端与本次调用都没有 GLM_API_KEY，后端将全程降级为规则解析" >&2
fi
# SITE_ADDRESS 同理：默认值 :80 会把已签发的 HTTPS 站点打回纯 HTTP，必须优先沿用服务端现值
FINAL_SITE="$(env_of SITE_ADDRESS ':80' "${SITE_ADDRESS_EXPLICIT:-}")"
echo "  站点地址：$FINAL_SITE"

ssh "$TARGET" "cat > '$REMOTE_DIR/.env' && chmod 600 '$REMOTE_DIR/.env'" <<EOF
GLM_API_KEY=${FINAL_KEY}
GLM_MODEL=$(env_of GLM_MODEL 'glm-4.5-flash' "${GLM_MODEL:-}")
GLM_TIMEOUT=$(env_of GLM_TIMEOUT '12' "${GLM_TIMEOUT:-}")
GLM_RETRIES=$(env_of GLM_RETRIES '2' "${GLM_RETRIES:-}")
GLM_VISION_TIMEOUT=$(env_of GLM_VISION_TIMEOUT '40' "${GLM_VISION_TIMEOUT:-}")
GLM_EXTRA_MODELS=$(env_of GLM_EXTRA_MODELS '' "${GLM_EXTRA_MODELS:-}")
SITE_ADDRESS=${FINAL_SITE}
SERVER_IP=$(env_of SERVER_IP "${TARGET#*@}" "${SERVER_IP:-}")
ACME_EMAIL=$(env_of ACME_EMAIL '' "${ACME_EMAIL:-}")
CORS_ORIGINS=$(env_of CORS_ORIGINS '*' "${CORS_ORIGINS:-}")
$(carry_over_unknown)
EOF
rm -f "$REMOTE_ENV_TMP"


echo "==> 4/4 安装 Docker 并启动"
ssh "$TARGET" "cd '$REMOTE_DIR' && sudo bash deploy/bootstrap.sh"

echo
echo "完成。若 SITE_ADDRESS=:80，访问 http://<公网IP>/ ；若填了域名，访问 https://\$SITE_ADDRESS/"
