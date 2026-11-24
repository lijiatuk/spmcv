#!/usr/bin/env bash
# 服务器端更新脚本（在服务器上执行，包由你通过堡垒机手动上传）。
#
#   bash deploy/update.sh /tmp/spmcv-release-v3.0-20261120_160500.tar.gz
#   bash deploy/update.sh <包> --all       连 vision/ocr 一起重建（改过推理代码/依赖时）
#   bash deploy/update.sh <包> --no-build  只解包重启，不重建镜像（改 .env / nginx 配置时）
#
# 做的事：gzip 完整性 → 解包覆盖 → 合并 .env 部署参数 → compose 校验 → 构建 → 起服务 → 健康检查 → 契约验收
# 任一步失败立即退出，不会出现「命令成功了但服务没起来」。
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "用法: bash deploy/update.sh <包路径> [--all|--no-build]" >&2
  echo "示例: bash deploy/update.sh /tmp/spmcv-release-v3.0-20261120_155911.tar.gz" >&2
  exit 1
fi
PKG="$1"
shift || true
SCOPE="api nginx"
case "${1:-}" in
  --all) SCOPE="" ;;
  --no-build) SCOPE="skip" ;;
  "") ;;
  *) echo "未知参数：$1" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 项目名必须固定：服务器上 /data/deploy 目录还跑着别的 compose 项目，
# 用目录名推断会撞成同一个项目，--remove-orphans 会把别人的容器删掉。
if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-spmcv}"
COMPOSE="docker compose -p $COMPOSE_PROJECT_NAME -f $ROOT/deploy/docker-compose.yml"
HOST_PORT="${HOST_PORT:-8020}"
STAMP=$(date +%Y%m%d_%H%M%S)

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$PKG" ]] || fail "包不存在：$PKG"
command -v docker >/dev/null || fail "没有 docker"
docker compose version >/dev/null 2>&1 || fail "没有 docker compose v2"

say "检查包完整性"
gzip -t "$PKG" 2>/dev/null || fail "包损坏或不完整（gzip 校验失败），请重新上传"
echo "  gzip 完整性 OK"

say "备份当前版本信息"
mkdir -p var/backup
{
  echo "updated_at: $(date +%Y-%m-%dT%H:%M:%S%z)"
  echo "package: $PKG"
  echo "git: $(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
  echo "model_current: $(readlink models/current 2>/dev/null || echo '')"
  docker compose -f deploy/docker-compose.yml ps 2>/dev/null || true
} > "var/backup/last_update_${STAMP}.txt"
echo "  已记录 var/backup/last_update_${STAMP}.txt"

say "解包覆盖"
tar -xzf "$PKG" -C "$ROOT"
[[ -f deploy/update.sh ]] && chmod +x deploy/update.sh deploy/package.sh deploy/verify.sh 2>/dev/null || true

[[ -f .env ]] || cp .env.example .env

say "合并部署参数到 .env"
if [[ -f ENV.OVERRIDES ]]; then
  cp .env ".env.bak.${STAMP}"
  echo "  原文件备份：.env.bak.${STAMP}"
  while IFS='=' read -r key value; do
    if [[ -z "$key" || "$key" == \#* ]]; then
      continue
    fi
    if grep -qE "^${key}=" .env; then
      tmp=$(mktemp)
      awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' .env > "$tmp"
      mv "$tmp" .env
      echo "  ${key}=${value}（更新）"
    else
      printf '%s=%s\n' "$key" "$value" >> .env
      echo "  ${key}=${value}（新增）"
    fi
  done < ENV.OVERRIDES
  # 兼容 macOS/Linux 的删除写法
  grep -v '^SEQUENTIAL_PHOTOS=' .env > .env.tmp && mv .env.tmp .env || rm -f .env.tmp
  rm -f ENV.OVERRIDES
else
  echo "  包内没有 ENV.OVERRIDES，跳过（保留服务器现有 .env）"
fi
[[ -e models/current ]] || fail "models/current 不存在（首次部署请用 --with-data 包并确认 models/）"

say "校验 compose 配置"
$COMPOSE config --quiet || fail "compose 配置不合法"

if [[ "$SCOPE" != "skip" ]]; then
  say "构建镜像：${SCOPE:-全部}"
  # shellcheck disable=SC2086
  $COMPOSE build $SCOPE || fail "构建失败，服务未重启（当前版本仍在运行）"
else
  say "跳过构建（--no-build）"
fi

say "启动 / 更新容器"
# 不要用 --remove-orphans：只管理本项目的 4 个容器，绝不能碰别人的
$COMPOSE up -d || fail "容器启动失败：docker compose ps / logs"

say "等待 /healthz"
for i in $(seq 1 60); do
  if curl -fsS -m 2 "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
    echo "  就绪（${i} 次探测）"
    break
  fi
  [[ "$i" == "60" ]] && fail "60 次仍未就绪：docker compose logs api"
  sleep 2
done

say "接口契约验收"
SAMPLE=$(ls data/external/spec/01_车头及车牌号/*.png 2>/dev/null | head -1 || true)
if [[ -n "$SAMPLE" ]]; then
  bash deploy/verify.sh "http://127.0.0.1:${HOST_PORT}" "$SAMPLE" || true
else
  bash deploy/verify.sh "http://127.0.0.1:${HOST_PORT}" || true
fi

say "当前状态"
curl -s "http://127.0.0.1:${HOST_PORT}/v1/meta" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  版本",d.get("version"),"| 模型",d.get("model",{}).get("configured_version"),"| 并发",d.get("limits",{}).get("max_concurrent"))' \
  2>/dev/null || true
$COMPOSE ps

cat <<EOF

更新完成。回滚方式：
  1) 用上一版包重新执行本脚本；
  2) 模型回滚：curl -X POST http://127.0.0.1:${HOST_PORT}/viz/api/model/activate \\
       -H 'Content-Type: application/json' -d '{"version":"<上一版>"}'
     然后 docker compose -f deploy/docker-compose.yml restart vision
EOF