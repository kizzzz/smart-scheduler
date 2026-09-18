#!/usr/bin/env bash
# 一键把本仓库发布到 GitHub 公开仓库。
#
# 为什么单独写成脚本：GitHub 的 token 权限经常一次配不对，
# 与其每次手工敲 curl + git remote + push，不如把「建仓 → 推代码 → 自检」固化下来，
# 换一个 token 直接重跑即可，失败时也能明确告诉你缺哪个权限。
#
# 用法：
#   GITHUB_TOKEN=xxx bash deploy/publish-github.sh [owner] [repo]
# 默认 owner 取 token 对应的登录名，repo 默认 smart-scheduler。
#
# token 需要的权限（二选一）：
#   - Classic token：勾选 repo
#   - Fine-grained token：Repository access = All repositories，
#     且 Administration = Read and write（建仓）、Contents = Read and write（推代码）
set -uo pipefail

TOKEN="${GITHUB_TOKEN:?请先设置 GITHUB_TOKEN}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API="https://api.github.com"
UA="smart-scheduler-publisher"

api() { # api <method> <path> [body]
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -m 30 -X "$method" "$API$path"
    -H "Authorization: Bearer $TOKEN"
    -H "Accept: application/vnd.github+json"
    -H "User-Agent: $UA" -w $'\n%{http_code}')
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}"
}

split() { CODE="$(printf '%s' "$1" | tail -n1)"; BODY="$(printf '%s' "$1" | sed '$d')"; }
pick() { printf '%s' "$2" | grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 | cut -d'"' -f4; }

echo "== 1/4 校验身份 =="
split "$(api GET /user)"
[ "$CODE" = "200" ] || { echo "token 无效（HTTP $CODE）：$BODY"; exit 1; }
LOGIN="$(pick login "$BODY")"
echo "登录用户：$LOGIN"

OWNER="${1:-$LOGIN}"
REPO="${2:-smart-scheduler}"
echo "目标仓库：$OWNER/$REPO"

echo "== 2/4 确认仓库存在（不存在则创建）=="
split "$(api GET "/repos/$OWNER/$REPO")"
if [ "$CODE" = "200" ]; then
  echo "仓库已存在，跳过创建"
else
  PAYLOAD='{"name":"'"$REPO"'","description":"智能排班助手：LLM 意图解析 + 确定性求解器 + 独立规则校验器","private":false,"has_issues":true,"has_wiki":false}'
  if [ "$OWNER" = "$LOGIN" ]; then
    split "$(api POST /user/repos "$PAYLOAD")"
  else
    split "$(api POST "/orgs/$OWNER/repos" "$PAYLOAD")"
  fi
  if [ "$CODE" != "201" ]; then
    echo "创建仓库失败（HTTP $CODE）：$BODY"
    echo
    echo "若提示 Resource not accessible by personal access token，说明 token 缺少建仓权限。两种解法："
    echo "  A. 换成勾选了 repo 的 classic token；或 fine-grained token 打开 Administration = Read and write"
    echo "  B. 你先在网页上手动建一个空的公开仓库 $OWNER/$REPO，再用带 Contents = Read and write 的 token 重跑本脚本"
    exit 1
  fi
  echo "仓库已创建"
fi

echo "== 3/4 推送代码 =="
cd "$REPO_DIR"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$OWNER/$REPO.git"
# 用请求头带凭证，不把 token 写进 remote URL，避免落盘到 .git/config
AUTH_B64="$(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')"
if git -c http.extraheader="AUTHORIZATION: basic $AUTH_B64" push -u origin "$BRANCH" 2>&1 | sed "s/$AUTH_B64/***/g"; then
  echo "推送完成"
else
  echo "推送失败：token 大概缺 Contents = Read and write"
  exit 1
fi

echo "== 4/4 自检 =="
split "$(api GET "/repos/$OWNER/$REPO")"
echo "默认分支：$(pick default_branch "$BODY")"
echo "可见性：$(pick visibility "$BODY")"
echo "完成：https://github.com/$OWNER/$REPO"
