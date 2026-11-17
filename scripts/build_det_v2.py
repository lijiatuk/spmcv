#!/usr/bin/env python3
"""把 VLX 自动标注的末页合并进现有 det 数据集，产出 det_v2（按工单切分，防泄漏）。

用法：
    python scripts/build_det_v2.py \
        --base data/processed/det \
        --new var/det_sign_auto \
        --out data/processed/det_v2
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

NAMES = ["vehicle_front", "license_plate", "driving_license", "vin_plate",
         "odometer_display", "service_sheet", "signature", "stamp",
         "filter_housing", "filter_element", "axle_housing", "wheel_hub",
         "drain_plug", "oil_stream", "oil_pan", "oil_gun", "oil_hose", "oil_drum"]


def order_of(name: str) -> str:
    m = re.search(r"(IKD\d+)", name)
    return m.group(1) if m else name.rsplit("_", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data/processed/det")
    ap.add_argument("--new", default="var/det_sign_auto")
    ap.add_argument("--out", default="data/processed/det_v2")
    ap.add_argument("--val-ratio", type=float, default=0.15)
    args = ap.parse_args()

    base, new, out = Path(args.base), Path(args.new), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    # 1) 沿用现有数据集的划分（含各场景，天然带负样本）
    copied = {"train": 0, "val": 0}
    val_orders = set()
    for split in ("train", "val"):
        for img in (base / "images" / split).iterdir():
            if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            shutil.copy2(img, out / "images" / split / img.name)
            lab = base / "labels" / split / f"{img.stem}.txt"
            if lab.is_file():
                shutil.copy2(lab, out / "labels" / split / lab.name)
            else:
                (out / "labels" / split / f"{img.stem}.txt").write_text("")
            copied[split] += 1
            if split == "val":
                val_orders.add(order_of(img.name))

    # 2) 新末页按工单归入 train/val：同一工单不跨集
    train_orders = {order_of(p.name) for p in (base / "images" / "train").iterdir()}
    new_count = {"train": 0, "val": 0}
    for img in sorted((new / "images").iterdir()):
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        order = order_of(img.name)
        if order in val_orders:
            split = "val"
        elif order in train_orders or hash(order) % 100 >= int(args.val_ratio * 100):
            split = "train"
        else:
            split = "val"
        dst = out / "images" / split / f"service_sign__{img.name}"
        shutil.copy2(img, dst)
        lab = new / "labels" / f"{img.stem}.txt"
        text = lab.read_text() if lab.is_file() else ""
        (out / "labels" / split / f"{dst.stem}.txt").write_text(text)
        new_count[split] += 1

    (out / "dataset.yaml").write_text(
        f"""path: {out.resolve()}
train: images/train
val: images/val
names:
""" + "".join(f"  {i}: {n}\n" for i, n in enumerate(NAMES)), encoding="utf-8")

    stats = {"base": copied, "new": new_count,
             "total": {k: copied[k] + new_count[k] for k in copied}}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
