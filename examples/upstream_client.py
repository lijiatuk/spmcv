#!/usr/bin/env python3
"""上层系统接入示例：只调一个 `/v1/recognize`。

    python examples/upstream_client.py --base http://10.51.13.7:8020 \
        --scene arrival_plate --photo plate.jpg

    python examples/upstream_client.py --base http://10.51.13.7:8020 \
        --scene license_vin --url https://oss.example.com/license.jpg

依赖只有 httpx。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

SCENES = ("arrival_plate", "license_vin", "frame_vin", "odometer",
          "bridge_drain", "bridge_fill", "tire_drain", "tire_fill",
          "filter_none", "filter_has", "service_front", "service_sign")


class SPMClient:
    """最小客户端：200 = 识别成功，422 = 识别不成功。"""

    def __init__(self, base: str, api_key: str = "", timeout: float = 15.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.headers = {"X-API-Key": api_key} if api_key else {}

    def recognize_file(self, scene: str, photo: Path) -> dict:
        with photo.open("rb") as fh:
            r = httpx.post(f"{self.base}/v1/recognize",
                           files={"image": (photo.name, fh, "image/png")},
                           data={"scene": scene},
                           headers=self.headers, timeout=self.timeout)
        return self._result(r)

    def recognize_url(self, scene: str, url: str) -> dict:
        r = httpx.post(f"{self.base}/v1/recognize",
                       data={"scene": scene, "image_url": url},
                       headers=self.headers, timeout=self.timeout)
        return self._result(r)

    @staticmethod
    def _result(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {"scene": "", "message": response.text[:200], "data": {}}
        body["_status"] = response.status_code
        return body

    def health(self) -> dict:
        return httpx.get(f"{self.base}/readyz", timeout=10).json()


def handle(result: dict) -> str:
    """上层真正要写的分支。"""
    if result.get("_status") == 200:
        return "识别成功"
    return f"识别不成功：{result.get('message')}（提示重拍）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.51.13.7:8020")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--scene", required=True, choices=SCENES)
    ap.add_argument("--photo", default="")
    ap.add_argument("--url", default="")
    args = ap.parse_args()

    if bool(args.photo) == bool(args.url):
        ap.error("--photo 与 --url 必须二选一")

    cli = SPMClient(args.base, args.api_key)
    ready = cli.health()
    print("服务:", "ok" if ready.get("ok") else ready,
          "| 模型:", ready.get("vision", {}).get("model_version"))

    if args.photo:
        result = cli.recognize_file(args.scene, Path(args.photo))
    else:
        result = cli.recognize_url(args.scene, args.url)

    print(f"\nHTTP {result['_status']} → {handle(result)}")
    print("data:", result.get("data"))
    return 0 if result["_status"] == 200 else 2


if __name__ == "__main__":
    sys.exit(main())