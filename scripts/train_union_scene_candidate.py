#!/usr/bin/env python3
"""Train grouped-fold scene candidates without changing models/current."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
SCENES = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
LABEL_TO_SLUG = {name: meta["slug"] for name, meta in SCENES["class_map"].items()}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dataset_fold(fold_root: Path) -> str:
    rows = []
    for split_name in ("train", "val"):
        split_root = fold_root / split_name
        for class_dir in sorted(p for p in split_root.iterdir() if p.is_dir()):
            for image in sorted(p for p in class_dir.iterdir() if p.is_file()):
                rows.append({"split": split_name, "class": class_dir.name,
                             "file": image.name, "sha256": sha256(image)})
    return hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def validate_fold_data(part: dict, fold_root: Path, fold: int) -> None:
    train_orders = {str(row["order"]) for row in part["train"]}
    val_orders = {str(row["order"]) for row in part["val"]}
    if train_orders & val_orders:
        raise SystemExit(f"fold {fold} has train/validation work-order leakage")
    for split_name in ("train", "val"):
        rows = part[split_name]
        expected = {
            (LABEL_TO_SLUG.get(str(row["label"]), str(row["label"])),
             Path(str(row["src"])).name)
            for row in rows
        }
        if len(expected) != len(rows):
            raise SystemExit(f"fold {fold} {split_name} split has duplicate class/filename keys")
        split_dir = fold_root / split_name
        if not split_dir.is_dir():
            raise SystemExit(f"fold {fold} {split_name} directory is missing: {split_dir}")
        actual = {
            (class_dir.name, image.name)
            for class_dir in split_dir.iterdir() if class_dir.is_dir()
            for image in class_dir.iterdir() if image.is_file()
        }
        if actual != expected:
            missing, extra = sorted(expected - actual), sorted(actual - expected)
            raise SystemExit(
                f"fold {fold} {split_name} data does not match grouped split; "
                f"missing={missing[:5]} extra={extra[:5]}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", default="0,1,2,3,4")
    parser.add_argument("--split", default="data/processed/union/splits_group_5fold.json")
    parser.add_argument("--data", default="data/processed/union/cls")
    parser.add_argument("--model", default="models/pretrained/yolo26n-cls.pt")
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--name", default="union_scene_candidate")
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    split_path = Path(args.split)
    if not split_path.is_absolute():
        split_path = ROOT / split_path
    data_root = Path(args.data)
    if not data_root.is_absolute():
        data_root = ROOT / data_root
    if not model_path.is_file() or not split_path.is_file():
        raise SystemExit("model checkpoint or grouped split file is missing")

    split = json.loads(split_path.read_text(encoding="utf-8"))
    split_folds = {int(item["fold"]): item for item in split.get("splits", [])}
    folds = [int(value) for value in args.folds.split(",") if value.strip()]
    if not folds or len(folds) != len(set(folds)) or set(folds) - set(split_folds):
        raise SystemExit("folds must be unique and present in the grouped split")
    seen_val_orders = {}
    seen_val_sources = set()
    source_images = sorted({str(Path(row["src"]).resolve())
                            for part in split_folds.values() for row in part["val"]})
    for fold, part in split_folds.items():
        train_orders = {str(row["order"]) for row in part["train"]}
        val_orders = {str(row["order"]) for row in part["val"]}
        if train_orders & val_orders:
            raise SystemExit(f"fold {fold} has train/validation work-order leakage")
        for order in val_orders:
            previous = seen_val_orders.setdefault(order, fold)
            if previous != fold:
                raise SystemExit(f"work order {order} occurs in validation folds {previous} and {fold}")
        for row in part["val"]:
            source = str(Path(row["src"]).resolve())
            if source in seen_val_sources:
                raise SystemExit(f"source image appears in multiple validation folds: {source}")
            seen_val_sources.add(source)
    if any(not Path(source).is_file() for source in source_images):
        raise SystemExit("one or more split source images are missing")
    image_manifest = [{"path": source, "sha256": sha256(Path(source))}
                      for source in source_images]
    image_manifest_sha = hashlib.sha256(json.dumps(
        image_manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()

    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    fold_results = []
    for fold in folds:
        data = data_root / f"fold{fold}"
        validate_fold_data(split_folds[fold], data, fold)
        run_name = f"{args.name}_f{fold}_{run_tag}"
        print(f"\n===== {run_name} =====", flush=True)
        model = YOLO(str(model_path))
        model.train(
            data=str(data), epochs=args.epochs, imgsz=args.imgsz,
            batch=args.batch, device=args.device,
            project=str(ROOT / "var/runs"), name=run_name, exist_ok=False,
            pretrained=True, optimizer="auto", patience=10, workers=2,
            seed=42 + fold, deterministic=True,
        )
        weights = ROOT / "var/runs" / run_name / "weights/best.pt"
        if not weights.is_file():
            raise SystemExit(f"training did not produce {weights}")
        fold_results.append({
            "fold": fold,
            "data": str(data.relative_to(ROOT)),
            "dataset_manifest_sha256": sha256_dataset_fold(data),
            "weights": str(weights.relative_to(ROOT)),
            "sha256": sha256(weights),
        })

    manifest = {
        "candidate_only": True,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "architecture_checkpoint": str(model_path),
        "architecture_checkpoint_sha256": sha256(model_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": args.device,
        "seed_base": 42,
        "split_file": str(split_path.relative_to(ROOT)),
        "split_sha256": sha256(split_path),
        "source_image_count": len(image_manifest),
        "source_image_manifest_sha256": image_manifest_sha,
        "folds": fold_results,
    }
    out = ROOT / "var/runs" / f"{args.name}_{'-'.join(map(str, folds))}_{run_tag}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nCandidate manifest: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
