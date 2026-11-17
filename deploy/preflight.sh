#!/usr/bin/env bash
# 构建前的资源预检：磁盘 / 内存 / GPU / 端口。
# 镜像很大（vision ~9GB），磁盘不够会在构建到一半时失败，重来一次很痛。
set -uo pipefail
cd "$(dirname "$0")/.."

REQUIRED_GB="${REQUIRED_GB:-30}"
fail=0

echo "===== 磁盘 ====="
# 用 -k 保证 Linux/macOS 都能跑
avail=$(df -k /var/lib/docker 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}')
[ -z "$avail" ] && avail=$(df -k . | awk 'NR==2 {print int($4/1024/1024)}')
if [ -n "$avail" ]; then
  if [ "$avail" -ge "$REQUIRED_GB" ]; then
    echo "  ✅ /var/lib/docker 可用 ${avail}GB（需要 ≥${REQUIRED_GB}GB）"
  else
    echo "  ❌ 可用 ${avail}GB 不足，需要 ≥${REQUIRED_GB}GB"
    echo "     预估：vision 镜像 ~9GB、api ~1.5GB、ocr ~1.2GB、构建缓存 ~8GB"
    fail=1
  fi
fi

echo "===== 内存 ====="
if command -v free >/dev/null 2>&1; then
  mem=$(free -g | awk '/^Mem:/ {print $2}')
else
  mem=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
fi
if [ -n "$mem" ]; then
  if [ "$mem" -ge 8 ]; then echo "  ✅ 内存 ${mem}GB"; else echo "  ⚠️ 内存仅 ${mem}GB，四容器跑起来偏紧"; fi
fi

echo "===== GPU ====="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.free --format=csv,noheader | while read -r line; do
    idx=$(echo "$line" | cut -d, -f1 | tr -d ' ')
    free=$(echo "$line" | cut -d, -f3 | tr -dc '0-9')
    if [ "${free:-0}" -gt 4000 ]; then echo "  ✅ GPU$idx $line"; else echo "  ⚠️ GPU$idx $line（空闲显存偏少）"; fi
  done
  gpu=$(grep -E '^GPU_ID=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
  [ -n "${gpu:-}" ] && echo "  当前配置 GPU_ID=${gpu}"
else
  echo "  ❌ 未检测到 nvidia-smi"
  fail=1
fi

echo "===== 端口 ====="
bash deploy/check_ports.sh 2>/dev/null | grep -E "✅|❌|端口检查" | sed 's/^/  /'

echo
[ "$fail" = "1" ] && { echo "预检未通过，先处理上面的 ❌ 再构建"; exit 1; }
echo "预检通过 ✅ 可以开始构建（首次预计 15–30 分钟，取决于网络）"
