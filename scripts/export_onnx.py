#!/usr/bin/env python3
"""Export the 7 classifier heads to ONNX for a torch-free inference image.

Why this matters beyond speed: the serving container currently installs
`ultralytics`, which is AGPL-3.0. Exports let the runtime depend only on
`onnxruntime` (MIT). NOTE: exporting does NOT change the licence status of the
weights themselves — see docs/reports/license_review.md.

Usage:
    python scripts/export_onnx.py --models models/v5 --version v5.0.0
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ultralytics import YOLO

# name -> (imgsz, task)
# imgsz 必须与训练时一致：导出尺寸和训练尺寸不同会让预处理改变，
# 实测 512 训练的模型按 640 导出，val 准确率从 95.3% 掉到 92.5%。
HEADS = {
    "scene": (512, "classify"),
    "group": (512, "classify"),
    "action": (512, "classify"),
    "bin_axle": (512, "classify"),
    "bin_tire": (512, "classify"),
    "bin_filter": (512, "classify"),
    "det": (640, "detect"),
}


def export_one(src: Path, out_dir: Path, name: str, imgsz: int, task: str) -> Path:
    model = YOLO(str(src))
    # simplify=True 会拉 onnxslim 做图优化，首次运行要联网下载；离线环境容易卡住。
    # 服务端只需要一个固定的静态图，先关掉简化，需要时再单独跑 onnxslim。
    kwargs = dict(format="onnx", imgsz=imgsz, half=False, dynamic=False,
                  simplify=False, opset=17)
    if task == "detect":
        kwargs.update(nms=True, conf=0.25, iou=0.45)
    path = model.export(**kwargs)
    exported = Path(path)
    dst = out_dir / f"{name}.onnx"
    shutil.move(str(exported), dst)
    print(f"  {name:<12} {src.name} -> {dst.name} ({dst.stat().st_size/1e6:.1f} MB)")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="models/v5")
    ap.add_argument("--version", default="v5.0.0")
    ap.add_argument("--only", default="", help="逗号分隔，只导出部分头")
    args = ap.parse_args()

    root = Path(args.models)
    out_dir = root / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    done = {}
    for name, (imgsz, task) in HEADS.items():
        if only and name not in only:
            continue
        src = root / f"{name}.pt"
        if not src.is_file():
            print(f"  skip {name}: {src} 不存在")
            continue
        done[name] = {"onnx": f"{name}.onnx", "imgsz": imgsz, "task": task}
        export_one(src, out_dir, name, imgsz, task)

    meta = {"version": args.version, "runtime": "onnxruntime", "heads": done}
    (out_dir / "export.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n导出完成：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
