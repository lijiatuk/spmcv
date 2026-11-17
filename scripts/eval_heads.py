#!/usr/bin/env python3
"""Per-class evaluation of the serving stack's classifier heads.

Reports what the business actually cares about, not just top-1:

    top1 / top5
    per-class precision / recall / F1
    confusion pairs (top 5)
    auto-approval proxy: coverage@conf>=0.85 and accuracy inside that set
      — this mirrors the runtime rule "高置信才放行，低置信转复核"

Usage:
    python scripts/eval_heads.py --weights var/runs/v5_scene_26n/weights/best.pt \
        --data data/processed/v5/cls/fold0 --out docs/reports/eval_v5_scene.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ultralytics import YOLO


def evaluate(weights: str, data: Path, imgsz: int, device: str,
             auto_conf: float = 0.85) -> dict:
    model = YOLO(weights)
    root = data / "val"
    names = {i: n for i, n in model.names.items()}

    tp = Counter(); fp = Counter(); fn = Counter()
    confusion = Counter()
    conf_correct = 0
    confident = 0
    total = 0
    conf_correct_all = 0
    top5_correct = 0

    for cls_dir in sorted(root.iterdir()):
        if not cls_dir.is_dir():
            continue
        true_slug = cls_dir.name
        for img in sorted(cls_dir.iterdir()):
            if not img.is_file():
                continue
            res = model.predict(str(img), imgsz=imgsz, device=device, verbose=False)[0]
            probs = res.probs
            pred_slug = names[int(probs.top1)]
            conf = float(probs.top1conf)
            total += 1

            top5 = [names[int(i)] for i in probs.top5]
            if true_slug in top5:
                top5_correct += 1

            if pred_slug == true_slug:
                tp[true_slug] += 1
                conf_correct_all += 1
                if conf >= auto_conf:
                    conf_correct += 1
            else:
                fn[true_slug] += 1
                fp[pred_slug] += 1
                confusion[(true_slug, pred_slug)] += 1

            if conf >= auto_conf:
                confident += 1

    classes = sorted({c.name for c in root.iterdir() if c.is_dir()})
    per_class = {}
    for c in classes:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        support = tp[c] + fn[c]
        per_class[c] = {
            "support": support, "precision": round(p, 4),
            "recall": round(r, 4), "f1": round(f1, 4),
        }

    return {
        "weights": weights,
        "val_dir": str(root),
        "n": total,
        "top1": round(conf_correct_all / total, 4) if total else 0.0,
        "top5": round(top5_correct / total, 4) if total else 0.0,
        "auto_approve": {
            "threshold": auto_conf,
            "coverage": round(confident / total, 4) if total else 0.0,
            "precision_inside": round(conf_correct / confident, 4) if confident else 0.0,
            "n_auto": confident,
        },
        "per_class": per_class,
        "confusions": [
            {"true": a, "pred": b, "count": c}
            for (a, b), c in confusion.most_common(12)
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", default="data/processed/v5/cls/fold0")
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--auto-conf", type=float, default=0.85)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    payload = evaluate(args.weights, Path(args.data), args.imgsz, args.device,
                       args.auto_conf)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"written: {out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
