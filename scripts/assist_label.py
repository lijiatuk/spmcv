#!/usr/bin/env python3
"""Model-assisted label check for the unconfirmed buckets.

产品给的 160 张「待分类_」、22 张跨类子桶、33 张语义待确认图，都需要人判断。
这里先用上一版场景模型打一遍分，把「模型与目录一致 / 不一致」分开，
人工只需要看不一致的那部分 —— 和 VLX 辅助标注是同一套流程，只是换了个标注器。

两种后端：
    --backend yolo   本地 ultralytics 权重（默认 var/runs/cls_v1/weights/best.pt）
    --backend vlx    VLX HTTP 服务（内网 10.51.13.7:8013，需能连通）

输出 CSV：path, folder_class, predicted_class, confidence, agree, suggestion
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

NUMERIC_PREFIX = re.compile(r"^\d+_")


def load_class_map(path: Path) -> dict[str, str]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return {name: meta["slug"] for name, meta in cfg["class_map"].items()}


def short(name: str) -> str:
    return NUMERIC_PREFIX.sub("", name)


def infer_folder_class(rel: str, canon: dict[str, str]) -> str | None:
    """目录树隐含的类别：优先取能对上的子桶名，其次取类目录。"""
    parts = Path(rel).parts
    if len(parts) >= 4:
        alias = {short(n): n for n in canon}
        if parts[2] in canon:
            return parts[2]
        if parts[2] in alias:
            return alias[parts[2]]
    if len(parts) >= 3 and parts[1] in canon:
        return parts[1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/processed/v3/manifest.csv")
    ap.add_argument("--buckets", default="pending,cross,review",
                    help="逗号分隔，只跑这些桶")
    ap.add_argument("--weights", default="var/runs/cls_v1/weights/best.pt")
    ap.add_argument("--backend", default="yolo", choices=["yolo", "vlx"])
    ap.add_argument("--vlx-url", default="http://10.51.13.7:8013")
    ap.add_argument("--source", default="data/raw/recut")
    ap.add_argument("--out", default="data/processed/v3/assist_labels.csv")
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--agree-conf", type=float, default=0.55,
                    help="模型与目录一致且置信度高于此值 → 建议直接采纳")
    args = ap.parse_args()

    canon = load_class_map(Path("app/scenes.json"))
    name_by_slug = {v: k for k, v in canon.items()}
    wanted = {b.strip() for b in args.buckets.split(",") if b.strip()}

    rows = []
    with Path(args.manifest).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["bucket"] in wanted:
                rows.append(row)
    print(f"待标注 {len(rows)} 张（桶：{sorted(wanted)}）")

    src = Path(args.source)
    if args.backend == "yolo":
        from ultralytics import YOLO
        model = YOLO(args.weights)
        predictor = lambda p: _yolo_predict(model, p, args.imgsz, args.device)
    else:
        predictor = lambda p: _vlx_predict(args.vlx_url, p)

    out_rows = []
    for i, row in enumerate(rows, 1):
        folder_cls = infer_folder_class(row["path"], canon)
        pred_cls, conf = predictor(src / row["path"])
        agree = bool(folder_cls and pred_cls and canon.get(folder_cls) == pred_cls)
        if agree and conf >= args.agree_conf:
            suggestion = "采纳：与目录一致"
        elif agree:
            suggestion = "存疑：一致但置信度低"
        elif folder_cls is None:
            suggestion = "人工：目录无类别"
        else:
            suggestion = "复核：与目录不一致"
        out_rows.append({
            "path": row["path"], "bucket": row["bucket"],
            "folder_class": folder_cls or "",
            "folder_slug": canon.get(folder_cls or "", ""),
            "predicted_class": name_by_slug.get(pred_cls, ""), "predicted_slug": pred_cls or "",
            "confidence": round(conf, 4), "agree": agree, "suggestion": suggestion,
        })
        if i % 25 == 0:
            print(f"  ... {i}/{len(rows)}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    from collections import Counter
    print("\n结果分布：", dict(Counter(r["suggestion"] for r in out_rows)))
    print("输出：", out.resolve())
    return 0


def _yolo_predict(model, path: Path, imgsz: int, device: str):
    res = model.predict(str(path), imgsz=imgsz, device=device, verbose=False)[0]
    probs = res.probs
    slug = res.names[int(probs.top1)]
    return slug, float(probs.top1conf)


def _vlx_predict(base_url: str, path: Path):
    import httpx
    with path.open("rb") as fh:
        resp = httpx.post(f"{base_url}/v1/scene",
                          files={"image": (path.name, fh, "image/jpeg")},
                          timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("class"), float(data.get("confidence") or 0.0)


if __name__ == "__main__":
    raise SystemExit(main())
