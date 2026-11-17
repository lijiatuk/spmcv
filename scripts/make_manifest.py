#!/usr/bin/env python3
"""Freeze a set of weights into a versioned model directory + manifest.json.

The serving stack must never guess which checkpoint is production: it reads
`model_version` and verifies every file's sha256 at startup, then refuses to
serve if anything is missing or changed.

Usage:
    python scripts/make_manifest.py --dir models/v5 --version v5.0.0 \
        --metrics metrics.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

# 服务端实际加载的 7 个头：文件名 -> 用途
ROLES = {
    "scene": "12 类场景分类",
    "group": "3 类油液分组",
    "action": "6 类动作",
    "bin_axle": "桥 加油/放油 二分类",
    "bin_tire": "轮胎 加油/放油 二分类",
    "bin_filter": "滤芯 有/无 二分类",
    "det": "18 类检测器",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="models/v5")
    ap.add_argument("--version", default="v5.0.0")
    ap.add_argument("--metrics", default="", help="可选，metrics JSON 文件")
    ap.add_argument("--extra-dir", default="",
                    help="额外扫描的子目录（如 v5.0.0 放 ONNX）")
    ap.add_argument("--copy", nargs="*", default=[],
                    help="name=source.pt，例如 scene=var/runs/v5_scene_26n/weights/best.pt")
    args = ap.parse_args()

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    by_role = {}
    for spec in args.copy:
        role, _, src = spec.partition("=")
        if not src:
            raise SystemExit(f"--copy 需要 name=path 形式，收到: {spec}")
        shutil.copy2(src, out / f"{role}.pt")
        by_role[role] = src

    files = {}
    scan = [out] + ([out / args.extra_dir] if args.extra_dir else [])
    for directory in scan:
        if not directory.is_dir():
            continue
        for p in sorted(directory.iterdir()):
            if p.is_file() and p.suffix in (".pt", ".onnx"):
                key = p.name if directory == out else f"{args.extra_dir}/{p.name}"
                files[key] = {"sha256": sha256(p), "bytes": p.stat().st_size}

    metrics = {}
    if args.metrics and Path(args.metrics).is_file():
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))

    manifest = {
        "model_version": args.version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roles": {k: v for k, v in ROLES.items() if f"{k}.pt" in files or f"{k}.onnx" in files},
        "source_checkpoints": by_role,
        "files": files,
        "metrics": metrics,
        "labels": {"scene": "app/scenes.json"},
        "notes": "sha256 由 make_manifest.py 生成；服务启动时校验，不一致即拒绝启动",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
