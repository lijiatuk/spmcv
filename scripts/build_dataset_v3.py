#!/usr/bin/env python3
"""Build a clean, work-order-grouped dataset from the 2026-11-11 re-curated drop.

The product team re-sorted `SPM系统AI识别训练照片` into sub-buckets that carry a
*meaning* the folder name alone no longer expresses:

    02_故障及修复照片/01_桥放油/滤芯拆卸过程照片/   <- step was "bridge drain",
                                                      picture shows filter removal

So the old rule "folder = label" now over-labels. This script encodes the
curation explicitly and emits five buckets instead of one label per file:

    core      直放在 12 类目录、且不是「待分类_」的干净样本 —— 可直接训练/评测
    hard      同一类里的「不合格*」子桶 —— 仍是该类，但证据质量差，评测需单列
    cross     子桶名本身是另一个 12 类 —— 疑似跨类错放，需人工确认后并入
    review    新件滤芯照片 / 滤芯原始状态 等 —— 语义介于「有/无滤芯」之间，需产品拍板
    negative  过程照/中间页/螺栓/车头/无用 —— 明确不属于 12 类，留作拒识负样本
    pending   「待分类_」前缀 —— 产品未确认，不进训练

Outputs (default `data/processed/v3/`):
    manifest.csv                每个源文件一行：决策、类别、工单、sha256、尺寸
    splits_group_5fold.json     按工单分组的 5 折（只含可训练样本）
    stats.json                  各桶计数与被排除原因
    excluded/<bucket>.csv       被排除的清单，交给产品复核
    cls/fold0/{train,val}/<slug>/   fold-0 的 Ultralytics 分类目录

Usage:
    python scripts/build_dataset_v3.py \
        --source "data/raw/recut" --out data/processed/v3 --include-hard
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
ORDER_RE = re.compile(r"(IKD\d+)")

# 12 个合法类目录名 -> slug，来自 app/scenes.json
CANON: dict[str, str] = {}


def load_class_map(path: Path) -> dict[str, str]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return {name: meta["slug"] for name, meta in cfg["class_map"].items()}


# 大类目录下、不属于 12 类的整目录：整桶判负
NEGATIVE_TOP = {"无用照片", "车架VIN", "拆滤芯的过程", "检查紧固照片",
                "滤芯原始状态", "车头照片", "中间页"}
# 子桶：同一类的低质量样本
HARD_PREFIX = "不合格"
# 子桶：语义待产品确认
REVIEW_SUBS = {"新件滤芯照片", "滤芯原始状态"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_orders(csv_path: Path) -> dict[str, str]:
    """filename -> work order id, from the product's manifest when present."""
    if not csv_path.is_file():
        return {}
    out = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("文件名") or "").strip()
            order = (row.get("维修工单号") or "").strip()
            if name and order:
                out[name] = order
    return out


NUMERIC_PREFIX = re.compile(r"^\d+_")


def short_names(canon: dict[str, str]) -> dict[str, str]:
    """去掉 `01_` 这类序号前缀后的别名表，用于识别错放在子桶里的同类照片。"""
    out = {}
    for name in canon:
        out[NUMERIC_PREFIX.sub("", name)] = name
    return out


