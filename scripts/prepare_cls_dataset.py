#!/usr/bin/env python3
"""Build train/val classification folders from the grouped split.

It copies images (not symlinks) so Ultralytics and sklearn can read them
without path surprises. Same work order never appears in both sides.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split-json", default="data/splits/group_5fold.json")
    ap.add_argument("--fold", type=int, default=0)
    ap.add_argument("--out", default="data/processed/cls")
    ap.add_argument("--source", required=True,
                    help="root of SPM系统AI识别训练照片")
    ap.add_argument("--extra", default="",
                    help="extra labeled folder (class_map keys) added to train only")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    root = Path(args.source).resolve()
    split = json.loads(Path(args.split_json).read_text(encoding="utf-8"))
    config = json.loads(Path("app/scenes.json").read_text(encoding="utf-8"))
    class_map = config["class_map"]

    fold = next(f for f in split["splits"] if f["fold"] == args.fold)
    out = Path(args.out)
    if out.exists() and args.force:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    stats = {}
    for split_name, items in (("train", fold["train"]), ("val", fold["val"])):
        counts = Counter()
        for item in items:
            meta = class_map.get(item["label"])
            if not meta:
                print("skip unknown label:", item["label"])
                continue
            src = root / item["path"]
            if not src.is_file():
                print("missing:", src)
                continue
            dst = out / split_name / meta["slug"]
            dst.mkdir(parents=True, exist_ok=True)
            target = dst / f"{item['order']}__{src.name}"
            shutil.copy2(src, target)
            counts[meta["slug"]] += 1
        stats[split_name] = dict(sorted(counts.items()))

    if args.extra:
        extra_root = Path(args.extra).resolve()
        extra_counts = Counter()
        for src in sorted(extra_root.rglob("*")):
            if not src.is_file() or src.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            meta = class_map.get(src.parent.name)
            if not meta:
                print("skip extra (unknown folder):", src.parent.name)
                continue
            dst = out / "train" / meta["slug"]
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / f"ext__{src.name}")
            extra_counts[meta["slug"]] += 1
        stats["extra"] = dict(sorted(extra_counts.items()))

    (out / "manifest.json").write_text(
        json.dumps({"source": str(root), "fold": args.fold, "stats": stats},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
