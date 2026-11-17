#!/usr/bin/env python3
"""Train a complete set of auxiliary classification heads on grouped union folds.

Outputs are experimental candidates under var/runs. This script never changes
models/current; activation requires separate out-of-fold and business gate review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
HEADS = {
    "group": "group",
    "action": "action",
    "bin_axle": "binary/axle",
    "bin_tire": "binary/tire",
    "bin_filter": "binary/filter",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dataset(dataset: Path) -> str:
    rows = []
    for split_name in ("train", "val"):
        split_root = dataset / split_name
        for class_dir in sorted(p for p in split_root.iterdir() if p.is_dir()):
            for image in sorted(p for p in class_dir.iterdir() if p.is_file()):
                rows.append({"split": split_name, "class": class_dir.name,
                             "file": image.name, "sha256": sha256(image)})
    return hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", default="0")
    ap.add_argument("--source", default="data/processed/union/cls")
    ap.add_argument("--out", default="data/processed/union/heads")
    ap.add_argument("--split", default="data/processed/union/splits_group_5fold.json")
    ap.add_argument("--model", default="models/pretrained/yolo26n-cls.pt")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--roles", default=",".join(HEADS))
    args = ap.parse_args()
    split_path = ROOT / args.split
    if not split_path.is_file():
        raise SystemExit(f"grouped split file does not exist: {split_path}")
    split_payload = json.loads(split_path.read_text(encoding="utf-8"))
    available_folds = {int(item["fold"]) for item in split_payload.get("splits", [])}
    requested_folds = [int(value) for value in args.folds.split(",") if value.strip()]
    if not requested_folds or len(requested_folds) != len(set(requested_folds)):
        raise SystemExit("folds must be a nonempty, duplicate-free list")
    if set(requested_folds) - available_folds:
        raise SystemExit(f"requested folds are absent from split: {sorted(set(requested_folds) - available_folds)}")
    source_images = sorted({str(Path(row["src"]).resolve())
                            for fold in split_payload["splits"] for row in fold["val"]})
    missing_images = [path for path in source_images if not Path(path).is_file()]
    if missing_images:
        raise SystemExit(f"split source images are missing: {missing_images[:5]}")
    image_manifest = [{"path": path, "sha256": sha256(Path(path))} for path in source_images]
    image_manifest_sha256 = hashlib.sha256(json.dumps(
        image_manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()

    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    if not model_path.is_file():
        raise SystemExit(f"training model does not exist: {model_path}")
    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    unknown = set(roles) - set(HEADS)
    if unknown:
        raise SystemExit(f"unknown roles: {sorted(unknown)}")

    folds_out = []
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    for fold in requested_folds:
        source = ROOT / args.source / f"fold{fold}"
        heads = ROOT / args.out / f"fold{fold}"
        subprocess.run([
            sys.executable, str(ROOT / "scripts/remap_heads.py"),
            "--src", str(source), "--out", str(heads), "--force", "--link",
        ], cwd=ROOT, check=True)
        fold_result = {"fold": fold, "roles": {}}
        for role in roles:
            dataset = heads / HEADS[role]
            run_name = f"union_aux_{role}_f{fold}_{run_tag}"
            print(f"\n===== {run_name} =====", flush=True)
            model = YOLO(str(model_path))
            model.train(
                data=str(dataset), epochs=args.epochs, imgsz=args.imgsz,
                batch=args.batch, device=args.device,
                project=str(ROOT / "var/runs"), name=run_name, exist_ok=False,
                pretrained=True, optimizer="auto", patience=10, workers=2,
                seed=42 + fold, deterministic=True,
            )
            best = ROOT / "var/runs" / run_name / "weights/best.pt"
            if not best.is_file():
                raise SystemExit(f"training did not produce {best}")
            fold_result["roles"][role] = {
                "data": str(dataset.relative_to(ROOT)),
                "dataset_manifest_sha256": sha256_dataset(dataset),
                "weights": str(best.relative_to(ROOT)),
                "sha256": sha256(best),
            }
        folds_out.append(fold_result)

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_split": str((ROOT / args.source).relative_to(ROOT)),
        "split_file": str(split_path.relative_to(ROOT)),
        "split_sha256": sha256(split_path),
        "source_image_count": len(image_manifest),
        "source_image_manifest_sha256": image_manifest_sha256,
        "architecture_checkpoint": str(model_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": args.device,
        "seed_base": 42,
        "roles": roles,
        "candidate_only": True,
        "folds": folds_out,
    }
    out = ROOT / "var/runs" / f"union_aux_candidate_{'-'.join(str(f['fold']) for f in folds_out)}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCandidate manifest: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
