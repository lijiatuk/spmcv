#!/usr/bin/env python3
"""生产使用模拟：按顶层目录分三类，用**完整工单**真实调用三个接口。

和 `eval_categories.py` 的区别：
  - 那个用 fold 切分的抽样验证集（每单只有 1–3 张，测不了完整流程）
  - 这个用原始素材里**每个工单的全部照片**，模拟运营真实上传

流程完全等同用户操作：
    01_接车照片/*      -> POST /v1/arrival
    02_故障及修复照片/* -> POST /v1/repair
    03_项目检查表/*     -> POST /v1/inspection

产出：
    - 每类通过率、复核原因 Top（能看出哪类证据最常缺）
    - 整单"三类全通过"率
    - 端到端延迟 P50 / P95
    - 逐单明细，供人工抽查

    python scripts/simulate_production.py --orders 30     # 先跑 30 单看看
    python scripts/simulate_production.py                 # 全量 111 单
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

TOP_CATEGORY = {
    "01_接车照片": "arrival",
    "02_故障及修复照片": "repair",
    "03_项目检查表": "inspection",
}


def order_of(name: str) -> str | None:
    """文件名形如 0478_IKD2043420261108004_2668587.png。"""
    for token in name.split("_"):
        if token.startswith("IKD"):
            return token
    return None


def collect(root: Path) -> dict[str, dict[str, list[Path]]]:
    """工单 -> 类别 -> 图片。只认三个顶层目录。"""
    orders: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    skipped = 0
    for top in sorted(root.iterdir()):
        cat = TOP_CATEGORY.get(top.name)
        if not cat or not top.is_dir():
            continue
        for p in sorted(top.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            order = order_of(p.name)
            if not order:
                skipped += 1
                continue
            orders[order][cat].append(p)
    return orders, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/raw/recut")
    ap.add_argument("--api", default="http://127.0.0.1:8017")
    ap.add_argument("--orders", type=int, default=0, help="只跑前 N 单，0=全部")
    ap.add_argument("--out", default="docs/reports/production_sim.json")
    args = ap.parse_args()

    orders, skipped = collect(Path(args.root))
    names = sorted(orders, key=lambda o: -sum(len(v) for v in orders[o].values()))
    if args.orders:
        names = names[: args.orders]

    stats = {c: {"ok": 0, "run": 0, "reasons": Counter(), "wrong_cat": 0,
                 "images": 0, "latency": []} for c in ("arrival", "repair", "inspection")}
    full_pass = 0
    all_three = 0
    details = []
    t_all = time.time()

    for i, order in enumerate(names, 1):
        cats = orders[order]
        rec = {"order": order, "result": {}}
        cats_ok = 0
        for cat in ("arrival", "repair", "inspection"):
            files = cats.get(cat) or []
            if not files:
                rec["result"][cat] = None
                continue
            payload = [("images", (p.name, p.read_bytes(), "image/png")) for p in files]
            t0 = time.time()
            try:
                r = httpx.post(f"{args.api}/v1/{cat}", files=payload, timeout=300)
                r.raise_for_status()
                d = r.json()
            except Exception as exc:  # noqa: BLE001
                rec["result"][cat] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            dt = (time.time() - t0) * 1000

            s = stats[cat]
            s["run"] += 1
            s["images"] += len(files)
            s["latency"].append(dt)
            s["wrong_cat"] += len(d.get("unexpected") or [])
            if d.get("ok"):
                s["ok"] += 1
                cats_ok += 1
            for reason in d.get("reasons") or []:
                s["reasons"][reason.split("：")[0]] += 1

            rec["result"][cat] = {"ok": d.get("ok"), "n": len(files),
                                  "reasons": d.get("reasons"),
                                  "wrong": len(d.get("unexpected") or []),
                                  "ms": round(dt)}

        present = [c for c in rec["result"].values() if c]
        if len(present) == 3:
            all_three += 1
            if cats_ok == 3:
                full_pass += 1
        details.append(rec)
        if i % 10 == 0:
            print(f"  ... {i}/{len(names)} 单", flush=True)

    def rate(a, b):
        return round(a / b, 4) if b else None

    def pct(v, p):
        if not v:
            return None
        v = sorted(v)
        return round(v[min(len(v) - 1, int(len(v) * p))])

    summary = {
        "orders_total": len(names),
        "orders_with_all_three": all_three,
        "orders_full_pass": full_pass,
        "full_pass_rate": rate(full_pass, all_three),
        "skipped_files": skipped,
        "elapsed_s": round(time.time() - t_all),
        "per_category": {
            cat: {
                "calls": s["run"],
                "ok": s["ok"],
                "ok_rate": rate(s["ok"], s["run"]),
                "images": s["images"],
                "wrong_category": s["wrong_cat"],
                "latency_p50": pct(s["latency"], 0.5),
                "latency_p95": pct(s["latency"], 0.95),
                "top_reasons": s["reasons"].most_common(8),
            }
            for cat, s in stats.items()
        },
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({**summary, "details": details},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n输出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
