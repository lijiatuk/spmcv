#!/usr/bin/env python3
"""对运行中的服务做**对外契约**检查（上层接入/上线前必跑）。

当前契约（v3.0）：只有一个业务接口 `POST /v1/recognize`，单张图 + `scene`。

    python scripts/contract_check.py --base http://127.0.0.1:8020 \\
        --scene arrival_plate --photo data/external/spec/01_车头及车牌号/spec_image1.png

退出码非 0 表示契约不符。
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import httpx

PASS, FAIL = "✅", "❌"
failures: list[str] = []
RETIRED = ("/v1/scan", "/v1/arrival", "/v1/repair", "/v1/inspection",
           "/v1/workorder", "/v1/check", "/v1/check/arrival")


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {PASS if cond else FAIL} {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8020")
    ap.add_argument("--scene", default="arrival_plate")
    ap.add_argument("--photo", required=True)
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    photo = Path(args.photo)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    client = httpx.Client(base_url=base, headers=headers, timeout=60)
    rid = uuid.uuid4().hex

    print(f"契约检查 → {base}\n")

    print("[基础]")
    h = client.get("/healthz")
    check("/healthz 200", h.status_code == 200)
    r = client.get("/readyz")
    body = r.json() or {}
    check("/readyz 有 vision/ocr", {"vision", "ocr"} <= set(body))
    check("vision.model_version 非空", bool((body.get("vision") or {}).get("model_version")))

    print("\n[契约声明]")
    meta = client.get("/v1/meta").json() or {}
    contract = meta.get("contract") or {}
    check("api_version 存在", bool(contract.get("api_version")))
    check("单张契约 single_image", contract.get("single_image") is True)
    check("场景数 12", contract.get("scene_count") == 12)
    check("响应字段 scene/message/data",
          contract.get("response_fields") == ["scene", "message", "data"])

    print("\n[接口枚举]")
    scenes = client.get("/v1/scenes").json()
    names = {row["scene"] for row in scenes.get("scenes", [])}
    check("12 个 scene", len(names) == 12, str(len(names)))
    check("包含 arrival_plate 与 service_sign",
          {"arrival_plate", "service_sign"} <= names)

    print("\n[正常调用]")
    r = client.post("/v1/recognize",
                    files={"image": (photo.name, photo.read_bytes(), "image/png")},
                    data={"scene": args.scene},
                    headers={"X-Request-Id": rid})
    check("HTTP 200 或 422", r.status_code in (200, 422), str(r.status_code))
    check("回传 X-Request-Id", r.headers.get("x-request-id") == rid,
          r.headers.get("x-request-id", ""))
    d = r.json() or {}
    check("响应只有 scene/message/data", set(d) == {"scene", "message", "data"}, str(set(d)))
    check("scene 回显", d.get("scene") == args.scene, str(d.get("scene")))
    check("message 非空", bool(d.get("message")))
    check("data 是对象", isinstance(d.get("data"), dict))

    print("\n[参数错误]")
    e = client.post("/v1/recognize",
                    files={"image": ("a.png", photo.read_bytes(), "image/png")},
                    data={"scene": "not_a_scene"})
    check("非法 scene 400", e.status_code == 400, str(e.status_code))

    e = client.post("/v1/recognize", data={"scene": args.scene})
    check("缺图片 400", e.status_code == 400, str(e.status_code))

    e = client.post("/v1/recognize",
                    files={"image": ("a.txt", b"not an image", "text/plain")},
                    data={"scene": args.scene})
    check("非图片 400", e.status_code == 400, str(e.status_code))

    e = client.post("/v1/recognize", data={"scene": args.scene,
                                           "image_url": "file:///etc/passwd"})
    check("非 http URL 400", e.status_code == 400, str(e.status_code))

    print("\n[已下线接口]")
    for retired in RETIRED:
        resp = client.post(retired, files={"images": ("a.png", b"x", "image/png")})
        check(f"{retired} 已下线（404）", resp.status_code == 404, str(resp.status_code))

    print()
    if failures:
        print(f"契约不符 {len(failures)} 项：" + "、".join(failures))
        return 1
    print("接口契约检查通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())