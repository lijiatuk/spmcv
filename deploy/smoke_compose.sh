#!/usr/bin/env bash
# 整栈自检（**在服务器上跑**）：起三个容器 + nginx，跑验收，再决定是否保留。
#
#   bash deploy/smoke_compose.sh            # 起栈 + 验收
#   bash deploy/smoke_compose.sh --down     # 验收完关掉
#
# 本机（无 NVIDIA）用 CPU 覆盖文件：
#   COMPOSE_FILE="deploy/docker-compose.yml -f deploy/docker-compose.cpu.yml" bash deploy/smoke_compose.sh
set -uo pipefail
cd "$(dirname "$0")/.."

FILES="${COMPOSE_FILE:-deploy/docker-compose.yml}"
ARGS=""
for f in $FILES; do ARGS="$ARGS -f $f"; done
# 端口取 .env 里的 HOST_PORT（compose 文件在 deploy/ 下，不会自动读根目录 .env）
if [ -f .env ]; then
  _hp=$(grep -E '^HOST_PORT=' .env | tail -1 | cut -d= -f2 | tr -d '[:space:]')
fi
PORT="${PORT:-${_hp:-8020}}"
IMG="${SAMPLE_IMAGE:-}"

if [ ! -f .env ]; then
  echo "缺少 .env：先 cp .env.example .env 再改端口/密钥"
  exit 1
fi
echo "== 构建并启动：$FILES（入口端口 $PORT）=="
# shellcheck disable=SC2086
docker compose $ARGS --env-file .env up -d --build || { echo "启动失败"; exit 1; }

echo "== 等待健康 =="
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null)
  [ "$code" = "200" ] && { echo "健康检查通过（第 ${i} 次轮询）"; break; }
  sleep 5
done
if [ "$code" != "200" ]; then
  echo "服务未就绪，最近日志："
  # shellcheck disable=SC2086
  docker compose $ARGS --env-file .env logs --tail=40
  exit 1
fi

echo "== 容器内 GPU 自检 =="
if docker compose $ARGS --env-file .env ps --services | grep -qx vision; then
  vid=$(docker compose $ARGS --env-file .env ps -q vision)
  if docker exec "$vid" python -c "import torch;print('cuda',torch.cuda.is_available())" 2>/dev/null | grep -q "cuda True"; then
    echo "  ✅ vision 容器内 CUDA 可用"
  else
    echo "  ⚠️ vision 容器内 CUDA 不可用（CPU 模式或驱动未挂载），生产必须修"
  fi
fi

echo "== 验收 =="
if [ -n "$IMG" ]; then
  bash deploy/verify.sh "http://127.0.0.1:${PORT}" "$IMG"
else
  bash deploy/verify.sh "http://127.0.0.1:${PORT}"
fi
rc=$?

if [ "${1:-}" = "--down" ]; then
  echo "== 关闭 =="
  # shellcheck disable=SC2086
  docker compose $ARGS --env-file .env down
fi
exit $rc
