#!/usr/bin/env bash
# 本机启动三个服务。
#
#   bash scripts/serve_dev.sh              # 只本机访问
#   HOST=0.0.0.0 bash scripts/serve_dev.sh # 局域网可访问（给别人验收用）
#
# 注意：只把 api 暴露出去，vision / ocr 仍然绑 127.0.0.1——
# 它们不该被局域网直接打到，也不带鉴权。
set -euo pipefail
cd "$(dirname "$0")/.."

# Match Pydantic's .env setting in both API and the separate vision process.
if [ -z "${ENABLE_FUSION+x}" ] && [ -f .env ]; then
  ENABLE_FUSION="$(awk -F= '$1 == "ENABLE_FUSION" {print $2; exit}' .env)"
fi
export ENABLE_FUSION="${ENABLE_FUSION:-0}"
export VISION_LOAD_AUX="${VISION_LOAD_AUX:-$ENABLE_FUSION}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8017}"

export MODEL_DIR="${MODEL_DIR:-$PWD/models/current}"
export VISION_DEVICE="${VISION_DEVICE:-}"     # 留空 = 自动探测 cuda → mps → cpu
export VISION_IMGSZ="${VISION_IMGSZ:-512}"
export VISION_ACTION_IMGSZ="${VISION_ACTION_IMGSZ:-512}"
export OCR_PROVIDER="${OCR_PROVIDER:-rapidocr}"
export KEV_MODE="${KEV_MODE:-off}"
export USE_VLX="${USE_VLX:-0}"

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"

echo "启动 vision :8016 / ocr :8018 / api :${PORT}"
if [ "$HOST" = "0.0.0.0" ] && [ -n "$LAN_IP" ]; then
  echo "局域网访问： http://${LAN_IP}:${PORT}/ui/"
fi
echo "本机访问：   http://127.0.0.1:${PORT}/ui/"

uvicorn app.services.vision_service:app --host 127.0.0.1 --port 8016 &
VIS_PID=$!
uvicorn app.services.ocr_service:app --host 127.0.0.1 --port 8018 &
OCR_PID=$!
trap 'kill $VIS_PID $OCR_PID 2>/dev/null || true' EXIT

VISION_URL=http://127.0.0.1:8016 \
OCR_SERVICE_URL=http://127.0.0.1:8018 \
MAX_CONCURRENT="${MAX_CONCURRENT:-4}" \
PREPROCESS="${PREPROCESS:-auto}" \
  uvicorn app.main:app --host "$HOST" --port "$PORT"
