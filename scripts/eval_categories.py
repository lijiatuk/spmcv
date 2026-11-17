#!/usr/bin/env python3
"""三个分场景接口的端到端验证。

单元测试只覆盖了纯函数聚合逻辑；这个脚本把真实图片按工单 + 类别喂给
/v1/arrival、/v1/repair、/v1/inspection，测三件单元测试测不到的事：

1. **归错类率** —— 传进某类的照片，有多大比例被识别成别的类（进了 unexpected）。
   这是分场景接口成立的前提，一旦高了，说明分类器或调用方约定有问题。
2. **动作判定准确率** —— repair 的加油/放油/有滤芯/无滤芯，对照数据集标签。
3. **完整工单的达成率** —— arrival 四件套是否都能识别到，inspection 签字盖章检出率。

用法：
    python scripts/eval_categories.py --split data/processed/v5/splits_group_5fold.json \
        --fold 0 --source data/raw/recut --api http://127.0.0.1:8017 \
        --out docs/reports/eval_categories.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import httpx

# 目录里的场景名 -> 哪一类接口
SCENE_CATEGORY = {
    "01_车头及车牌号": "arrival", "02_行驶证VIN": "arrival",
    "03_车架贴牌VIN": "arrival", "04_里程表": "arrival",
    "01_桥放油": "repair", "02_桥加油": "repair",
    "03_轮胎放油": "repair", "04_轮胎加油": "repair",
    "05_无滤芯": "repair", "06_有滤芯": "repair",
    "01_服务单首页VIN": "inspection",
    "02_服务单末页客户签字及服务站盖章": "inspection",
}

# 数据集目录名 -> repair 期望动作（有真值，可以算准确率）
EXPECTED_ACTION = {
    "01_桥放油": ("axle", "drain"), "02_桥加油": ("axle", "fill"),
    "03_轮胎放油": ("tire", "drain"), "04_轮胎加油": ("tire", "fill"),
    "05_无滤芯": ("filter", "none"), "06_有滤芯": ("filter", "has"),
}

ARRIVAL_SCENES = {"01_车头及车牌号", "02_行驶证VIN", "03_车架贴牌VIN", "04_里程表"}


def post(api: str, path: str, files: list[tuple[str, bytes]], data: dict | None = None) -> dict:
    r = httpx.post(f"{api}{path}", files=files, data=data or {}, timeout=300)
    r.raise_for_status()
    return r.json()


def evaluate_fold(fold_idx: int, split: dict, src: Path, api: str) -> dict:
    fold = next(s for s in split["splits"] if s["fold"] == fold_idx)

    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for item in fold["val"]:
        cat = SCENE_CATEGORY.get(item["label"])
        if cat:
            buckets[item["order"]][cat].append(item)

    fed = Counter()
    unexpected = Counter()
    hits = total = 0
    miss = []
    arrival_full = arrival_orders = 0
    sign = stamp = insp = 0
    errors = []

    for order in sorted(buckets):
        for cat in ("arrival", "repair", "inspection"):
            items = buckets[order].get(cat)
            if not items:
                continue
            files = [("images", (Path(it["path"]).name, (src / it["path"]).read_bytes(), "image/png"))
                     for it in items if (src / it["path"]).is_file()]
            if not files:
                continue
            fed[cat] += len(files)
            try:
                d = post(api, f"/v1/{cat}", files)
            except Exception as exc:  # noqa: BLE001
                errors.append({"order": order, "category": cat, "error": str(exc)})
                continue

            unexpected[cat] += len(d.get("unexpected") or [])

            if cat == "repair":
                per_photo = {p["filename"]: p.get("action") for p in d.get("photos", [])}
                for it in items:
                    exp = EXPECTED_ACTION.get(it["label"])
                    if not exp:
                        continue
                    slot, want = exp
                    got = per_photo.get(Path(it["path"]).name)
                    total += 1
                    if got == want:
                        hits += 1
                    else:
                        miss.append({"fold": fold_idx, "order": order,
                                     "label": it["label"], "want": want, "got": got})

            if cat == "arrival":
                arrival_orders += 1
                if len(d["present"]) >= 4 and not d["reasons"]:
                    arrival_full += 1

            if cat == "inspection":
                insp += 1
                sign += bool(d.get("signature"))
                stamp += bool(d.get("stamp"))

    def pct(a, b):
        return round(a / b, 4) if b else None

    return {
        "fold": fold_idx,
        "orders": len(buckets),
        "images_fed": dict(fed),
        "wrong_category": {c: [unexpected[c], fed[c]] for c in fed},
        "repair_action": [hits, total],
        "arrival_complete": [arrival_full, arrival_orders],
        "inspection": [sign, stamp, insp],
        "errors": errors,
        "misses": miss,
    }


def aggregate(runs: list[dict]) -> dict:
    def pct(a, b):
        return round(a / b, 4) if b else None

    def ratios(key, i, j):
        a = sum(r[key][i] for r in runs)
        b = sum(r[key][j] for r in runs)
        return pct(a, b)

    fed = Counter()
    for r in runs:
        fed.update(r["images_fed"])
    bad = Counter()
    for r in runs:
        for c, (u, _n) in r["wrong_category"].items():
            bad[c] += u

    hits = sum(r["repair_action"][0] for r in runs)
    total = sum(r["repair_action"][1] for r in runs)
    per_fold = [pct(r["repair_action"][0], r["repair_action"][1]) for r in runs]
    vals = [v for v in per_fold if v is not None]
    mean = sum(vals) / len(vals) if vals else None
    var = sum((v - mean) ** 2 for v in vals) / len(vals) if vals else None

    return {
        "folds": [r["fold"] for r in runs],
        "images_per_fold": [sum(r["images_fed"].values()) for r in runs],
        "wrong_category_rate": {c: pct(bad[c], fed[c]) for c in fed},
        "wrong_category_overall": pct(sum(bad.values()), sum(fed.values())),
        "repair_action_accuracy": pct(hits, total),
        "repair_action_per_fold": per_fold,
        "repair_action_mean": round(mean, 4) if mean is not None else None,
        "repair_action_std": round(var ** 0.5, 4) if var is not None else None,
        "arrival_complete_rate": ratios("arrival_complete", 0, 1),
        "signature_rate": ratios("inspection", 0, 2),
        "stamp_rate": ratios("inspection", 1, 2),
        "errors": sum(len(r["errors"]) for r in runs),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="data/processed/v5/splits_group_5fold.json")
    ap.add_argument("--fold", default="0", help="折号，或 all 跑全部")
    ap.add_argument("--source", default="data/raw/recut")
    ap.add_argument("--api", default="http://127.0.0.1:8017")
    ap.add_argument("--out", default="docs/reports/eval_categories.json")
    ap.add_argument("--limit-orders", type=int, default=0, help="只跑前 N 个工单，0=全部")
    args = ap.parse_args()

    split = json.loads(Path(args.split).read_text(encoding="utf-8"))
    src = Path(args.source)
    folds = ([f["fold"] for f in split["splits"]] if args.fold == "all"
             else [int(args.fold)])

    runs = []
    for idx in folds:
        print(f"\n=== fold {idx} ===", flush=True)
        run = evaluate_fold(idx, split, src, args.api)
        runs.append(run)
        h, t = run["repair_action"]
        print(f"  工单 {run['orders']}  动作 {h}/{t}"
              f"  归错类 {sum(run['wrong_category'][c][0] for c in run['wrong_category'])}"
              f"/{sum(run['images_fed'].values())}", flush=True)

    summary = aggregate(runs)
    all_misses = [m for r in runs for m in r["misses"]]

    payload = {"summary": summary, "per_fold": runs, "misses": all_misses}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    print("\n" + "=" * 52)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if all_misses:
        by_label = Counter((m["label"], m["want"], m["got"]) for m in all_misses)
        print("\n误判 Top：")
        for (label, want, got), n in by_label.most_common(10):
            print(f"  {label:<10} 期望 {str(want):<5} 得到 {str(got):<5} × {n}")
    print(f"\n输出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
