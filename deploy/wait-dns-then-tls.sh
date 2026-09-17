#!/usr/bin/env bash
# 等域名 A 记录生效后，立刻让 Caddy 重试签发证书。
#
# 背景：Caddy 在 ACME 连续失败后会指数退避，退避到几十分钟一次。
# 域名解析刚配好那一刻手动重启 web 容器最省事，但没人愿意守着。
# 这个脚本就是替人守着：解析一生效就重启 web，然后自检 HTTPS。
#
# 用法（在服务器上，后台跑）：
#   nohup sudo bash deploy/wait-dns-then-tls.sh typexx.work 43.134.136.29 \
#     >> /var/log/smart-scheduler-tls.log 2>&1 &
set -u

DOMAIN="${1:?用法: wait-dns-then-tls.sh <域名> <期望IP> [轮询秒数] [最长等待秒数]}"
EXPECT_IP="${2:?缺少期望 IP}"
INTERVAL="${3:-60}"
MAX_WAIT="${4:-86400}"        # 默认最多守 24 小时

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
log() { echo "[$(date -u '+%F %T UTC')] $*"; }

log "开始等待 $DOMAIN 解析到 $EXPECT_IP（每 ${INTERVAL}s 探测，最长 ${MAX_WAIT}s）"

waited=0
while [ "$waited" -lt "$MAX_WAIT" ]; do
  got="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
  if echo " $got " | grep -q " $EXPECT_IP "; then
    log "解析已生效：$DOMAIN -> $got"
    break
  fi
  sleep "$INTERVAL"
  waited=$((waited + INTERVAL))
done

if [ "$waited" -ge "$MAX_WAIT" ]; then
  log "超时退出：$DOMAIN 仍未解析到 $EXPECT_IP，请检查 DNS 记录"
  exit 1
fi

log "重启 web 容器，触发 Caddy 立即重试 ACME"
cd "$PROJECT_DIR" && docker compose restart web

# 证书签发通常几秒到几十秒
for i in $(seq 1 30); do
  sleep 10
  code="$(curl -sS -o /dev/null -w '%{http_code}' -m 15 "https://$DOMAIN/api/health" 2>/dev/null || echo 000)"
  if [ "$code" = "200" ]; then
    log "HTTPS 就绪：https://$DOMAIN/ （/api/health = 200）"
    exit 0
  fi
  log "等待证书… 第 $i 次，https 返回 $code"
done

log "证书仍未就绪，请查看：docker compose logs web | grep -i acme"
exit 1
