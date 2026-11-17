#!/usr/bin/env python3
"""把「旧数据（已清洗）」和「新数据（771 张）」合并成一个统一数据集，按工单分 5 折。

旧数据用 `data/processed/v5/manifest.csv` 的 bucket 做筛选，只取 core/hard；
新数据没有子桶，整批视为 core。

输出：
    data/processed/union/splits_group_5fold.json
    data/processed/union/cls/fold{N}/{train,val}/<slug>/  （符号链接，不复制）

用法：
    python scripts/build_union.py
    python scripts/build_union.py --fold 0          # 只导出 fold0 目录
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
SLUG = {name: meta["slug"] for name, meta in CONFIG["class_map"].items()}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ORDER_RE = re.compile(r"(IKD\d+)")
TRAINABLE = {"core", "hard"}


def orders_from(name: str) -> str | None:
    m = ORDER_RE.search(name)
    return m.group(1) if m else None


def collect_old() -> list[dict]:
    out = []
    man = ROOT / "data/processed/v5/manifest.csv"
    base = ROOT / "data/raw/recut"
    for r in csv.DictReader(man.open(encoding="utf-8")):
        r = {k.lstrip("\ufeff"): v for k, v in r.items()}
        if r.get("bucket") not in TRAINABLE:
            continue
        p = base / r["path"]
        if not p.is_file():
            continue
        out.append({"src": str(p), "label": r["class"], "order": r["order"], "from": "old"})
    return out


def collect_new(new_root: Path) -> list[dict]:
    out = []
    for d in sorted(p for p in new_root.rglob("*") if p.is_dir()):
        if d.name not in SLUG:
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in IMG_EXT:
                continue
            order = orders_from(f.name)
            if not order:
                continue
            out.append({"src": str(f), "label": d.name, "order": order, "from": "new"})
    return out


def ensure_export_target(link: Path, source: Path) -> None:
    """Skip only a correct source symlink; never trust stale copied files."""
    if not link.exists() and not link.is_symlink():
        link.symlink_to(source)
        return
    if link.is_symlink() and link.resolve() == source.resolve():
        return
    raise SystemExit(
        f"refusing to reuse stale dataset entry: {link}; "
        "build into a fresh output directory"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", default="data/SPM系统AI识别训练照片_新增_20261111-20261113")
    ap.add_argument("--out", default="data/processed/union")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fold", type=int, default=-1, help="导出哪一折的目录；-1 表示全部")
    args = ap.parse_args()

    old = collect_old()
    new = collect_new(ROOT / args.new)
    items = old + new
    print(f"旧数据 {len(old)} 张，新数据 {len(new)} 张，合计 {len(items)} 张")

    by_order: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_order[it["order"]].append(it)
    orders = sorted(by_order)
    print(f"工单数 {len(orders)}")

    rng = random.Random(args.seed)
    rng.shuffle(orders)
    folds: list[list[str]] = [[] for _ in range(args.folds)]
    for i, o in enumerate(orders):
        folds[i % args.folds].append(o)

    out_dir = ROOT / args.out
    (out_dir).mkdir(parents=True, exist_ok=True)

    splits = []
    for fi, val_orders in enumerate(folds):
        val_set = set(val_orders)
        train = [it for it in items if it["order"] not in val_set]
        val = [it for it in items if it["order"] in val_set]
        splits.append({
            "fold": fi,
            "train": [{"src": t["src"], "label": t["label"], "order": t["order"],
                       "bucket": t["from"]} for t in train],
            "val": [{"src": t["src"], "label": t["label"], "order": t["order"],
                     "bucket": t["from"]} for t in val],
            "train_orders": [o for o in orders if o not in val_set],
            "val_orders": val_orders,
        })
        print(f"  fold{fi}: train {len(train)} / val {len(val)}")

    (out_dir / "splits_group_5fold.json").write_text(
        json.dumps({"root": str(out_dir), "seed": args.seed, "splits": splits},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 类别分布
    dist = Counter(i["label"] for i in items)
    print("\n类别分布：")
    for k, v in dist.most_common():
        print(f"  {k:<28} {v}")

    # 导出目录（符号链接）
    targets = range(args.folds) if args.fold < 0 else [args.fold]
    for fi in targets:
        s = splits[fi]
        for part in ("train", "val"):
            for it in s[part]:
                d = out_dir / "cls" / f"fold{fi}" / part / SLUG[it["label"]]
                d.mkdir(parents=True, exist_ok=True)
                link = d / Path(it["src"]).name
                ensure_export_target(link, Path(it["src"]))
        print(f"  导出 fold{fi} 完成")

    print(f"\n输出：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
