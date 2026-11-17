#!/usr/bin/env bash
# 打发布包。两种模式：
#
#   bash deploy/package.sh              # 升级包（默认，几十 MB）：代码 + 模型 + 配置
#   bash deploy/package.sh --with-data  # 首次部署全量包（几百 MB）：额外带 data/
#
# 为什么默认不带 data/：
#   data/（原始照片、派生数据集）和 var/runs（训练产物）占了仓库 70% 以上，
#   但**升级时服务器上已经有了**，每次都打包只是把几个 G 搬一遍。
#   首次部署用 --with-data，或者 rsync 单独同步一次 data/，之后只发升级包。
#
# 首次部署：
#   rsync -av data/ 服务器:/opt/spmcv/data/     # 只同步一次
#   bash deploy/package.sh && scp dist/spmcv-release-*.tar.gz 服务器:/opt/
#   ssh 服务器 'cd /opt/spmcv && tar -xzf /opt/spmcv-release-*.tar.gz && bash deploy/upgrade.sh'
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_DATA=0
[[ "${1:-}" == "--with-data" ]] && WITH_DATA=1

STAMP=$(date +%Y%m%d_%H%M)
MODE=$([[ $WITH_DATA == 1 ]] && echo full || echo release)
OUT="dist/spmcv-${MODE}-${STAMP}.tar.gz"
mkdir -p dist

COMMON_EXCLUDES=(
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store'
  --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='.git'
  --exclude='./models/pretrained'      # 训练用底模，推理不需要
  --exclude='./var/runs'               # 训练产物（488MB）
  --exclude='./var/audit'              # 历史审计截图（33MB）
  --exclude='./var/logs'               # 历史日志
  --exclude='./var/train_jobs'         # 训练任务中间态
  --exclude='./var/eval'               # 评测用符号链接目录
  --exclude='./data/raw/legacy'        # 与「训练照片」目录 100% 重复
  --exclude='./data/processed/v5'      # 可由 scripts/ 重建的派生数据集
  --exclude='./tools/node_modules'
)

# 运行必需：代码、模型、部署脚本、文档（控制台要读 docs/reports 与标注指南）
TARGETS=(
  ./app ./web ./scripts ./deploy ./docs ./models ./tests
  ./Makefile ./README.md ./.env.example ./requirements.txt ./pyproject.toml
)

# 容器要挂载 data/ 与 var/：缺目录 docker 会建成 root 属主，先放空骨架
SKELETON=$(mktemp -d)
trap 'rm -rf "$SKELETON"' EXIT
mkdir -p "$SKELETON/data" "$SKELETON/var/logs" "$SKELETON/var/runs" \
         "$SKELETON/var/train_jobs" "$SKELETON/var/eval"

if [[ $WITH_DATA == 1 ]]; then
  # 全量包：带 data/（照片、上传、划分），var/ 仍只给空骨架
  tar -czf "$OUT" "${COMMON_EXCLUDES[@]}" "${TARGETS[@]}" ./data \
    -C "$SKELETON" var 2>/dev/null || true
else
  # 升级包：data/ 只给空目录，照片不重复打包
  tar -czf "$OUT" "${COMMON_EXCLUDES[@]}" "${TARGETS[@]}" \
    -C "$SKELETON" data var 2>/dev/null || true
fi

SIZE=$(du -h "$OUT" | cut -f1)
echo "包：$OUT  ($SIZE)"
echo
echo "包含：app web scripts deploy docs models tests + data/var 目录骨架"
echo "排除：data 照片与派生集、var/runs、var/audit、models/pretrained、.git、node_modules"
[[ $WITH_DATA == 0 ]] && echo "首次部署先 rsync 一次 data/，之后升级都用这个包。"
