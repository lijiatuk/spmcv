#!/usr/bin/env python3
"""Strict out-of-fold evaluation for the union scene models.

Each image is predicted only by the model whose validation fold contains its
work order. The report includes split and weight fingerprints so dashboard
metrics can be tied to immutable artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from ultralytics import YOLO


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dataset_fold(fold_root: Path) -> str:
    rows = []
    for split_name in ("train", "val"):
        split_root = fold_root / split_name
        if not split_root.is_dir():
            raise SystemExit(f"dataset fold directory is missing: {split_root}")
        for class_dir in sorted(p for p in split_root.iterdir() if p.is_dir()):
            for image in sorted(p for p in class_dir.iterdir() if p.is_file()):
                rows.append({
                    "split": split_name,
                    "class": class_dir.name,
                    "file": image.name,
                    "sha256": sha256(image),
                })
    return hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="data/processed/union/splits_group_5fold.json")
    ap.add_argument("--data", default="data/processed/union/cls")
    ap.add_argument("--weights-pattern", default="var/runs/union_scene_f{fold}/weights/best.pt")
    ap.add_argument("--manifest", default="",
                    help="候选折模型 manifest；优先于 --weights-pattern")
    ap.add_argument("--folds", default="0,1,2,3,4")
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--auto-conf", type=float, default=0.85)
    ap.add_argument("--out", default="docs/reports/verified_oof.json")
    args = ap.parse_args()

    split_path, data_root = Path(args.split), Path(args.data)
    split = json.loads(split_path.read_text(encoding="utf-8"))
    folds = [int(v) for v in args.folds.split(",") if v.strip()]
    if not folds or len(folds) != len(set(folds)):
        raise SystemExit("folds must be a nonempty, duplicate-free list")
    candidate_manifest = None
    candidate_manifest_path = None
    candidate_folds = {}
    if args.manifest:
        candidate_manifest_path = Path(args.manifest)
        candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
        if candidate_manifest.get("split_sha256") != sha256(split_path):
            raise SystemExit("candidate manifest grouped split SHA-256 mismatch")
        candidate_fold_rows = candidate_manifest.get("folds", [])
        candidate_folds = {int(item["fold"]): item for item in candidate_fold_rows}
        if len(candidate_folds) != len(candidate_fold_rows):
            raise SystemExit("candidate manifest has duplicate folds")
        if set(candidate_folds) != set(folds):
            raise SystemExit("candidate manifest folds must exactly match the requested OOF folds")
    scene_cfg = json.loads((Path(__file__).resolve().parents[1] / "app/scenes.json").read_text(encoding="utf-8"))
    label_to_slug = {name: meta["slug"] for name, meta in scene_cfg["class_map"].items()}
    fold_data = {int(s["fold"]): s for s in split["splits"]}
    details = []
    source_image_hashes = {}
    file_hash_cache = {}
    dataset_manifest_entries = []
    source_dataset_byte_mismatch_paths = set()
    weights_used = {}
    seen_orders: dict[str, int] = {}
    seen_sources: set[str] = set()
    confusion: Counter[tuple[str, str]] = Counter()
    support: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    class_confident: Counter[str] = Counter()
    class_confident_hits: Counter[str] = Counter()
    conf_bins = [0] * 10
    conf_bin_hits = [0] * 10
    conf_bin_sum = [0.0] * 10
    total_top5 = 0
    total_confident = 0
    confident_hits = 0
    folds_out = []

    for fold in folds:
        if fold not in fold_data:
            raise SystemExit(f"fold {fold} is missing from split file")
        part = fold_data[fold]
        train_orders = {str(item["order"]) for item in part["train"]}
        val_orders = {str(item["order"]) for item in part["val"]}
        overlap = train_orders & val_orders
        if overlap:
            raise SystemExit(f"fold {fold} work orders leak across train/val: {sorted(overlap)[:5]}")

        for split_name in ("train", "val"):
            expected_rows = part[split_name]
            expected_by_key = {
                (label_to_slug.get(str(item["label"]), str(item["label"])),
                 Path(str(item["src"])).name): str(Path(item["src"]).resolve())
                for item in expected_rows
            }
            if len(expected_by_key) != len(expected_rows):
                raise SystemExit(f"duplicate class/filename entries in fold {fold} {split_name} split")
            split_dir = data_root / f"fold{fold}" / split_name
            if not split_dir.is_dir():
                raise SystemExit(f"fold {fold} {split_name} directory is missing: {split_dir}")
            actual_by_key = {
                (class_dir.name, image.name): image
                for class_dir in sorted(p for p in split_dir.iterdir() if p.is_dir())
                for image in sorted(p for p in class_dir.iterdir() if p.is_file())
            }
            if actual_by_key.keys() != expected_by_key.keys():
                missing = sorted(expected_by_key.keys() - actual_by_key.keys())
                extra = sorted(actual_by_key.keys() - expected_by_key.keys())
                raise SystemExit(
                    f"fold {fold} {split_name} files do not match grouped split; "
                    f"missing={missing[:5]} extra={extra[:5]}"
                )
            for key, source in expected_by_key.items():
                source_path = Path(source)
                if not source_path.is_file():
                    raise SystemExit(f"source image is missing: {source}")
                dataset_path = actual_by_key[key]
                dataset_key = str(dataset_path.resolve())
                if source not in file_hash_cache:
                    file_hash_cache[source] = sha256(source_path)
                if dataset_key not in file_hash_cache:
                    file_hash_cache[dataset_key] = sha256(dataset_path)
                if file_hash_cache[source] != file_hash_cache[dataset_key]:
                    source_dataset_byte_mismatch_paths.add(source)
                dataset_manifest_entries.append({
                    "fold": fold,
                    "split": split_name,
                    "class": key[0],
                    "file": key[1],
                    "source_sha256": file_hash_cache[source],
                    "sha256": file_hash_cache[dataset_key],
                })

        if candidate_manifest is not None:
            if fold not in candidate_folds:
                raise SystemExit(f"candidate manifest does not include fold {fold}")
            recorded_data = Path(candidate_folds[fold]["data"])
            if not recorded_data.is_absolute():
                recorded_data = Path(__file__).resolve().parents[1] / recorded_data
            expected_data = data_root / f"fold{fold}"
            if not expected_data.is_absolute():
                expected_data = Path.cwd() / expected_data
            if recorded_data.resolve() != expected_data.resolve():
                raise SystemExit(f"fold {fold} evaluation data path differs from candidate manifest")
            weights = Path(candidate_folds[fold]["weights"])
            if not weights.is_absolute():
                weights = Path(__file__).resolve().parents[1] / weights
        else:
            weights = Path(args.weights_pattern.format(fold=fold))
        if not weights.is_file():
            raise SystemExit(f"fold {fold} weights do not exist: {weights}")
        if candidate_manifest is not None and sha256(weights) != candidate_folds[fold].get("sha256"):
            raise SystemExit(f"fold {fold} candidate weights SHA-256 mismatch")
        if (candidate_manifest is not None and
                candidate_folds[fold].get("dataset_manifest_sha256") != sha256_dataset_fold(
                    data_root / f"fold{fold}"
                )):
            raise SystemExit(f"fold {fold} candidate dataset manifest SHA-256 mismatch")
        weights_used[str(fold)] = {"path": str(weights), "sha256": sha256(weights)}
        model = YOLO(str(weights))
        val_root = data_root / f"fold{fold}" / "val"
        source_by_key = {}
        for item in part["val"]:
            source = str(Path(item["src"]).resolve())
            key = (label_to_slug.get(str(item["label"]), str(item["label"])), Path(source).name)
            if key in source_by_key:
                raise SystemExit(f"duplicate validation filename in fold {fold}: {key}")
            source_by_key[key] = item

        used_keys = set()
        fold_n = fold_hits = fold_top5 = fold_confident = fold_confident_hits = 0
        for class_dir in sorted(p for p in val_root.iterdir() if p.is_dir()):
            true_slug = class_dir.name
            for image_path in sorted(p for p in class_dir.iterdir() if p.is_file()):
                item = source_by_key.get((true_slug, image_path.name))
                if item is None:
                    raise SystemExit(f"validation file absent from split metadata: {image_path}")
                used_keys.add((true_slug, image_path.name))
                source = str(Path(item["src"]).resolve())
                source_path = Path(source)
                if not source_path.is_file():
                    raise SystemExit(f"source image is missing: {source}")
                source_image_hashes[source] = file_hash_cache.get(source) or sha256(source_path)
                if source in seen_sources:
                    raise SystemExit(f"source appears in multiple validation folds: {source}")
                seen_sources.add(source)
                order = str(item["order"])
                previous_fold = seen_orders.setdefault(order, fold)
                if previous_fold != fold:
                    raise SystemExit(f"work order {order} appears in validation folds {previous_fold} and {fold}")

                pred = model.predict(str(image_path), imgsz=args.imgsz,
                                     device=args.device, verbose=False)[0].probs
                names = model.names
                pred_slug = names[int(pred.top1)]
                confidence = float(pred.top1conf)
                top5 = [names[int(idx)] for idx in pred.top5]
                top5_detail = [
                    {"class": names[int(idx)], "confidence": round(float(pred.data[int(idx)]), 6)}
                    for idx in pred.top5
                ]
                correct = pred_slug == true_slug
                confident = confidence >= args.auto_conf
                fold_n += 1
                fold_hits += int(correct)
                fold_top5 += int(true_slug in top5)
                fold_confident += int(confident)
                fold_confident_hits += int(confident and correct)
                total_top5 += int(true_slug in top5)
                support[true_slug] += 1
                hits[true_slug] += int(correct)
                confusion[(true_slug, pred_slug)] += 1
                total_confident += int(confident)
                confident_hits += int(confident and correct)
                class_confident[true_slug] += int(confident)
                class_confident_hits[true_slug] += int(confident and correct)
                bin_idx = min(9, int(confidence * 10))
                conf_bins[bin_idx] += 1
                conf_bin_hits[bin_idx] += int(correct)
                conf_bin_sum[bin_idx] += confidence
                details.append({
                    "fold": fold,
                    "path": source,
                    "file": image_path.name,
                    "order": order,
                    "bucket": item.get("bucket"),
                    "true": true_slug,
                    "pred": pred_slug,
                    "confidence": round(confidence, 6),
                    "top5": top5_detail,
                    "correct": correct,
                })

        if used_keys != set(source_by_key):
            missing = sorted(set(source_by_key) - used_keys)
            raise SystemExit(f"split metadata contains validation files missing from fold{fold}: {missing[:5]}")

        folds_out.append({
            "fold": fold,
            "n": fold_n,
            "correct": fold_hits,
            "top1": fold_hits / fold_n if fold_n else None,
            "top5": fold_top5 / fold_n if fold_n else None,
            "auto_confidence_threshold": args.auto_conf,
            "auto_coverage": fold_confident / fold_n if fold_n else None,
            "auto_precision": fold_confident_hits / fold_confident if fold_confident else None,
        })

    n = len(details)
    classes = {}
    for label in sorted(support):
        pred_count = sum(v for (true, pred), v in confusion.items() if pred == label)
        tp = confusion[(label, label)]
        precision = tp / pred_count if pred_count else 0.0
        recall = hits[label] / support[label]
        classes[label] = {
            "support": support[label],
            "correct": hits[label],
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "auto_coverage": class_confident[label] / support[label],
            "auto_precision": class_confident_hits[label] / class_confident[label]
            if class_confident[label] else None,
        }
    ece = sum(
        conf_bins[i] / n * abs(conf_bin_hits[i] / conf_bins[i] - conf_bin_sum[i] / conf_bins[i])
        for i in range(10) if conf_bins[i]
    ) if n else None
    per_bucket = {}
    for bucket in sorted({str(row.get("bucket") or "unknown") for row in details}):
        rows = [row for row in details if str(row.get("bucket") or "unknown") == bucket]
        bn = len(rows)
        bucket_classes = {}
        for label in sorted({row["true"] for row in rows}):
            label_rows = [row for row in rows if row["true"] == label]
            label_pred = [row for row in rows if row["pred"] == label]
            tp = sum(row["correct"] for row in label_rows)
            precision = tp / len(label_pred) if label_pred else 0.0
            recall = tp / len(label_rows)
            bucket_classes[label] = {
                "support": len(label_rows),
                "correct": tp,
                "precision": precision,
                "recall": recall,
                "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            }
        per_bucket[bucket] = {
            "n": bn,
            "correct": sum(row["correct"] for row in rows),
            "top1": sum(row["correct"] for row in rows) / bn if bn else None,
            "per_class": bucket_classes,
        }
    source_manifest = [{"path": path, "sha256": digest}
                       for path, digest in sorted(source_image_hashes.items())]
    source_manifest_sha256 = hashlib.sha256(json.dumps(
        source_manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if (candidate_manifest is not None and
            candidate_manifest.get("source_image_manifest_sha256") != source_manifest_sha256):
        raise SystemExit("candidate source image manifest SHA-256 mismatch")
    dataset_manifest_entries.sort(key=lambda row: (row["fold"], row["split"], row["class"], row["file"]))
    dataset_manifest_sha256 = hashlib.sha256(json.dumps(
        dataset_manifest_entries, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    report = {
        "evaluation": "strict_grouped_out_of_fold",
        "created_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "split": str(split_path),
        "split_sha256": sha256(split_path),
        "candidate_manifest": str(candidate_manifest_path) if candidate_manifest_path else None,
        "candidate_manifest_sha256": sha256(candidate_manifest_path)
        if candidate_manifest_path else None,
        "source_image_count": len(source_manifest),
        "source_image_manifest_sha256": source_manifest_sha256,
        "source_dataset_byte_mismatch_count": len(source_dataset_byte_mismatch_paths),
        "dataset_image_count": len(dataset_manifest_entries),
        "dataset_image_manifest_sha256": dataset_manifest_sha256,
        "image_identity_note": "source 哈希绑定原始路径；dataset 哈希绑定模型实际读取的 train/val 字节。若处理目录含 JPEG 重编码文件，两类哈希允许不同，并分别记录。",
        "imgsz": args.imgsz,
        "device": args.device,
        "threshold": args.auto_conf,
        "weights": weights_used,
        "n": n,
        "correct": sum(row["correct"] for row in folds_out),
        "top1": sum(row["correct"] for row in folds_out) / n if n else None,
        "top5": total_top5 / n if n else None,
        "auto_coverage": total_confident / n if n else None,
        "auto_precision": confident_hits / total_confident if total_confident else None,
        "ece_10_bins_approx": ece,
        "folds": folds_out,
        "per_class": classes,
        "per_bucket": per_bucket,
        "confusions": [
            {"true": true, "pred": pred, "count": count}
            for (true, pred), count in confusion.most_common(50) if true != pred
        ],
        "predictions": details,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in ("predictions", "per_class", "confusions")}
    summary["per_class"] = classes
    summary["confusions"] = report["confusions"][:15]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
