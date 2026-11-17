#!/usr/bin/env python3
"""Auto-label detection boxes with YOLO-World or the existing VLX service.

Outputs YOLO-format labels + a dataset manifest, split by work order.

Usage:
    python scripts/auto_label.py --provider yoloworld --limit 50
    python scripts/auto_label.py --provider vlx --limit 10
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ONTOLOGY = [
    "vehicle_front", "license_plate", "driving_license", "vin_plate",
    "odometer_display", "service_sheet", "signature", "stamp",
    "filter_housing", "filter_element", "axle_housing", "wheel_hub",
    "drain_plug", "oil_stream", "oil_pan", "oil_gun", "oil_hose", "oil_drum",
]
CLS = {name: i for i, name in enumerate(ONTOLOGY)}

ZH_MAP = {
    "车头": "vehicle_front", "车牌": "license_plate", "行驶证": "driving_license",
    "车架号": "vin_plate", "VIN码": "vin_plate", "车辆识别代号": "vin_plate",
    "里程表": "odometer_display", "仪表盘": "odometer_display",
    "服务单": "service_sheet", "客户签字": "signature", "服务站盖章": "stamp",
    "滤芯座": "filter_housing", "滤芯": "filter_element",
    "桥壳": "axle_housing", "车轮": "wheel_hub", "放油螺栓": "drain_plug",
    "油柱": "oil_stream", "接油盆": "oil_pan", "加油枪": "oil_gun",
    "加油管": "oil_hose", "油桶": "oil_drum",
}

GROUP_CLASSES = {
    "arrival_plate": ["vehicle_front", "license_plate"],
    "license_vin": ["driving_license"],
    "frame_vin": ["vin_plate"],
    "odometer": ["odometer_display"],
    "bridge_drain": ["axle_housing", "oil_stream", "oil_pan", "drain_plug", "oil_gun", "oil_hose", "oil_drum"],
    "bridge_fill": ["axle_housing", "oil_gun", "oil_hose", "oil_drum"],
    "tire_drain": ["wheel_hub", "oil_stream", "oil_pan", "oil_gun", "oil_hose", "oil_drum"],
    "tire_fill": ["wheel_hub", "oil_gun", "oil_hose", "oil_drum"],
    "filter_has": ["filter_housing", "filter_element"],
    "filter_none": ["filter_housing"],
    "service_front": ["service_sheet"],
    "service_sign": ["service_sheet", "signature", "stamp"],
}


def parse_order(name: str) -> str:
    import re
    m = re.search(r"IKD[A-Za-z0-9]+", Path(name).stem)
    if m:
        return m.group(0)
    parts = Path(name).stem.split("_")
    return parts[1] if len(parts) > 1 else Path(name).stem


def yolo_line(cls_id: int, box, w: int, h: int) -> str:
    x1, y1, x2, y2 = [max(0.0, min(float(v), dim)) for v, dim in
                      zip(box, (w, h, w, h))]
    cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
    bw, bh = abs(x2 - x1) / w, abs(y2 - y1) / h
    return f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/processed/cls/train")
    ap.add_argument("--out", default="data/det")
    ap.add_argument("--provider", choices=["yoloworld", "vlx"], default="vlx")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per-class", type=int, default=0)
    ap.add_argument("--conf", type=float, default=0.15)
    ap.add_argument("--vlx", default="http://10.51.13.7:8013")
    args = ap.parse_args()

    src = Path(args.source)
    files = [p for p in sorted(src.rglob("*")) if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    if args.per_class:
        by_group = {}
        for f in files:
            by_group.setdefault(f.parent.name, []).append(f)
        files = []
        for group, group_files in sorted(by_group.items()):
            step = max(1, len(group_files) // args.per_class)
            files.extend(group_files[::step][: args.per_class])
    if args.limit:
        files = files[: args.limit]

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    if args.provider == "yoloworld":
        from ultralytics import YOLOWorld
        model = YOLOWorld("yolov8s-worldv2.pt")

    import httpx
    from PIL import Image

    manifest = []
    for i, f in enumerate(files, 1):
        group = f.parent.name
        classes = GROUP_CLASSES.get(group, [])
        if not classes:
            continue
        img = Image.open(f).convert("RGB")
        w, h = img.size
        boxes = []

        if args.provider == "vlx":
            zh = [k for k, v in ZH_MAP.items() if v in classes]
            r = httpx.post(f"{args.vlx}/v1/detect",
                           files={"image": (f.name, f.read_bytes(), "image/png")},
                           data={"text": "; ".join(zh), "lang": "zh"},
                           timeout=60)
            r.raise_for_status()
            for b in (r.json().get("result_bbox_list") or []):
                name = ZH_MAP.get(b.get("label"))
                if name in classes:
                    boxes.append((name, [b["xmin"], b["ymin"], b["xmax"], b["ymax"]]))
        else:
            model.set_classes(classes)
            res = model.predict(str(f), conf=args.conf, device="mps", verbose=False)[0]
            for b in res.boxes:
                boxes.append((res.names[int(b.cls)], b.xyxy[0].tolist()))

        dst_img = out / "images" / f"{group}__{f.name}"
        shutil.copy2(f, dst_img)
        label_path = out / "labels" / f"{dst_img.stem}.txt"
        label_path.write_text("\n".join(
            yolo_line(CLS[name], box, w, h) for name, box in boxes if name in CLS), encoding="utf-8")
        manifest.append({"image": str(dst_img), "group": group,
                         "order": parse_order(f.name), "boxes": len(boxes)})
        print(f"[{i}/{len(files)}] {group:16s} {f.name[:32]:32s} boxes={len(boxes)}", flush=True)

    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print("done", len(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
