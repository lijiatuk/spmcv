#!/usr/bin/env bash
# 打成可直接上传服务器的包（不含训练产物和缓存）
#   bash deploy/package.sh            → dist/spmcv-<日期>.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."

STAMP=$(date +%Y%m%d_%H%M)
OUT="dist/spmcv-${STAMP}.tar.gz"
mkdir -p dist

tar -czf "$OUT" \
  --exclude='./var/runs' \
  --exclude='./var/archive' \
  --exclude='./var/train_jobs' \
  --exclude='./var/logs' \
  --exclude='./.git' \
  --exclude='./tools/node_modules' \
  --exclude='./dist' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='.DS_Store' \
  --exclude='./data/processed/union/cls' \
  --exclude='./data/processed/uploads' \
  --exclude='./data/processed/v5/cls' \
  --exclude='./data/raw/uploads' \
  ./app ./web ./scripts ./tests ./deploy ./docs \
  ./models ./data ./var ./Makefile ./README.md \
  ./.env.example ./requirements.txt ./pyproject.toml 2>/dev/null || true

echo "包：$OUT  ($(du -h "$OUT" | cut -f1))"
echo
echo "包含：app web scripts tests deploy docs models data var"
echo "排除：var/runs（训练产物）、缓存、node_modules、数据集 cls 软链"
echo
echo "服务器上解包后："
echo "  tar -xzf $(basename "$OUT")"
echo "  python scripts/build_union.py --fold 0     # 重建数据集软链（秒级）"
echo "  docker compose -f deploy/docker-compose.yml up -d --build"
