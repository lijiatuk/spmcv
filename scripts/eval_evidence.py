#!/usr/bin/env python3
"""量一下：用检测器的「元件组合」判定油液场景，比整图分类好多少。

规格要求的是「存在桥 AND 存在油柱」这类组合证据，不是"这张图像哪一类"。
这里不改线上逻辑，只做离线对比，用数据决定要不要切。

    python scripts/eval_evidence.py --limit 0
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
OIL = ["bridge_drain", "bridge_fill", "tire_drain", "tire_fill", "filter_none", "filter_has"]

# 元件 → 语义
PART = {"axle_housing": "axle", "wheel_hub": "wheel",
        "filter_housing": "filter", "filter_element": "filter"}
DRAIN_SIGN = {"oil_stream", "oil_pan"}
FILL_SIGN = {"oil_gun", "oil_hose", "oil_drum"}


def evidence_of(boxes, min_score: float = 0.25) -> dict:
    best: dict[str, float] = {}
    for b in boxes:
        label = b["label"]
        if b["score"] >= min_score:
            best[label] = max(best.get(label, 0), b["score"])
    labels = set(best)
    part = None
    if "axle_housing" in labels:
        part = "axle"
    elif "filter_element" in labels or "filter_housing" in labels:
        part = "filter"
    elif "wheel_hub" in labels:
        part = "wheel"

    action = None
    if labels & DRAIN_SIGN:
        action = "drain"
    if labels & FILL_SIGN:
        # 同时出现时，取分数更高的那一侧
        fill_s = max(best.get(l, 0) for l in labels & FILL_SIGN)
        drain_s = max((best.get(l, 0) for l in labels & DRAIN_SIGN), default=0)
        action = "fill" if fill_s >= drain_s else action

    if part == "filter":
        return {"part": part, "action": "has" if "filter_element" in labels else "none",
                "labels": sorted(labels), "score": max(best.values(), default=0)}
    if part and action:
        return {"part": part, "action": action, "labels": sorted(labels),
                "score": max(best.values(), default=0)}
    return {"part": part, "action": action, "labels": sorted(labels),
            "score": max(best.values(), default=0)}


def to_class(ev: dict) -> str | None:
    p, a = ev.get("part"), ev.get("action")
    if p == "axle" and a == "drain": return "bridge_drain"
    if p == "axle" and a == "fill": return "bridge_fill"
    if p == "wheel" and a == "drain": return "tire_drain"
    if p == "wheel" and a == "fill": return "tire_fill"
    if p == "filter" and a == "has": return "filter_has"
    if p == "filter" and a == "none": return "filter_none"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/SPM系统AI识别训练照片_新增_20261111-20261113")
    ap.add_argument("--det", default="models/current/weights/det.pt")
    ap.add_argument("--cls", default="models/current/weights/scene.pt")
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--limit", type=int, default=0, help="每个类最多看几张，0=全部")
    ap.add_argument("--out", default="docs/reports/eval_evidence.json")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
    slug_of = {n: m["slug"] for n, m in cfg["class_map"].items()}
    det = YOLO(args.det)
    cls = YOLO(args.cls)

    stat = defaultdict(lambda: {"n": 0, "cls_ok": 0, "ev_ok": 0, "ev_none": 0, "both_ok": 0})
    root = ROOT / args.data
    for d in sorted(p for p in root.rglob("*") if p.is_dir()):
        slug = slug_of.get(d.name)
        if slug not in OIL:
            continue
        files = sorted(f for f in d.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if args.limit:
            files = files[:args.limit]
        for f in files:
            r = det.predict(str(f), imgsz=args.imgsz, verbose=False, conf=0.05)[0]
            boxes = [{"label": r.names[int(b.cls)], "score": float(b.conf),
                      "box": [float(v) for v in b.xyxy[0]]} for b in r.boxes]
            ev = evidence_of(boxes)
            ev_cls = to_class(ev)

            c = cls.predict(str(f), imgsz=args.imgsz, verbose=False)[0]
            cls_cls = c.names[int(c.probs.top1)]

            s = stat[slug]
            s["n"] += 1
            s["cls_ok"] += cls_cls == slug
            if ev_cls == slug:
                s["ev_ok"] += 1
            elif ev_cls is None:
                s["ev_none"] += 1
            if cls_cls == slug and ev_cls == slug:
                s["both_ok"] += 1

    rows = []
    tot = Counter()
    for k in OIL:
        s = stat[k]
        n = s["n"] or 1
        rows.append({"class": k, **s,
                     "cls_acc": round(s["cls_ok"] / n, 4),
                     "ev_acc": round(s["ev_ok"] / n, 4),
                     "ev_none_rate": round(s["ev_none"] / n, 4)})
        tot["n"] += s["n"]; tot["cls_ok"] += s["cls_ok"]; tot["ev_ok"] += s["ev_ok"]; tot["ev_none"] += s["ev_none"]
    summary = {
        "rows": rows,
        "overall": {"n": tot["n"],
                    "cls_acc": round(tot["cls_ok"] / max(tot["n"], 1), 4),
                    "ev_acc": round(tot["ev_ok"] / max(tot["n"], 1), 4),
                    "ev_none_rate": round(tot["ev_none"] / max(tot["n"], 1), 4)},
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'类别':<14}{'张数':>5}{'整图分类':>9}{'元件组合':>9}{'证据缺失':>9}")
    for r in rows:
        print(f"{r['class']:<14}{r['n']:>5}{r['cls_acc']:>9.1%}{r['ev_acc']:>9.1%}{r['ev_none_rate']:>9.1%}")
    o = summary["overall"]
    print(f"{'合计':<14}{o['n']:>5}{o['cls_acc']:>9.1%}{o['ev_acc']:>9.1%}{o['ev_none_rate']:>9.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
