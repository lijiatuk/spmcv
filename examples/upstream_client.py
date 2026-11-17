#!/usr/bin/env python3
"""上层系统接入示例：只调一个 `/v1/check`，只看 HTTP 200 / 422。

    python examples/upstream_client.py --base http://10.51.13.7:8020 \
        --scene arrival \
        --photo plate.jpg --photo license.jpg --photo frame.jpg --photo odometer.jpg \
        --expected-plate 晋D08906D --expected-vin HLC3J2VF9TA604594 --expected-odometer 182

依赖只有 httpx（或 requests）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


class SPMClient:
    """最小客户端：200 即通过，422 即不通过。"""

    def __init__(self, base: str, api_key: str = "", timeout: float = 120.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.headers = {"X-API-Key": api_key} if api_key else {}

    def check(self, photos: list[Path], scene: str, *,
              axle_type: str = "", expected_plate: str = "",
              expected_vin: str = "", expected_odometer: str = "") -> dict:
        files = [("images", (p.name, p.read_bytes(), "image/png")) for p in photos]
        data = {"scene": scene}
        if axle_type:
            data["axle_type"] = axle_type
        if expected_plate:
            data["expected_plate"] = expected_plate
        if expected_vin:
            data["expected_vin"] = expected_vin
        if expected_odometer:
            data["expected_odometer"] = expected_odometer
        r = httpx.post(f"{self.base}/v1/check", files=files, data=data,
                       headers=self.headers, timeout=self.timeout)
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {"ok": False, "message": r.text[:200]}
        body["_http"] = r.status_code
        return body

    def health(self) -> dict:
        return httpx.get(f"{self.base}/readyz", timeout=10).json()


def handle(result: dict) -> str:
    """上层真正要写的分支：只有这一句。"""
    return "通过" if result.get("_http") == 200 else f"不通过：{result.get('message')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.51.13.7:8020")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--scene", required=True,
                    choices=["arrival", "repair", "inspection"])
    ap.add_argument("--photo", action="append", required=True)
    ap.add_argument("--axle-type", default="")
    ap.add_argument("--expected-plate", default="")
    ap.add_argument("--expected-vin", default="")
    ap.add_argument("--expected-odometer", default="")
    args = ap.parse_args()

    cli = SPMClient(args.base, args.api_key)
    ready = cli.health()
    print("服务:", "ok" if ready.get("ok") else ready,
          "| 模型:", ready.get("vision", {}).get("model_version"))

    result = cli.check([Path(p) for p in args.photo], args.scene,
                       axle_type=args.axle_type, expected_plate=args.expected_plate,
                       expected_vin=args.expected_vin,
                       expected_odometer=args.expected_odometer)
    print(f"\nHTTP {result.get('_http')} → {handle(result)}")
    if result.get("missing"):
        print("缺少:", "、".join(result["missing"]))
    print("识别结果:", result.get("data"))
    return 0 if result.get("_http") == 200 else 2


if __name__ == "__main__":
    sys.exit(main())
