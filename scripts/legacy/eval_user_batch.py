#!/usr/bin/env python3
"""用用户提供的原始照片目录跑一遍线上接口，逐张对答案。

用途：用户说「随便试了几张，签字盖章都没识别出来」时，别用个例下结论，
把两个素材目录里**所有**照片都过一遍 /v1/analyze，给出：
  - 场景分类准确率（按目录标签）
  - 每类字段读出率（车牌 / VIN / 里程 / 首页 VIN）
  - 末页签字、盖章检出率
  - P50/P95 时延、错误数

用法：
    python scripts/eval_user_batch.py --base http://10.51.13.7:8020
    python scripts/eval_user_batch.py --workers 2 --out docs/reports/user_batch_eval.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = [
    ROOT / "data/SPM系统AI识别训练照片",
    ROOT / "data/SPM系统AI识别训练照片_新增_20261111-20261113",
]


def load_slug_map() -> dict[str, str]:
    cfg = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
    return {name: meta["slug"] for name, meta in cfg["class_map"].items()}


def collect(dirs: list[Path]) -> list[dict]:
    slug_of = load_slug_map()
    items = []
    for root in dirs:
        if not root.is_dir():
            continue
        # 素材目录是三层：<批次>/<01_接车照片>/<01_车头及车牌号>/*.jpg
        for group in sorted(p for p in root.rglob("*") if p.is_dir()):
            want = slug_of.get(group.name)
            if not want:
                continue
            for img in sorted(list(group.glob("*.jpg")) + list(group.glob("*.png"))):
                items.append({"path": img, "folder": root.name,
                              "class": group.name, "want": want})
    return items


def pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
    return round(values[idx], 1)


def one(client: httpx.Client, item: dict, retries: int = 5) -> dict:
    data = item["path"].read_bytes()
    for attempt in range(retries):
        try:
            r = client.post("/v1/analyze",
                            files={"image": (item["path"].name, data, "image/jpeg")},
                            timeout=90)
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                return {**item, "error": f"{type(exc).__name__}: {exc}"}
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After") or 2)
            time.sleep(max(wait, 1.0) + random.random())
            continue
        if r.status_code >= 500:
            if attempt == retries - 1:
                return {**item, "error": f"HTTP {r.status_code}"}
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            body = r.json()
        except Exception:
            return {**item, "error": f"HTTP {r.status_code} 非 JSON"}
        body["_status"] = r.status_code
        return {**item, "resp": body}
    return {**item, "error": "429/5xx 重试耗尽"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.51.13.7:8020")
    ap.add_argument("--dirs", nargs="*", default=[str(p) for p in DEFAULT_DIRS])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default="docs/reports/user_batch_eval.json")
    args = ap.parse_args()

    items = collect([Path(p) for p in args.dirs])
    if not items:
        print("没有找到照片")
        return 1
    print(f"共 {len(items)} 张，目标 {args.base}，并发 {args.workers}")

    started = time.time()
    rows = []
    with httpx.Client(base_url=args.base) as client, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(lambda it: one(client, it), items), 1):
            rows.append(row)
            if i % 20 == 0 or i == len(items):
                print(f"  {i}/{len(items)}  {time.time() - started:.0f}s")

    latencies, errors = [], []
    scene_hit = Counter()
    scene_n = Counter()
    confusions = Counter()
    field_ok = Counter()
    field_n = Counter()
    sign_ok = Counter()
    sign_n = Counter()
    details = []

    for row in rows:
        if row.get("error"):
            errors.append({"file": str(row["path"]), "error": row["error"]})
            continue
        body = row["resp"]
        lat = body.get("latency_ms")
        if isinstance(lat, (int, float)):
            latencies.append(float(lat))
        want = row["want"]
        got = body.get("scene")
        scene_n[want] += 1
        if got == want:
            scene_hit[want] += 1
        else:
            confusions[(want, got)] += 1
        fields = body.get("fields") or {}
        if want in ("arrival_plate",):
            field_n[want] += 1
            field_ok[want] += bool(fields.get("plate"))
        elif want in ("license_vin", "frame_vin", "service_front"):
            field_n[want] += 1
            field_ok[want] += bool(fields.get("vin") or fields.get("vin_partial"))
        elif want == "odometer":
            field_n[want] += 1
            field_ok[want] += bool(fields.get("odometer"))
        elif want == "service_sign":
            reasons = body.get("review_reasons") or []
            sign_n["signature"] += 1
            sign_ok["signature"] += not any("签字" in r for r in reasons)
            sign_n["stamp"] += 1
            sign_ok["stamp"] += not any("盖章" in r for r in reasons)
        details.append({
            "file": str(Path(row["path"]).resolve().relative_to(ROOT)),
            "folder": row["folder"], "want": want, "got": got,
            "confidence": body.get("scene_confidence"),
            "fields": fields, "review_reasons": body.get("review_reasons"),
            "orientation": body.get("orientation"),
            "latency_ms": lat,
        })

    total = sum(scene_n.values())
    hit = sum(scene_hit.values())
    summary = {
        "base": args.base,
        "images": len(items),
        "evaluated": total,
        "errors": errors,
        "scene_accuracy": pct(hit, total),
        "scene_per_class": {k: {"hit": scene_hit[k], "n": scene_n[k],
                                "acc": pct(scene_hit[k], scene_n[k])}
                            for k in sorted(scene_n)},
        "confusions": [{"want": w, "got": g, "n": n}
                       for (w, g), n in confusions.most_common()],
        "field_read_rate": {k: {"ok": field_ok[k], "n": field_n[k],
                                "rate": pct(field_ok[k], field_n[k])}
                            for k in sorted(field_n)},
        "service_sign": {k: {"ok": sign_ok[k], "n": sign_n[k],
                             "rate": pct(sign_ok[k], sign_n[k])}
                         for k in sorted(sign_n)},
        "latency_ms": {"p50": percentile(latencies, 0.50),
                       "p95": percentile(latencies, 0.95),
                       "mean": round(statistics.mean(latencies), 1) if latencies else None},
        "elapsed_s": round(time.time() - started, 1),
        "details": details,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n场景准确率 {hit}/{total} = {summary['scene_accuracy']}%")
    for k, v in summary["scene_per_class"].items():
        print(f"  {k:16s} {v['hit']}/{v['n']}  {v['acc']}%")
    print("\n字段读出率")
    for k, v in summary["field_read_rate"].items():
        print(f"  {k:16s} {v['ok']}/{v['n']}  {v['rate']}%")
    print("\n末页签字/盖章")
    for k, v in summary["service_sign"].items():
        print(f"  {k:10s} {v['ok']}/{v['n']}  {v['rate']}%")
    print(f"\n时延 P50 {summary['latency_ms']['p50']}ms  P95 {summary['latency_ms']['p95']}ms"
          f"  错误 {len(errors)}")
    print(f"明细：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
