#!/usr/bin/env bash
# 跑完 union 数据集的 5 折（fold0 已单独跑过，这里默认补 1-4）
# 用法：bash scripts/run_union_5fold.sh [起始折] [结束折]
set -euo pipefail
cd "$(dirname "$0")/.."

START="${1:-1}"
END="${2:-4}"
EPOCHS="${EPOCHS:-25}"

for f in $(seq "$START" "$END"); do
  echo "===== fold $f: 构建数据 ====="
  conda run --no-capture-output -n spmcv python scripts/build_union.py --fold "$f"

  echo "===== fold $f: 训练 ====="
  conda run --no-capture-output -n spmcv python scripts/train_scene_cls.py \
    --data "data/processed/union/cls/fold$f" \
    --epochs "$EPOCHS" --imgsz 512 --batch 8 \
    --name "union_scene_f$f"

  echo "===== fold $f: 评测 ====="
  conda run --no-capture-output -n spmcv python scripts/eval_heads.py \
    --weights "var/runs/union_scene_f$f/weights/best.pt" \
    --data "data/processed/union/cls/fold$f" \
    --out "docs/reports/eval_union_f$f.json"
done

echo "===== 严格 OOF 汇总（逐图精确计数 + SHA 指纹 + 泄漏检查） ====="
conda run --no-capture-output -n spmcv python scripts/eval_oof.py \
  --out docs/reports/verified_oof.json