def classify_rel(rel: Path, canon: dict[str, str],
                 alias: dict[str, str]) -> tuple[str, str | None, str]:
    """Return (bucket, target_class_name_or_None, reason) for one relative path."""
    parts = rel.parts
    name = parts[-1]
    pending = name.startswith("待分类_")

    if pending:
        return "pending", None, "待分类_ 前缀"

    if len(parts) == 3:                      # <category>/<class>/<file>
        cls = parts[1]
        if cls in canon:
            return "core", cls, "直放 12 类目录"
        if cls in NEGATIVE_TOP:
            return "negative", None, f"非 12 类目录 {cls}"
        return "negative", None, f"未知目录 {cls}"

    if len(parts) == 4:                      # <category>/<class>/<sub>/<file>
        cls, sub = parts[1], parts[2]
        if sub in canon:                     # 子桶名本身是另一个合法类
            return "cross", sub, f"{cls} 下的子桶指向 {sub}"
        if sub in alias and alias[sub] != cls:
            # 子桶名是「无滤芯」「桥放油」这类短名，前缀序号被去掉了
            return "cross", alias[sub], f"{cls} 下的子桶指向 {alias[sub]}"
        if sub.startswith(HARD_PREFIX):
            return "hard", cls, f"{cls} 的{ HARD_PREFIX }样本"
        if sub in REVIEW_SUBS:
            return "review", cls, f"语义待确认：{sub}"
        return "negative", None, f"过程/无关照片：{cls}/{sub}"

    return "review", None, "目录层级异常"


