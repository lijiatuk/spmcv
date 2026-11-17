#!/usr/bin/env python3
"""Split auto-labeled detection data by work order into train/val."""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from pathlib import Path

ONTOLOGY = [
    "vehicle_front", "license_plate", "driving_license", "vin_plate",
    "odometer_display", "service_sheet", "signature", "stamp",
    "filter_housing", "filter_element", "axle_housing", "wheel_hub",
    "drain_plug", "oil_stream", "oil_pan", "oil_gun", "oil_hose", "oil_drum",
]


def order_of(item: dict) -> str:
    m = re.search(r"IKD[A-Za-z0-9]+", item.get("image", ""))
    return m.group(0) if m else (item.get("order") or item["image"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="var/archive/data/det_auto")
    ap.add_argument("--out", default="data/det")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    orders = sorted({order_of(m) for m in manifest})
    random.Random(args.seed).shuffle(orders)
    n_val = max(1, int(len(orders) * args.val_ratio))
    val_orders = set(orders[:n_val])

    for split in ("train", "val"):
        img_dir = out / "images" / split
        lab_dir = out / "labels" / split
        for d in (img_dir, lab_dir):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

    counts = {"train": 0, "val": 0}
    for item in manifest:
        split = "val" if order_of(item) in val_orders else "train"
        img_src = Path(item["image"])
        lab_src = src / "labels" / (img_src.stem + ".txt")
        img_dst = out / "images" / split / img_src.name
        lab_dst = out / "labels" / split / (img_src.stem + ".txt")
        shutil.copy2(img_src, img_dst)
        if lab_src.exists():
            shutil.copy2(lab_src, lab_dst)
        counts[split] += 1

    yaml = out / "dataset.yaml"
    lines = [f"path: {out.resolve()}", "train: images/train", "val: images/val", "names:"]
    for i, name in enumerate(ONTOLOGY):
        lines.append(f"  {i}: {name}")
    yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"train={counts['train']} val={counts['val']} orders_val={len(val_orders)}")
    print(yaml.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
