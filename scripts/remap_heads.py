#!/usr/bin/env python3
"""Derive the group / action / binary head datasets from the scene-class dataset.

The serving stack runs three extra heads on top of the 12-class scene classifier:
    group   3 classes: axle_oil / tire_oil / filter
    action  6 classes: bridge_drain / bridge_fill / tire_drain / tire_fill /
                       filter_none / filter_has
    binary  in-group 2-way: axle(drain|fill), tire(drain|fill), filter(none|has)

They must be rebuilt whenever the scene dataset changes, otherwise the heads keep
their old (noisier) labels. This script only remaps slugs — no new labeling.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

GROUP = {"bridge_drain": "axle_oil", "bridge_fill": "axle_oil",
         "tire_drain": "tire_oil", "tire_fill": "tire_oil",
         "filter_none": "filter", "filter_has": "filter"}
ACTION = {"bridge_drain", "bridge_fill", "tire_drain", "tire_fill",
          "filter_none", "filter_has"}
BINARY = {"axle": {"bridge_drain": "drain", "bridge_fill": "fill"},
          "tire": {"tire_drain": "drain", "tire_fill": "fill"},
          "filter": {"filter_none": "none", "filter_has": "has"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/processed/v5/cls/fold0")
    ap.add_argument("--out", default="data/processed/v5/heads")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--link", action="store_true",
                    help="symlink source images instead of copying them")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if out.exists() and args.force:
        shutil.rmtree(out)
    stats = {}

    def add_images(src_dir: Path, dst_dir: Path) -> int:
        dst_dir.mkdir(parents=True, exist_ok=True)
        added = 0
        for image in src_dir.iterdir():
            if not image.is_file():
                continue
            target = dst_dir / image.name
            if not target.exists():
                if args.link:
                    target.symlink_to(image.resolve())
                else:
                    shutil.copy2(image, target)
            added += 1
        return added

    for split in ("train", "val"):
        for cls_dir in sorted((src / split).iterdir()):
            if not cls_dir.is_dir():
                continue
            slug = cls_dir.name
            for name, dst in ((GROUP.get(slug), out / "group" / split / GROUP.get(slug, "")),
                              (slug if slug in ACTION else None, out / "action" / split / slug)):
                if not name:
                    continue
                stats[f"{dst.relative_to(out)}"] = stats.get(f"{dst.relative_to(out)}", 0) + add_images(cls_dir, dst)
            for group, mapping in BINARY.items():
                if slug in mapping:
                    dst = out / "binary" / group / split / mapping[slug]
                    stats[str(dst.relative_to(out))] = stats.get(str(dst.relative_to(out)), 0) + add_images(cls_dir, dst)
    for k in sorted(stats):
        print(f"  {k:<52} {stats[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
