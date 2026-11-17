#!/usr/bin/env python3
"""上层系统接入示例（可直接抄进你们的服务）。

覆盖三种真实用法：
  1. 单张实时判定（拍照即传）
  2. 整单批量（拍完一起传）
  3. 拿机器可读码做分支

    python examples/upstream_client.py --base http://127.0.0.1:8017 \
        --photo data/xxx.png --category arrival

依赖只有 httpx（或 requests）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


class SPMClient:
    """最小客户端。所有错误都归一成 (code, message)，调用方不用解析 HTTP 状态。"""

    def __init__(self, base: str, api_key: str = "", timeout: float = 120.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.headers = {"X-API-Key": api_key} if api_key else {}

    def _post(self, path: str, files: list[tuple[str, tuple[str, bytes, str]]],
              data: dict | None = None) -> dict:
        r = httpx.post(f"{self.base}{path}", files=files, data=data or {},
                       headers=self.headers, timeout=self.timeout)
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = {"code": f"HTTP_{r.status_code}", "message": r.text[:200]}
            raise RuntimeError(f"{body.get('code')}: {body.get('message')}")
        return r.json()

    def scan(self, photos: list[Path], category: str = "",
             expected_plate: str = "", expected_odometer: str = "") -> dict:
        files = [("images", (p.name, p.read_bytes(), "image/png")) for p in photos]
        data = {"category": category}
        if expected_plate:
            data["expected_plate"] = expected_plate
        if expected_odometer:
            data["expected_odometer"] = expected_odometer
        return self._post("/v1/scan", files, data)

    def health(self) -> dict:
        return httpx.get(f"{self.base}/readyz", timeout=10).json()


def handle(result: dict) -> str:
    """把结果翻译成业务动作——上层真正要写的就是这一段。"""
    if result.get("degraded"):
        return "转人工：分析超时，结果不完整"
    decision = result.get("decision")
    codes = result.get("codes") or []
    if decision == "pass":
        return "自动通过"
    if "VIN_MISMATCH" in codes or "SERVICE_VIN_MISMATCH" in codes:
        return "人工必审：VIN 与工单不一致"
    if "MISSING_PLATE" in codes:
        return "继续：车牌未识别，按规格跳过校验"
    return "转人工：" + (codes[0] if codes else "未分类原因")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8017")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--photo", action="append", required=True)
    ap.add_argument("--category", default="", choices=["", "arrival", "repair", "inspection"])
    ap.add_argument("--expected-plate", default="")
    ap.add_argument("--expected-odometer", default="")
    args = ap.parse_args()

    cli = SPMClient(args.base, args.api_key)
    ready = cli.health()
    print("服务:", "ok" if ready.get("ok") else ready,
          "| 模型:", ready.get("vision", {}).get("model_version"))

    photos = [Path(p) for p in args.photo]
    result = cli.scan(photos, args.category, args.expected_plate, args.expected_odometer)
    print(f"\n{len(photos)} 张 → decision={result.get('decision')}  codes={result.get('codes')}")
    for p in result.get("photos") or []:
        print(f"  [{p.get('index')}] {p.get('scene'):<14} conf={p.get('confidence')} {p.get('filename')}")
    print("\n业务动作:", handle(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
