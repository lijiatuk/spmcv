#!/usr/bin/env bash
# 一条命令完成升级：校验配置 → 构建 → 滚动起服务 → 健康检查 → 打印验收信息。
#
# 用法（在仓库根目录）：
#   bash deploy/upgrade.sh              # 构建 api+nginx（最常用）
#   bash deploy/upgrade.sh --all        # 连 vision/ocr 一起构建（首次部署/换模型镜像）
#   bash deploy/upgrade.sh --no-build   # 只重启，不改镜像
#
# 只改前端时也走这个脚本：web/ 是 COPY 进 api 镜像的，必须重建 api。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"
cd "$ROOT"

BUILD_SCOPE="api nginx"
if [[ "${1:-}" == "--all" ]]; then
  BUILD_SCOPE=""
elif [[ "${1:-}" == "--no-build" ]]; then
  BUILD_SCOPE="skip"
fi

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "没有 docker"
docker compose version >/dev/null 2>&1 || fail "没有 docker compose v2"

say "检查 .env"
[[ -f .env ]] || fail "缺少 .env：先 cp .env.example .env 并按需填写"
# shellcheck disable=SC1091
set -a; source .env; set +a
HOST_PORT="${HOST_PORT:-8020}"

say "检查模型与数据"
[[ -e models/current ]] || fail "models/current 不存在（需要软链到某个版本目录）"
[[ -d data ]] || fail "data/ 不存在（照片与数据集必须挂载）"
mkdir -p var

say "校验 compose 配置"
$COMPOSE config --quiet || fail "compose 配置不合法"

if [[ "$BUILD_SCOPE" != "skip" ]]; then
  say "构建镜像：${BUILD_SCOPE:-全部}"
  # shellcheck disable=SC2086
  $COMPOSE build $BUILD_SCOPE
else
  say "跳过构建（--no-build）"
fi

say "启动 / 更新容器"
$COMPOSE up -d --remove-orphans

say "等待 /healthz"
for i in $(seq 1 60); do
  if curl -fsS -m 2 "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 2
  [[ "$i" == "60" ]] && fail "60 次仍未就绪：docker compose ps / docker compose logs api"
done

say "验收"
curl -fsS "http://127.0.0.1:${HOST_PORT}/healthz" || fail "healthz 不通"
echo
META="$(curl -fsS "http://127.0.0.1:${HOST_PORT}/v1/meta" || true)"
echo "$META" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("模型:", d["model"]["configured_version"], "| 契约:", d["contract"]["api_version"], "| 限制:", d["limits"])' 2>/dev/null || echo "$META"
READY="$(curl -fsS "http://127.0.0.1:${HOST_PORT}/readyz" || true)"
echo "$READY"
echo "$READY" | grep -q '"ok":true' || fail "readyz 未通过：检查 vision/ocr 容器和 GPU"

$COMPOSE ps
say "完成：http://<服务器IP>:${HOST_PORT}/ui/console.html"
say "接口文档：http://<服务器IP>:${HOST_PORT}/docs"
