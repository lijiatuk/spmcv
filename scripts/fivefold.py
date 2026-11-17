#!/usr/bin/env python3
"""真正的 5 折交叉验证：每折单独训一个场景模型，只在**该折**的 val 上评。

为什么必须这样：仓库里的 v5 模型只训过 fold-0。直接拿它去评 fold1–4，
那 4 折的工单 100% 出现在 fold-0 训练集里（属于背答案），
所以之前跑出来的 97.4% 是无意义的样本内数字。

每折成本：建数据集 ~30s + 训 40 epoch（M1 MPS ~13min）+ 评测 ~1min。

    python scripts/fivefold.py --folds 0,1,2,3,4 --out docs/reports/fivefold_scene.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("  $ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", default="0,1,2,3,4")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--data", default="data/processed/v5")
    ap.add_argument("--raw", default="data/raw/recut")
    ap.add_argument("--assist", default="data/processed/v3/assist_labels.csv")
    ap.add_argument("--out", default="docs/reports/fivefold_scene.json")
    args = ap.parse_args()

    folds = [int(x) for x in args.folds.split(",") if x.strip()]
    results = []

    for n in folds:
        print(f"\n===== fold {n} =====", flush=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"fold{n}_"))
        try:
            # 1) 用该折重建数据集（同一份清洗规则，只换 fold）
            run([PY, "scripts/build_dataset_v3.py", "--source", args.raw,
                 "--out", str(tmp), "--fold", str(n),
                 "--accept-assist", args.assist, "--force"])

            # 2) 训场景头（每折一个模型，不共享权重）
            name = f"fivefold_scene_f{n}"
            run([PY, "scripts/train_scene_cls.py",
                 "--model", "models/pretrained/yolo26n-cls.pt",
                 "--data", str(tmp / "cls" / f"fold{n}"),
                 "--epochs", str(args.epochs), "--imgsz", str(args.imgsz),
                 "--batch", "8", "--device", args.device, "--name", name,
                 "--project", "var/runs"])

            # 3) 只在该折的 val 上评
            weights = ROOT / "var/runs" / name / "weights/best.pt"
            ev = tmp / "eval.json"
            run([PY, "scripts/eval_heads.py", "--weights", str(weights),
                 "--data", str(tmp / "cls" / f"fold{n}"),
                 "--imgsz", str(args.imgsz), "--device", args.device,
                 "--out", str(ev)])

            d = json.loads(ev.read_text(encoding="utf-8"))
            results.append({"fold": n, "n_val": d["n"], "top1": d["top1"],
                            "top5": d["top5"],
                            "auto_coverage": d["auto_approve"]["coverage"],
                            "auto_precision": d["auto_approve"]["precision_inside"]})
            print(f"  fold{n}: top1={d['top1']:.4f} n={d['n']}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    vals = [r["top1"] for r in results]
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    summary = {
        "folds": results,
        "top1_mean": round(mean, 4),
        "top1_std": round(std, 4),
        "n_total_val": sum(r["n_val"] for r in results),
        "note": "每折独立训练；val 只含该折工单，无泄露",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
