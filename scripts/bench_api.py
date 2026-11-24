#!/usr/bin/env python3
"""`/v1/recognize` 的有界压测：并发、时延、错误率、429 比例。

    python scripts/bench_api.py --base http://127.0.0.1:8020 \\
        --scene arrival_plate --image plate.jpg \\
        --vus 32 --requests 320 --max-p95-ms 3000

判定口径：
- 200 / 422 都算「业务正常返回」（422 是识别不成功，不是接口故障）；
- 429 = 准入拒绝；5xx / 超时 = 错误；
- `p95_ms` 是端到端墙钟，含上传与排队。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx

OK_STATUS = (200, 422)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[index]


async def run(args: argparse.Namespace) -> dict:
    images = [(p.name, p.read_bytes()) for p in args.image]
    api_key = os.environ.get("SPMCV_API_KEY") or os.environ.get("API_KEY")
    base = args.base.rstrip("/")
    headers = {"X-API-Key": api_key} if api_key else {}
    semaphore = asyncio.Semaphore(args.vus)
    results: list[dict] = []
    status_counts: Counter = Counter()

    limits = httpx.Limits(max_connections=args.vus, max_keepalive_connections=args.vus)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        meta = await client.get(f"{base}/v1/meta", headers=headers)
        if not meta.is_success:
            raise RuntimeError(f"preflight /v1/meta returned HTTP {meta.status_code}")

        async def one(index: int):
            name, data = images[index % len(images)]
            request_headers = {**headers,
                               "X-Request-Id": f"bench-{index}-{uuid.uuid4().hex[:8]}"}
            async with semaphore:
                start = time.perf_counter()
                try:
                    response = await client.post(
                        f"{base}{args.path}",
                        files={"image": (name, data, "application/octet-stream")},
                        data={"scene": args.scene},
                        headers=request_headers,
                    )
                    elapsed = (time.perf_counter() - start) * 1000
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    status_counts[response.status_code] += 1
                    results.append({
                        "status": response.status_code,
                        "latency_ms": round(elapsed, 1),
                        "message": payload.get("message"),
                        "data": payload.get("data"),
                        "request_id": response.headers.get("X-Request-Id"),
                    })
                except Exception as exc:  # noqa: BLE001
                    elapsed = (time.perf_counter() - start) * 1000
                    status_counts["error"] += 1
                    results.append({"status": 0, "latency_ms": round(elapsed, 1),
                                    "error": f"{type(exc).__name__}: {exc}"})

        wall_start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(args.requests)))
        wall_s = time.perf_counter() - wall_start

    latency = [r["latency_ms"] for r in results]
    business_ok = sum(1 for r in results if r["status"] == 200)
    business_fail = sum(1 for r in results if r["status"] == 422)
    busy = sum(1 for r in results if r["status"] == 429)
    errors = sum(1 for r in results if r["status"] not in OK_STATUS + (429,))
    p95 = percentile(latency, 0.95)
    p50 = percentile(latency, 0.50)

    verdict = "passed"
    if p95 is None or (args.max_p95_ms and p95 > args.max_p95_ms):
        verdict = "failed"
    if errors and args.max_error_rate is not None and errors / len(results) > args.max_error_rate:
        verdict = "failed"

    report = {
        "base": base,
        "path": args.path,
        "scene": args.scene,
        "vus": args.vus,
        "requests": args.requests,
        "wall_s": round(wall_s, 2),
        "rps": round(len(results) / wall_s, 2) if wall_s else None,
        "latency_ms": {
            "p50": p50,
            "p95": p95,
            "max": max(latency) if latency else None,
            "mean": round(statistics.mean(latency), 1) if latency else None,
        },
        "status_counts": dict(status_counts),
        "business_ok": business_ok,
        "business_fail": business_fail,
        "busy_429": busy,
        "errors": errors,
        "thresholds": {"max_p95_ms": args.max_p95_ms,
                       "max_error_rate": args.max_error_rate},
        "verdict": verdict,
        "results": results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8020")
    parser.add_argument("--path", default="/v1/recognize")
    parser.add_argument("--scene", required=True,
                        help="12 场景之一，压测请求会一直带这个 scene")
    parser.add_argument("--image", action="append", type=Path, required=True,
                        help="图片路径，可重复；请求按轮转取用")
    parser.add_argument("--vus", type=int, default=8)
    parser.add_argument("--requests", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=5000)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.vus < 1 or args.requests < 1:
        parser.error("--vus and --requests 必须为正数")
    missing = [str(p) for p in args.image if not p.is_file()]
    if missing:
        parser.error(f"图片不存在: {missing}")

    report = asyncio.run(run(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")

    lat = report["latency_ms"]
    print(f"vus={report['vus']} requests={report['requests']} wall={report['wall_s']}s "
          f"rps={report['rps']}")
    print(f"状态: {report['status_counts']}  p50={lat['p50']}ms p95={lat['p95']}ms "
          f"max={lat['max']}ms")
    print(f"业务: 成功 {report['business_ok']} / 识别不成功 {report['business_fail']}")
    print(f"判定: {report['verdict']}")
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())