#!/usr/bin/env python3
"""用一张图 / 一批图逐场景调 `/v1/recognize`，统计每个场景的通过率与时延。

用法：

    # 单张
    python scripts/eval_recognize.py --base http://127.0.0.1:8020 \
        --scene arrival_plate --image data/external/spec/01_车头及车牌号/spec_image1.png

    # 12 场景 × 规格样例（目录名 → 场景）
    python scripts/eval_recognize.py --base http://127.0.0.1:8020 --spec-dir data/external/spec

    # 并发压测式评测
    python scripts/eval_recognize.py --base http://127.0.0.1:8020 --spec-dir data/external/spec --vus 8
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

# 规格目录 → 12 场景
SPEC_MAP = {
    "01_车头及车牌号": "arrival_plate",
    "02_行驶证VIN": "license_vin",
    "03_车架贴牌VIN": "frame_vin",
    "04_里程表": "odometer",
    "01_桥放油": "bridge_drain",
    "02_桥加油": "bridge_fill",
    "03_轮胎放油": "tire_drain",
    "04_轮胎加油": "tire_fill",
    "05_无滤芯": "filter_none",
    "06_有滤芯": "filter_has",
    "01_服务单首页VIN": "service_front",
    "02_服务单末页客户签字及服务站盖章": "service_sign",
}


def call(client: httpx.Client, base: str, path: Path, scene: str) -> dict:
    t0 = time.time()
    with path.open("rb") as fh:
        r = client.post(f"{base}/v1/recognize",
                        files={"image": (path.name, fh, "image/png")},
                        data={"scene": scene}, timeout=30)
    elapsed = round((time.time() - t0) * 1000)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"message": r.text[:120], "data": {}}
    return {"scene": scene, "file": str(path), "status": r.status_code,
            "ok": r.status_code == 200, "message": body.get("message"),
            "data": body.get("data"), "wall_ms": elapsed}


def collect(spec_dir: Path) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for folder, scene in SPEC_MAP.items():
        for path in sorted((spec_dir / folder).glob("*.png")):
            items.append((path, scene))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8020")
    ap.add_argument("--scene", default="", help="单张模式：12 场景之一")
    ap.add_argument("--image", action="append", default=[], help="单张模式：图片路径")
    ap.add_argument("--spec-dir", default="", help="12 场景样例目录")
    ap.add_argument("--vus", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.spec_dir:
        items = collect(Path(args.spec_dir))
    elif args.scene and args.image:
        items = [(Path(p), args.scene) for p in args.image]
    else:
        ap.error("需要 --spec-dir，或同时给 --scene 与 --image")
    if not items:
        print("没有找到图片")
        return 1

    with httpx.Client() as client:
        if args.vus <= 1:
            rows = [call(client, args.base, path, scene) for path, scene in items]
        else:
            with ThreadPoolExecutor(max_workers=args.vus) as pool:
                rows = list(pool.map(lambda it: call(client, args.base, *it), items))

    for row in rows:
        flag = "OK  " if row["ok"] else "FAIL"
        print(f"{flag} {row['scene']:14s} {row['status']} {row['wall_ms']:5d}ms "
              f"{json.dumps(row['data'], ensure_ascii=False)} {'' if row['ok'] else row['message']}")

    lat = [r["wall_ms"] for r in rows]
    passed = sum(1 for r in rows if r["ok"])
    summary = {
        "base": args.base,
        "vus": args.vus,
        "total": len(rows),
        "passed": passed,
        "pass_rate": round(100 * passed / len(rows), 1),
        "latency_ms": {
            "p50": round(statistics.median(lat), 1),
            "max": max(lat),
            "mean": round(statistics.mean(lat), 1),
        },
        "rows": rows,
    }
    print(f"\n{passed}/{len(rows)} = {summary['pass_rate']}%  "
          f"P50 {summary['latency_ms']['p50']}ms  Max {max(lat)}ms")
    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"明细：{args.out}")
    return 0 if passed == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())