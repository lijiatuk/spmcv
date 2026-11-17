#!/usr/bin/env python3
"""Bounded, repeatable multipart benchmark for the upstream image API."""
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


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[index]


async def run(args: argparse.Namespace) -> dict:
    images = [(path.name, path.read_bytes()) for path in args.image]
    headers = {"X-Request-Id": "spmcv-bench-" + uuid.uuid4().hex}
    api_key = os.environ.get("SPMCV_API_KEY") or os.environ.get("API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    semaphore = asyncio.Semaphore(args.vus)
    results = []
    contract = {}

    async with httpx.AsyncClient(timeout=args.timeout, limits=httpx.Limits(
        max_connections=args.vus, max_keepalive_connections=args.vus
    )) as client:
        meta_response = await client.get(args.base.rstrip("/") + "/v1/meta", headers=headers)
        if not meta_response.is_success:
            raise RuntimeError(f"preflight /v1/meta returned HTTP {meta_response.status_code}")
        contract = meta_response.json()

        async def one(index: int):
            files = [("images", (name, data, "application/octet-stream"))
                     for name, data in images]
            data = {"category": args.category} if args.category else {}
            request_headers = {**headers, "X-Request-Id": f"spmcv-bench-{index}-{uuid.uuid4().hex[:8]}"}
            async with semaphore:
                start = time.perf_counter()
                try:
                    response = await client.post(
                        args.base.rstrip("/") + args.path,
                        files=files,
                        data=data,
                        headers=request_headers,
                    )
                    elapsed = (time.perf_counter() - start) * 1000
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    photos = payload.get("photos", [])
                    results.append({
                        "status": response.status_code,
                        "latency_ms": elapsed,
                        "business_decision": payload.get("decision"),
                        "workflow_ok": payload.get("ok"),
                        "need_review": payload.get("need_review"),
                        "photo_latency_ms": [photo.get("latency_ms") for photo in photos
                                             if isinstance(photo, dict)
                                             and photo.get("latency_ms") is not None],
                        "photo_scenes": [photo.get("scene") for photo in photos
                                         if isinstance(photo, dict)],
                        "photo_field_presence": [
                            {key: bool(value) for key, value in
                             (photo.get("field_summary") or photo.get("fields") or {}).items()
                             if key in ("plate", "vin", "odometer")}
                            for photo in photos if isinstance(photo, dict)
                        ],
                        "photo_ocr_error_codes": [photo.get("ocr_error_code")
                                                   for photo in photos
                                                   if isinstance(photo, dict)],
                        "photo_review_reasons": [photo.get("review_reasons", [])
                                                 for photo in photos
                                                 if isinstance(photo, dict)],
                        "model_versions": sorted({
                            str(photo.get("model_version")) for photo in photos
                            if photo.get("model_version")
                        }),
                        "request_id": response.headers.get("X-Request-Id"),
                        "error_code": payload.get("code"),
                    })
                except httpx.HTTPError as exc:  # benchmark captures transport failures as outcomes
                    elapsed = (time.perf_counter() - start) * 1000
                    results.append({"status": 0, "latency_ms": elapsed,
                                    "error_code": type(exc).__name__})

        wall_start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(args.requests)))
        wall_elapsed_s = max(time.perf_counter() - wall_start, 0.001)

    latency = [row["latency_ms"] for row in results]
    photo_latency = [value for row in results for value in row.get("photo_latency_ms", [])]
    succeeded = sum(200 <= row["status"] < 300 for row in results)
    error_rate = (len(results) - succeeded) / len(results) if results else None
    p95 = percentile(latency, 0.95)
    business_decisions = Counter(
        row["business_decision"] for row in results if row.get("business_decision")
    )
    workflow_outcomes = Counter(str(row["workflow_ok"]).lower() for row in results
                                if row.get("workflow_ok") is not None)
    checks = []
    if args.max_error_rate is not None:
        checks.append(error_rate is not None and error_rate <= args.max_error_rate)
    if args.max_p95_ms is not None:
        checks.append(p95 is not None and p95 <= args.max_p95_ms)
    return {
        "target": args.base.rstrip("/") + args.path,
        "runner": "local",
        "api_contract": contract,
        "model_versions": sorted({version for row in results
                                   for version in row.get("model_versions", [])}),
        "business_decision_counts": dict(business_decisions),
        "workflow_ok_counts": dict(workflow_outcomes),
        "request_count": len(results),
        "virtual_users": args.vus,
        "photo_count_per_request": len(images),
        "category": args.category or None,
        "timeout_s": args.timeout,
        "success_count": succeeded,
        "error_count": len(results) - succeeded,
        "error_rate": error_rate,
        "elapsed_s": wall_elapsed_s,
        "rps": len(results) / wall_elapsed_s,
        "threshold_verdict": "not_set" if not checks else "passed" if all(checks) else "failed",
        "latency_ms": {
            "min": min(latency) if latency else None,
            "mean": statistics.mean(latency) if latency else None,
            "p50": percentile(latency, 0.50),
            "p95": p95,
            "p99": percentile(latency, 0.99),
            "max": max(latency) if latency else None,
        },
        "photo_latency_ms": {
            "count": len(photo_latency),
            "p50": percentile(photo_latency, 0.50),
            "p95": percentile(photo_latency, 0.95),
            "max": max(photo_latency) if photo_latency else None,
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8017")
    parser.add_argument("--path", default="/v1/scan")
    parser.add_argument("--image", action="append", type=Path, required=True,
                        help="one image path; repeat to upload a multi-photo request")
    parser.add_argument("--category", choices=("", "arrival", "repair", "inspection"), default="")
    parser.add_argument("--vus", type=int, default=2)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-error-rate", type=float, default=None)
    parser.add_argument("--max-p95-ms", type=float, default=None)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.vus < 1 or args.requests < 1:
        parser.error("--vus and --requests must be positive")
    if args.max_error_rate is not None and not 0 <= args.max_error_rate <= 1:
        parser.error("--max-error-rate must be between 0 and 1")
    if args.max_p95_ms is not None and args.max_p95_ms <= 0:
        parser.error("--max-p95-ms must be positive")
    missing = [str(path) for path in args.image if not path.is_file()]
    if missing:
        parser.error(f"image file(s) do not exist: {missing}")
    report = asyncio.run(run(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["error_count"] == 0 and report["threshold_verdict"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
