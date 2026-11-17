"""训练数据集的“一键准备”：合并、去重、按工单分组切分、落 manifest。

为什么需要这一层：
  - 以前训练前要先在服务器上跑 `scripts/build_union.py`，换台机器就忘；
  - 旧脚本用绝对路径软链，打包到服务器后全部失效；
  - 上传批次只有 8:2 的 train/val，没有独立测试集，验收数字不可信；
  - 人工改过的标签只存在 SQLite 里，训练时没有被用上。

这里的输出是 `data/processed/datasets/<name>/{train,val,test}/<slug>/`，
全部使用**相对软链**，换机/换挂载点都不需要重建。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR
from app.services.feedback_store import feedback_store
from app.services.paths import portable_path

ROOT = BASE_DIR
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ORDER_RE = re.compile(r"(IKD\d+)")
SPLITS = ("train", "val", "test")
DEFAULT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}
TRAINABLE_BUCKETS = {"core", "hard"}


def _class_map() -> dict:
    cfg = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
    return cfg["class_map"]


CLASS_MAP = _class_map()
NAME_TO_SLUG = {name: meta["slug"] for name, meta in CLASS_MAP.items()}
SLUGS = list(dict.fromkeys(NAME_TO_SLUG.values()))
SLUG_SET = set(SLUGS)
LABEL_CN = {
    "arrival_plate": "车牌",
    "license_vin": "行驶证 VIN",
    "frame_vin": "车架 VIN",
    "odometer": "里程表",
    "bridge_drain": "桥放油",
    "bridge_fill": "桥加油",
    "tire_drain": "轮胎放油",
    "tire_fill": "轮胎加油",
    "filter_none": "无滤芯",
    "filter_has": "有滤芯",
    "service_front": "服务单首页",
    "service_sign": "服务单末页",
}


def order_of(name: str) -> str:
    m = ORDER_RE.search(name)
    return m.group(1) if m else Path(name).stem


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sources_all() -> list[dict]:
    """旧数据（manifest 的 core/hard）+ 新数据目录。"""
    items: list[dict] = []
    manifest = ROOT / "data/processed/v5/manifest.csv"
    if manifest.is_file():
        for row in csv.DictReader(manifest.open(encoding="utf-8")):
            row = {k.lstrip("\ufeff"): v for k, v in row.items()}
            if row.get("bucket") not in TRAINABLE_BUCKETS:
                continue
            slug = NAME_TO_SLUG.get(row.get("class", ""))
            src = ROOT / "data/raw/recut" / row.get("path", "")
            if not slug or not src.is_file():
                continue
            items.append({"src": src, "label": slug, "order": row.get("order") or order_of(src.name),
                          "origin": "old", "sha256": row.get("sha256") or ""})
    new_root = ROOT / "data/SPM系统AI识别训练照片_新增_20261111-20261113"
    if new_root.is_dir():
        for d in sorted(p for p in new_root.rglob("*") if p.is_dir()):
            slug = NAME_TO_SLUG.get(d.name)
            if not slug:
                continue
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in IMG_EXT:
                    items.append({"src": f, "label": slug, "order": order_of(f.name),
                                  "origin": "new", "sha256": ""})
    return items


def _sources_upload(batch: str) -> list[dict]:
    base = ROOT / "data/raw/uploads" / batch
    if not base.is_dir():
        raise FileNotFoundError(f"批次不存在: {batch}")
    ann = {str(portable_path(k)): v
           for k, v in feedback_store.annotations_for_batch(batch).items()}
    items: list[dict] = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        slug = NAME_TO_SLUG.get(d.name, d.name if d.name in SLUG_SET else None)
        if not slug:
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in IMG_EXT:
                continue
            final = slug
            row = ann.get(str(f))
            if row and row.get("final_label") in SLUG_SET:
                final = row["final_label"]
            items.append({"src": f, "label": final, "order": order_of(f.name),
                          "origin": f"upload:{batch}", "sha256": ""})
    return items


def _dedupe(items: list[dict]) -> tuple[list[dict], int]:
    """按内容去重：同一张图重复上传只保留一份。"""
    seen: dict[str, dict] = {}
    for it in items:
        digest = it.get("sha256") or ""
        if not digest:
            try:
                digest = _sha256(it["src"])
            except OSError:
                continue
            it["sha256"] = digest
        if digest in seen:
            continue
        seen[digest] = it
    return list(seen.values()), len(items) - len(seen)


def _allocate(items: list[dict], ratios: dict[str, float], seed: int) -> dict[str, list[dict]]:
    """按工单整组切分，保证同一工单的照片只落在一个 split。"""
    by_order: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_order[it["order"]].append(it)
    groups = list(by_order.values())
    random.Random(seed).shuffle(groups)

    total = sum(len(g) for g in groups)
    target = {s: max(1, round(total * ratios[s])) for s in SPLITS}
    # 先保证 val/test 至少有一个工单，否则“测试集”是空的。
    assigned: dict[str, list[dict]] = {s: [] for s in SPLITS}
    counts = {s: 0 for s in SPLITS}
    for g in groups:
        # 选择“离目标还差得最多”的 split；train 最后兜底
        deficits = {s: target[s] - counts[s] for s in SPLITS}
        if counts["val"] == 0 and len(groups) > 2:
            pick = "val"
        elif counts["test"] == 0 and len(groups) > 3:
            pick = "test"
        else:
            pick = max(SPLITS, key=lambda s: (deficits[s], s == "train"))
        assigned[pick].extend(g)
        counts[pick] += len(g)
    return assigned


def _manifest_stats(files: list[dict]) -> dict:
    stats = {s: Counter() for s in SPLITS}
    for f in files:
        stats[f["split"]][f["label"]] += 1
    return {s: dict(sorted(stats[s].items())) for s in SPLITS}


def build_dataset(source: str = "all", name: str = "", ratios: dict | None = None,
                  seed: int = 42, out_root: Path | None = None,
                  include_uploads: bool = False) -> dict:
    """构建一个可训练数据集，返回 manifest 摘要。"""
    ratios = {**DEFAULT_RATIOS, **(ratios or {})}
    if abs(sum(ratios.values()) - 1.0) > 1e-6:
        raise ValueError("train/val/test 比例之和必须为 1")

    if source.startswith("upload:"):
        batch = source.split(":", 1)[1]
        items = _sources_upload(batch)
        default_name = f"upload_{batch}"
    else:
        items = _sources_all()
        if include_uploads:
            uploads = ROOT / "data/raw/uploads"
            if uploads.is_dir():
                for d in sorted(p for p in uploads.iterdir() if p.is_dir()):
                    try:
                        items.extend(_sources_upload(d.name))
                    except FileNotFoundError:
                        continue
        default_name = "all"
    if not items:
        raise ValueError("没有可训练的图片：请检查 data/ 是否挂载完整")

    items, dup = _dedupe(items)
    split_items = _allocate(items, ratios, seed)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = (name or f"{default_name}_{stamp}").strip()
    name = re.sub(r"[^0-9A-Za-z_.-]+", "_", name)[:80] or f"dataset_{stamp}"
    out_root = out_root or ROOT / "data/processed/datasets"
    dest = out_root / name
    if dest.exists():
        name = f"{name}_{stamp}"
        dest = out_root / name

    files: list[dict] = []
    for split in SPLITS:
        for it in split_items[split]:
            target_dir = dest / split / it["label"]
            target_dir.mkdir(parents=True, exist_ok=True)
            link = target_dir / it["src"].name
            if link.exists() or link.is_symlink():
                link = target_dir / f"{it['sha256'][:8]}_{it['src'].name}"
            try:
                rel = os.path.relpath(it["src"].resolve(), link.parent.resolve())
                link.symlink_to(rel)
            except OSError:
                shutil.copy2(it["src"], link)
            files.append({"split": split, "label": it["label"], "order": it["order"],
                          "origin": it.get("origin", ""), "sha256": it.get("sha256", ""),
                          "file": str(link.relative_to(dest))})

    files.sort(key=lambda f: (f["split"], f["label"], f["file"]))
    content = hashlib.sha256(
        "\n".join(f"{f['sha256']}:{f['label']}:{f['order']}" for f in files).encode()
    ).hexdigest()
    counts = {s: sum(1 for f in files if f["split"] == s) for s in SPLITS}
    class_counts = _manifest_stats(files)
    warnings = []
    if dup:
        warnings.append(f"按内容去重 {dup} 张重复图片")
    for split in ("val", "test"):
        if counts[split] == 0:
            warnings.append(f"{split} 为空，比例无法满足（数据量太小）")
    for slug in SLUGS:
        if class_counts["train"].get(slug, 0) and not class_counts["test"].get(slug):
            warnings.append(f"{slug} 在测试集中没有样本")

    manifest = {
        "name": name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "seed": seed,
        "ratios": ratios,
        "counts": counts,
        "total": len(files),
        "classes": class_counts,
        "orders": {s: len({f["order"] for f in files if f["split"] == s}) for s in SPLITS},
        "content_hash": content,
        "warnings": warnings,
        "path": str(dest),
        "files": files,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return manifest


def list_datasets(out_root: Path | None = None) -> list[dict]:
    out_root = out_root or ROOT / "data/processed/datasets"
    out = []
    if not out_root.is_dir():
        return out
    for d in sorted(out_root.iterdir(), reverse=True):
        m = d / "manifest.json"
        if not d.is_dir() or not m.is_file():
            continue
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: data.get(k) for k in
                    ("name", "created_at", "source", "ratios", "counts", "total",
                     "orders", "warnings", "path")})
    return out


def delete_dataset(name: str, out_root: Path | None = None) -> dict:
    out_root = out_root or ROOT / "data/processed/datasets"
    if not name or "/" in name or name.startswith("."):
        raise ValueError("数据集名非法")
    dest = out_root / name
    if not dest.is_dir():
        raise FileNotFoundError(name)
    shutil.rmtree(dest)
    return {"deleted": name}