def group_folds(orders: list[str], images_per_order: dict[str, int],
                k: int, seed: int,
                class_counts: dict[str, dict[str, int]] | None = None,
                class_totals: dict[str, int] | None = None) -> list[list[str]]:
    """Assign whole work orders to k folds.

    Balances total images, and when class stats are given also balances each
    class across folds —否则 94 个工单里油液类样本少，fold-0 的 val 可能只剩
    3 张 bridge_drain，评测噪声大到没有意义。
    """
    rng = random.Random(seed)
    order_list = sorted(orders)
    rng.shuffle(order_list)
    order_list.sort(key=lambda o: -images_per_order.get(o, 0))

    folds: list[list[str]] = [[] for _ in range(k)]
    load = [0] * k
    per_fold_class = [Counter() for _ in range(k)]
    for order in order_list:
        n = images_per_order.get(order, 0)
        counts = (class_counts or {}).get(order, {})

        def cost(j: int) -> tuple[float, int]:
            worst = 0.0
            for cls, c in counts.items():
                total = (class_totals or {}).get(cls, 0) or 1
                worst = max(worst, (per_fold_class[j][cls] + c) / total)
            return (worst, load[j])

        i = min(range(k), key=cost)
        folds[i].append(order)
        load[i] += n
        per_fold_class[i].update(counts)
    return folds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/raw/recut")
    ap.add_argument("--out", default="data/processed/v3")
    ap.add_argument("--config", default="app/scenes.json")
    ap.add_argument("--csv", default="照片整理清单.csv",
                    help="relative to --source; product manifest with work order ids")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fold", type=int, default=0, help="fold to export")
    ap.add_argument("--include-hard", action="store_true",
                    help="treat 不合格* samples as trainable (flagged separately)")
    ap.add_argument("--accept-assist", default="",
                    help="assist_labels.csv；其中『采纳』行并入训练集")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    source = Path(args.source).resolve()
    out = Path(args.out)
    if out.exists() and args.force:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    canon = load_class_map(Path(args.config))
    alias = short_names(canon)
    orders = load_orders(source / args.csv)

    files: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMG_EXT:
            continue
        rel = path.relative_to(source)
        digest = sha256(path)
        bucket, cls, reason = classify_rel(rel, canon, alias)
        name = path.name
        order = orders.get(name) or (ORDER_RE.search(name).group(1) if ORDER_RE.search(name) else "")
        if digest in seen:
            bucket, reason = "duplicate", f"与 {seen[digest]} 内容相同"
        else:
            seen[digest] = str(rel)
        files.append({"path": rel, "name": name, "bucket": bucket, "class": cls,
                      "reason": reason, "order": order, "sha256": digest,
                      "size": path.stat().st_size})

    # ---- 把模型辅助标注里『与目录一致且高置信』的行并入训练集 ----
    if args.accept_assist:
        accepted = 0
        by_path = {str(f["path"]): f for f in files}
        with Path(args.accept_assist).open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if not row["suggestion"].startswith("采纳"):
                    continue
                item = by_path.get(row["path"].replace("\\", "/"))
                if item is None:
                    continue
                item["bucket"] = "core"
                item["class"] = row["folder_class"]
                item["reason"] = "模型辅助标注采纳（与目录一致）"
                accepted += 1
        print(f"[accept-assist] 并入 {accepted} 张")

    # ---- manifest ----
    with (out / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "name", "bucket", "class",
                                           "reason", "order", "sha256", "size"])
        w.writeheader()
        for row in files:
            w.writerow({**row, "path": str(row["path"])})

    # ---- 被排除清单，交给产品复核 ----
    exc = out / "excluded"
    exc.mkdir(exist_ok=True)
    for bucket in ("pending", "cross", "review", "negative", "hard", "duplicate"):
        rows = [f for f in files if f["bucket"] == bucket]
        if not rows:
            continue
        with (exc / f"{bucket}.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["path", "bucket", "class", "reason",
                                               "order", "sha256"])
            w.writeheader()
            for r in rows:
                w.writerow({"path": r["path"], "bucket": r["bucket"],
                            "class": r["class"] or "", "reason": r["reason"],
                            "order": r["order"], "sha256": r["sha256"]})

    # ---- 可训练集合 ----
    trainable = [f for f in files if f["bucket"] == "core"]
    if args.include_hard:
        trainable += [f for f in files if f["bucket"] == "hard"]
    trainable = [f for f in trainable if f["order"]]

    per_order = Counter(f["order"] for f in trainable)
    class_counts: dict[str, Counter] = defaultdict(Counter)
    for f in trainable:
        class_counts[f["order"]][f["class"]] += 1
    class_totals = Counter(f["class"] for f in trainable)
    folds = group_folds(sorted(per_order), per_order, args.folds, args.seed,
                        class_counts, class_totals)
    splits = []
    for i, fold_orders in enumerate(folds):
        hold = set(fold_orders)
        splits.append({
            "fold": i,
            "train": [{"path": str(f["path"]), "label": f["class"],
                       "order": f["order"], "bucket": f["bucket"]}
                      for f in trainable if f["order"] not in hold],
            "val": [{"path": str(f["path"]), "label": f["class"],
                     "order": f["order"], "bucket": f["bucket"]}
                    for f in trainable if f["order"] in hold],
            "train_orders": sorted({f["order"] for f in trainable if f["order"] not in hold}),
            "val_orders": sorted(hold),
        })
    (out / "splits_group_5fold.json").write_text(
        json.dumps({"root": str(source), "seed": args.seed, "splits": splits},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 导出 fold 分类目录（统一改名为 .jpg：源文件全部是 JPEG，只是名字写成了 .png） ----
    cls_root = out / "cls" / f"fold{args.fold}"
    if cls_root.exists():
        shutil.rmtree(cls_root)
    fold = next(s for s in splits if s["fold"] == args.fold)
    for split_name, items in (("train", fold["train"]), ("val", fold["val"])):
        for item in items:
            slug = canon[item["label"]]
            dst = cls_root / split_name / slug
            dst.mkdir(parents=True, exist_ok=True)
            src = source / item["path"]
            shutil.copy2(src, dst / f"{item['order']}__{src.stem}.jpg")

    # ---- 统计 ----
    by_bucket = Counter(f["bucket"] for f in files)
    by_class = Counter(f["class"] for f in files if f["bucket"] in ("core",))
    train_classes = Counter(f["class"] for f in trainable)
    stats = {
        "source": str(source),
        "total_files": len(files),
        "buckets": dict(sorted(by_bucket.items())),
        "trainable": len(trainable),
        "include_hard": args.include_hard,
        "trainable_per_class": dict(sorted(train_classes.items())),
        "core_per_class": dict(sorted(by_class.items())),
        "orders": len(per_order),
        "fold_sizes": [(len(s["train"]), len(s["val"])) for s in splits],
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\n输出目录：{out.resolve()}")
    print(f"fold{args.fold} 分类数据：{cls_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
