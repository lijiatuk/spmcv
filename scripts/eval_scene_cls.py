#!/usr/bin/env python3
"""Evaluate the scene classifier on the validation split."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="var/runs/cls_v1/weights/best.pt")
    ap.add_argument("--data", default="data/processed/cls")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--out", default="var/runs/cls_v1/eval.json")
    args = ap.parse_args()

    model = YOLO(args.weights)
    metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device)
    payload = {
        "top1": float(getattr(metrics, "top1", 0.0)),
        "top5": float(getattr(metrics, "top5", 0.0)),
        "fitness": float(getattr(metrics, "fitness", 0.0)),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
