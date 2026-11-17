#!/usr/bin/env python3
"""对**运行中的服务**做接口契约检查（不是单元测试）。

上层系统接入前跑一遍，确认文档里写的和实际返回的一致：
  - 错误结构统一（code/message/request_id）
  - result 取值合法、codes 是机器码
  - 每张图带 model_version，能发现"切了模型但没重启"
  - 请求 ID 原样回传
  - 参数非法时给 422 而不是 500

    python scripts/contract_check.py --base http://127.0.0.1:8017 --photo data/x.png
退出码非 0 表示契约不符，可直接接进 CI 或部署门禁。
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import httpx

PASS, FAIL = "✅", "❌"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {PASS if cond else FAIL} {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8017")
    ap.add_argument("--photo", required=True)
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    photo = Path(args.photo)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    rid = uuid.uuid4().hex
    client = httpx.Client(base_url=base, headers=headers, timeout=180)
    files = {"images": (photo.name, photo.read_bytes(), "image/png")}

    print(f"契约检查 → {base}\n")

    print("[基础]")
    h = client.get("/healthz")
    check("/healthz 200", h.status_code == 200)
    check("/healthz 有 ok/version", {"ok", "version"} <= set(h.json() or {}))
    r = client.get("/readyz")
    body = r.json() or {}
    check("/readyz 有 vision/ocr", {"vision", "ocr"} <= set(body))
    check("vision.model_version 非空", bool((body.get("vision") or {}).get("model_version")),
          str(body.get("vision")))

    print("\n[契约声明]")
    meta = client.get("/v1/meta").json() or {}
    contract = meta.get("contract") or {}
    check("api_version 存在", bool(contract.get("api_version")))
    check("taxonomy_version 存在", bool(contract.get("taxonomy_version")))
    check("result 取值含 pass/partial/review",
          set(contract.get("result_values") or []) >= {"pass", "partial", "review"},
          str(contract.get("result_values")))

    print("\n[正常调用]")
    r = client.post("/v1/scan", files=files, headers={"X-Request-Id": rid})
    check("HTTP 200", r.status_code == 200, f"{r.status_code}")
    check("回传 X-Request-Id", r.headers.get("x-request-id") == rid,
          r.headers.get("x-request-id", ""))
    d = r.json() or {}
    check("result 合法", d.get("result") in {"pass", "partial", "review"}, str(d.get("result")))
    check("codes 是字符串数组", isinstance(d.get("codes"), list)
          and all(isinstance(c, str) for c in d.get("codes", [])))
    check("codes 是机器码（无中文）",
          all(not any("\u4e00" <= ch <= "\u9fff" for ch in c) for c in d.get("codes", [])),
          str(d.get("codes")))
    photos = d.get("photos") or []
    check("photos 非空", bool(photos))
    if photos:
        p = photos[0]
        check("每张图带 model_version", bool(p.get("model_version")), str(p.get("model_version")))
        check("每张图带 scene/confidence/index",
              {"scene", "confidence", "index"} <= set(p))

    print("\n[工单比对]")
    r2 = client.post("/v1/scan", files=files,
                     data={"category": "arrival", "expected_plate": "京A00000"})
    d2 = r2.json() or {}
    check("车牌不符时 result=review", d2.get("result") == "review", str(d2.get("result")))
    check("给出 PLATE_MISMATCH 映射或相关码",
          any(c in d2.get("codes", []) for c in ("PLATE_MISMATCH", "VIN_MISMATCH"))
          or bool(d2.get("plate")) is False,
          str(d2.get("codes")))

    print("\n[错误结构]")
    e = client.post("/v1/scan")
    check("缺参数返回 422", e.status_code == 422, str(e.status_code))
    eb = e.json() or {}
    check("错误体含 code/message/request_id", {"code", "message"} <= set(eb), str(eb)[:120])

    bad = client.post("/v1/scan", files={"images": ("x.txt", b"not an image", "text/plain")})
    check("非图片返回 400/413", bad.status_code in (400, 413), str(bad.status_code))
    check("错误体仍是统一结构", {"code", "message"} <= set(bad.json() or {}))

    nf = client.get("/v1/definitely-not-a-route")
    check("未知路由 404", nf.status_code == 404)
    check("404 也是统一结构", {"code", "message", "request_id"} <= set(nf.json() or {}))

    print()
    if failures:
        print(f"契约不符 {len(failures)} 项：" + "、".join(failures))
        return 1
    print("接口契约检查通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
