#!/usr/bin/env python3
"""把自动标注结果画成一张联系表，方便人工快速过一遍。

每张缩略图上画框：签字=红、盖章=蓝、服务单=橙；标题带文件名和框数。
人工只需要看这张图，就能发现"漏框/错框"的样本，再回 labels/ 里改。

用法：
    python scripts/render_label_sheet.py \
        --images var/det_sign_auto/images --labels var/det_sign_auto/labels \
        --out var/det_sign_auto/contact_sheet.jpg
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAMES = ["vehicle_front", "license_plate", "driving_license", "vin_plate",
         "odometer_display", "service_sheet", "signature", "stamp",
         "filter_housing", "filter_element", "axle_housing", "wheel_hub",
         "drain_plug", "oil_stream", "oil_pan", "oil_gun", "oil_hose", "oil_drum"]
SHORT = {"signature": "签字", "stamp": "盖章", "service_sheet": "服务单"}
COLORS = {"signature": (220, 40, 40), "stamp": (30, 90, 220),
          "service_sheet": (240, 150, 30)}


def load_font(size: int):
    for path in ("/System/Library/Fonts/PingFang.ttc",
                 "/System/Library/Fonts/Supplemental/Songti.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="var/det_sign_auto/images")
    ap.add_argument("--labels", default="var/det_sign_auto/labels")
    ap.add_argument("--out", default="var/det_sign_auto/contact_sheet.jpg")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--width", type=int, default=420)
    ap.add_argument("--report", default="", help="同时输出逐图汇总 JSON")
    ap.add_argument("--per-sheet", type=int, default=0,
                    help="每张联系表最多放多少图（0=一张全放）")
    args = ap.parse_args()

    images = sorted(p for p in Path(args.images).iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    font = load_font(18)
    rows = []
    tiles = []
    for img_path in images:
        label_path = Path(args.labels) / f"{img_path.stem}.txt"
        boxes = []
        if label_path.is_file():
            for line in label_path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                cls = int(parts[0])
                cx, cy, bw, bh = map(float, parts[1:5])
                boxes.append((NAMES[cls], cx, cy, bw, bh))
        img = Image.open(img_path).convert("RGB")
        w, h = img.size
        scale = args.width / w
        thumb = img.resize((args.width, int(h * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(thumb)
        for name, cx, cy, bw, bh in boxes:
            x1, y1 = (cx - bw / 2) * args.width, (cy - bh / 2) * thumb.height
            x2, y2 = (cx + bw / 2) * args.width, (cy + bh / 2) * thumb.height
            color = COLORS.get(name, (120, 120, 120))
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
            if name in SHORT:
                draw.text((x1 + 2, max(0, y1 - 20)), SHORT[name], fill=color, font=font)
        labels = [b[0] for b in boxes]
        rows.append({"image": img_path.name,
                     "boxes": len(boxes),
                     "signature": "signature" in labels,
                     "stamp": "stamp" in labels,
                     "service_sheet": "service_sheet" in labels})
        tiles.append((thumb, img_path.name, labels))

    tile_w = args.width
    tile_h = max(t[0].height for t in tiles) + 30
    cols = args.cols
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    chunk = args.per_sheet or len(tiles)
    for page, start in enumerate(range(0, len(tiles), chunk), 1):
        group = tiles[start:start + chunk]
        rows_n = (len(group) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tile_w, rows_n * tile_h), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (thumb, name, labels) in enumerate(group):
            x, y = (i % cols) * tile_w, (i // cols) * tile_h
            sheet.paste(thumb, (x, y + 26))
            mark = "签字✓" if "signature" in labels else "缺签字"
            color = (0, 140, 0) if "signature" in labels else (200, 0, 0)
            draw.text((x + 4, y + 3), f"{name[:34]}  {mark}", fill=color, font=font)
        target = out.with_name(f"{out.stem}-{page}{out.suffix}") if args.per_sheet else out
        sheet.save(target, quality=88)
        print(f"联系表：{target}")
    if args.report:
        Path(args.report).write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    miss_sig = [r["image"] for r in rows if not r["signature"]]
    miss_stamp = [r["image"] for r in rows if not r["stamp"]]
    print(f"共 {len(rows)} 张，联系表：{out}")
    print(f"缺签字 {len(miss_sig)} 张：{miss_sig}")
    print(f"缺盖章 {len(miss_stamp)} 张：{miss_stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
