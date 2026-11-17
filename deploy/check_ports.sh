#!/usr/bin/env bash
# 部署前端口体检：确认服务器上没有冲突。
#   bash deploy/check_ports.sh            # 检查默认端口
#   HOST_PORT=8021 bash deploy/check_ports.sh
set -uo pipefail

HOST_PORT="${HOST_PORT:-8020}"
PORTS=("$HOST_PORT" "${VISION_PORT:-8016}" "${API_PORT:-8017}" "${OCR_PORT:-8018}")
NAMES=("对外入口 nginx" "vision 直连" "api 直连" "ocr 直连")

echo "当前主机：$(hostname)  $(date '+%F %T')"
echo

free_all=1
for i in "${!PORTS[@]}"; do
  p="${PORTS[$i]}"
  # ss 优先，退回 netstat / lsof
  if command -v ss >/dev/null 2>&1; then
    holder=$(ss -lntp 2>/dev/null | awk -v port=":$p" '$4 ~ port {print $NF}' | head -1)
  elif command -v lsof >/dev/null 2>&1; then
    holder=$(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" pid="$2}')
  else
    holder=""
  fi

  if [ -n "$holder" ]; then
    printf "  %-6s %-22s ❌ 已被占用：%s\n" "$p" "${NAMES[$i]}" "$holder"
    free_all=0
  else
    printf "  %-6s %-22s ✅ 空闲\n" "$p" "${NAMES[$i]}"
  fi
done

echo
if [ "$free_all" = "1" ]; then
  echo "端口检查通过，可以部署 ✅"
else
  echo "有端口被占用。处理办法："
  echo "  - 只关心 ${HOST_PORT}：改 .env 里的 HOST_PORT（如 8021），其余端口只在容器内网，不占宿主机"
  echo "  - 8016/8017/8018 只有加了 docker-compose.debug.yml 才会占用，正常部署不冲突"
fi

echo
echo "顺带检查："
command -v docker >/dev/null && echo "  docker      ✅ $(docker --version | cut -d' ' -f3 | tr -d ,)" || echo "  docker      ❌ 未安装"
docker compose version >/dev/null 2>&1 && echo "  compose     ✅ $(docker compose version --short 2>/dev/null)" || echo "  compose     ❌ 未安装 v2"
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "  GPU         ✅ $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
else
  echo "  GPU         ⚠️ 未检测到 nvidia-smi"
fi

if command -v firewall-cmd >/dev/null 2>&1; then
  echo
  echo "firewalld 已启用，放行 ${HOST_PORT}："
  echo "  firewall-cmd --permanent --add-port=${HOST_PORT}/tcp && firewall-cmd --reload"
elif command -v ufw >/dev/null 2>&1; then
  echo
  echo "ufw 已启用，放行 ${HOST_PORT}："
  echo "  ufw allow ${HOST_PORT}/tcp"
fi
