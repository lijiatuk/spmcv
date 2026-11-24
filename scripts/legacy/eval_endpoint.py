#!/usr/bin/env python3
"""End-to-end evaluation of /v1/analyze against the labeled photo folders.

Usage:
    python scripts/eval_endpoint.py --per-class 6 --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/legacy"
API = "http://127.0.0.1:8017/v1/analyze"

FOLDERS = {
    "arrival_plate": "01_接车照片/01_车头及车牌号",
    "license_vin": "01_接车照片/02_行驶证VIN",
    "frame_vin": "01_接车照片/03_车架贴牌VIN",
    "odometer": "01_接车照片/04_里程表",
    "bridge_drain": "02_故障及修复照片/01_桥放油",
    "bridge_fill": "02_故障及修复照片/02_桥加油",
    "tire_drain": "02_故障及修复照片/03_轮胎放油",
    "tire_fill": "02_故障及修复照片/04_轮胎加油",
    "filter_none": "02_故障及修复照片/05_无滤芯",
    "filter_has": "02_故障及修复照片/06_有滤芯",
    "service_front": "03_项目检查表/01_服务单首页VIN",
    "service_sign": "03_项目检查表/02_服务单末页客户签字及服务站盖章",
}

FIELD = {"arrival_plate": "plate", "license_vin": "vin",
         "frame_vin": "vin", "odometer": "odometer", "service_front": "vin"}
ACTION = {"bridge_drain": "drain", "bridge_fill": "fill",
          "tire_drain": "drain", "tire_fill": "fill",
          "filter_none": "none", "filter_has": "has"}


async def one(client: httpx.AsyncClient, path: Path, sem: asyncio.Semaphore):
    async with sem:
        data = path.read_bytes()
        try:
            r = await client.post(API, files={"image": (path.name, data, "image/png")},
                                  timeout=180)
            r.raise_for_status()
            return {"ok": True, **r.json()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=6)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    jobs = []
    for slug, sub in FOLDERS.items():
        files = sorted(p for p in (RAW / sub).iterdir()
                       if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        rng.shuffle(files)
        for p in files[:args.per_class]:
            jobs.append((slug, p))

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[one(client, p, sem) for _, p in jobs])

    scene_ok = Counter()
    scene_total = Counter()
    field_ok = Counter()
    field_total = Counter()
    action_ok = Counter()
    action_total = Counter()
    auto_n = auto_ok = 0
    confusions = defaultdict(Counter)
    latencies = []
    for (expect, path), res in zip(jobs, results):
        scene_total[expect] += 1
        if not res.get("ok"):
            print(f"[ERR] {expect} {path.name}: {res.get('error')}")
            continue
        got = res.get("scene")
        latencies.append(res.get("latency_ms") or 0)
        if got in (expect, FEED_SCENE.get(expect)):
            scene_ok[expect] += 1
        else:
            confusions[expect][got] += 1
        field = FIELD.get(expect)
        if field:
            field_total[expect] += 1
            if (res.get("fields") or {}).get(field):
                field_ok[expect] += 1
        action = ACTION.get(expect)
        if action:
            action_total[expect] += 1
            if (res.get("conclusion") or {}).get("action") == action:
                action_ok[expect] += 1
        if not res.get("need_review"):
            auto_n += 1
            if got in (expect, FEED_SCENE.get(expect)):
                auto_ok += 1

    print("\n== scene accuracy ==")
    for slug in FOLDERS:
        n = scene_total[slug]
        if not n:
            continue
        print(f"{slug:15s} {scene_ok[slug]}/{n}  "
              f"{scene_ok[slug] / n:5.1%}  miss={dict(confusions[slug])}")

    print("\n== field extraction ==")
    for slug, n in field_total.items():
        print(f"{slug:15s} {field_ok[slug]}/{n}  {field_ok[slug] / n:5.1%}")

    if action_total:
        print("\n== action accuracy ==")
        for slug, n in action_total.items():
            print(f"{slug:15s} {action_ok[slug]}/{n}  {action_ok[slug] / n:5.1%}")

    print(f"\n== auto-pass ==\n{auto_n} requests without review "
          f"({auto_n / max(len(jobs), 1):.1%}), scene acc {auto_ok}/{auto_n}")

    if latencies:
        latencies.sort()
        p = lambda q: latencies[min(len(latencies) - 1, int(len(latencies) * q))]
        print(f"\nlatency p50={p(0.5):.0f}ms p95={p(0.95):.0f}ms "
              f"max={latencies[-1]:.0f}ms n={len(latencies)}")


# Scene names used by the API for actions that share a scene.
FEED_SCENE = {
    "bridge_drain": "axle_oil", "bridge_fill": "axle_oil",
    "tire_drain": "tire_oil", "tire_fill": "tire_oil",
    "filter_none": "filter", "filter_has": "filter",
}

if __name__ == "__main__":
    asyncio.run(main())
