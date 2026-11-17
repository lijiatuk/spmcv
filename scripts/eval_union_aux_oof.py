#!/usr/bin/env python3
"""Evaluate candidate auxiliary heads on their matching grouped validation folds."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_heads import evaluate

ROLES = ("group", "action", "bin_axle", "bin_tire", "bin_filter")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dataset(dataset: Path) -> str:
    rows = []
    for split_name in ("train", "val"):
        split_root = dataset / split_name
        if not split_root.is_dir():
            raise SystemExit(f"candidate dataset split is missing: {split_root}")
        for class_dir in sorted(p for p in split_root.iterdir() if p.is_dir()):
            for image in sorted(p for p in class_dir.iterdir() if p.is_file()):
                rows.append({"split": split_name, "class": class_dir.name,
                             "file": image.name, "sha256": sha256(image)})
    return hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--split", default="data/processed/union/splits_group_5fold.json")
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--auto-conf", type=float, default=0.85)
    parser.add_argument("--out", default="docs/reports/aux_candidate_oof.json")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    split_path = Path(args.split)
    split = json.loads(split_path.read_text(encoding="utf-8"))
    if manifest.get("split_sha256") != sha256(split_path):
        raise SystemExit("candidate manifest is missing the current grouped split SHA-256")
    split_folds = {int(row["fold"]): row for row in split["splits"]}
    folds = manifest.get("folds") or []
    fold_ids = [int(row["fold"]) for row in folds]
    if len(fold_ids) != len(set(fold_ids)):
        raise SystemExit("candidate manifest has duplicate folds")
    if set(fold_ids) != set(split_folds):
        raise SystemExit(f"candidate folds {sorted(fold_ids)} do not cover split folds {sorted(split_folds)}")

    validation_orders: dict[str, int] = {}
    for fold, part in split_folds.items():
        train = {str(row["order"]) for row in part["train"]}
        val = {str(row["order"]) for row in part["val"]}
        if train & val:
            raise SystemExit(f"fold {fold} has train/validation order leakage")
        for order in val:
            previous = validation_orders.setdefault(order, fold)
            if previous != fold:
                raise SystemExit(f"work order {order} appears in validation folds {previous} and {fold}")

    source_paths = sorted({str(Path(row["src"]).resolve())
                           for part in split_folds.values() for row in part["val"]})
    source_manifest = []
    for source in source_paths:
        path = Path(source)
        if not path.is_file():
            raise SystemExit(f"split source image is missing: {source}")
        source_manifest.append({"path": source, "sha256": sha256(path)})
    source_manifest_sha = hashlib.sha256(json.dumps(
        source_manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if manifest.get("source_image_manifest_sha256") != source_manifest_sha:
        raise SystemExit("candidate source image manifest does not match the current split data")

    per_role: dict[str, list[dict]] = {role: [] for role in ROLES}
    for fold in sorted(folds, key=lambda row: int(row["fold"])):
        fold_id = int(fold["fold"])
        roles = fold.get("roles", {})
        if set(roles) != set(ROLES):
            raise SystemExit(f"fold {fold_id} role set mismatch: {sorted(roles)}")
        for role in ROLES:
            item = roles[role]
            weights = ROOT / item["weights"]
            data = ROOT / item["data"]
            if not weights.is_file() or not data.is_dir():
                raise SystemExit(f"fold {fold_id} {role} candidate files are missing")
            if sha256(weights) != item.get("sha256"):
                raise SystemExit(f"fold {fold_id} {role} weight SHA-256 mismatch")
            if sha256_dataset(data) != item.get("dataset_manifest_sha256"):
                raise SystemExit(f"fold {fold_id} {role} dataset manifest SHA-256 mismatch")
            result = evaluate(weights, data, args.imgsz, args.device, args.auto_conf)
            per_role[role].append({
                "fold": fold_id,
                "weights": str(weights.relative_to(ROOT)),
                "sha256": item["sha256"],
                "n": result["n"],
                "correct": round(result["top1"] * result["n"]),
                "top1": result["top1"],
                "top5": result["top5"],
                "auto_approve": result["auto_approve"],
                "auto_correct": round((result["auto_approve"]["precision_inside"] or 0.0) *
                                       result["auto_approve"]["n_auto"]),
                "per_class": result["per_class"],
                "confusions": result["confusions"],
            })
            print(f"fold{fold_id} {role}: top1={result['top1']:.2%} n={result['n']}", flush=True)

    summary = {}
    for role, rows in per_role.items():
        n = sum(row["n"] for row in rows)
        correct = sum(row["correct"] for row in rows)
        auto_n = sum(row["auto_approve"]["n_auto"] for row in rows)
        auto_correct = sum(row["auto_correct"] for row in rows)
        summary[role] = {
            "n": n,
            "top1": correct / n if n else None,
            "top5": sum(row["top5"] * row["n"] for row in rows) / n if n else None,
            "auto_coverage": auto_n / n if n else None,
            "auto_precision": auto_correct / auto_n if auto_n else None,
            "folds": rows,
        }

    report = {
        "evaluation": "grouped_auxiliary_out_of_fold",
        "candidate_manifest": str(manifest_path),
        "candidate_manifest_sha256": sha256(manifest_path),
        "source_split": str(split_path),
        "source_split_sha256": sha256(split_path),
        "source_image_count": len(source_manifest),
        "source_image_manifest_sha256": source_manifest_sha,
        "imgsz": args.imgsz,
        "device": args.device,
        "auto_confidence_threshold": args.auto_conf,
        "candidate_only": True,
        "roles": summary,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report written: {out}")
    for role, result in summary.items():
        print(f"OOF {role}: top1={result['top1']:.2%} n={result['n']} "
              f"auto={result['auto_coverage']:.2%}/{result['auto_precision']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
