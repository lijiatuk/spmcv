#!/usr/bin/env python3
"""接口级场景准确率：逐张调 /v1/analyze，和目录标签对答案。

和 `eval_heads.py` 的区别：那个直接跑模型，这个**走完整 HTTP 链路**，
所以能测到编排层才有的偏差——分组头覆盖、动作头纠偏、里程→行驶证兜底等。
实测这些逻辑会让「接口返回的 scene」和「模型 top1」不一致。

前提：验证折的工单从未出现在训练集里。fold-0 满足；fold1–4 用当前模型评会泄露
（它们的工单 100% 在 fold-0 训练集中），要评必须配合 `scripts/fivefold.py` 每折重训。

用法：
    python scripts/eval_api_scene.py --fold 0
    python scripts/eval_api_scene.py --fold 0 --show-mismatch
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def load_slug_map() -> dict[str, str]:
    cfg = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
    return {name: meta["slug"] for name, meta in cfg["class_map"].items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="data/processed/v5/splits_group_5fold.json")
    ap.add_argument("--fold", type=int, default=0)
    ap.add_argument("--source", default="data/raw/recut")
    ap.add_argument("--api", default="http://127.0.0.1:8017")
    ap.add_argument("--out", default="docs/reports/eval_api_scene.json")
    ap.add_argument("--show-mismatch", action="store_true")
    args = ap.parse_args()

    slug_of = load_slug_map()
    split = json.loads(Path(args.split).read_text(encoding="utf-8"))
    fold = next(s for s in split["splits"] if s["fold"] == args.fold)
    src = Path(args.source)

    total = correct = 0
    per_class_hit = Counter()
    per_class_n = Counter()
    confusions = Counter()
    mismatches = []
    latencies = []
    predictions = []

    for item in fold["val"]:
        path = src / item["path"]
        if not path.is_file():
            continue
        want = slug_of.get(item["label"])
        if not want:
            continue
        try:
            with path.open("rb") as fh:
                r = httpx.post(f"{args.api}/v1/analyze",
                               files={"image": (path.name, fh, "image/png")},
                               timeout=120)
            r.raise_for_status()
            d = r.json()
        except Exception as exc:  # noqa: BLE001
            mismatches.append({"file": path.name, "want": want,
                               "got": None, "error": str(exc)})
            continue

        got = d.get("scene")
        predictions.append({"file": path.name, "want": want, "got": got,
                            "scene_conf": d.get("scene_confidence"),
                            "need_review": bool(d.get("need_review"))})
        total += 1
        per_class_n[want] += 1
        if d.get("latency_ms"):
            latencies.append(d["latency_ms"])
        if got == want:
            correct += 1
            per_class_hit[want] += 1
        else:
            confusions[(want, got)] += 1
            mismatches.append({
                "file": path.name, "order": item["order"],
                "want": want, "got": got,
                "scene_conf": d.get("scene_confidence"),
                "need_review": d.get("need_review"),
                "rerouted_from": d.get("rerouted_from"),
                "reasons": d.get("review_reasons"),
            })

    def pct(a, b):
        return round(a / b, 4) if b else None

    per_class = {
        c: {"n": per_class_n[c], "correct": per_class_hit[c],
            "acc": pct(per_class_hit[c], per_class_n[c])}
        for c in sorted(per_class_n)
    }
    lat = sorted(latencies)
    summary = {
        "fold": args.fold,
        "n": total,
        "correct": correct,
        "accuracy": pct(correct, total),
        "latency_p50": lat[len(lat) // 2] if lat else None,
        "latency_p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        "per_class": per_class,
        "confusions": [{"want": a, "got": b, "n": n}
                       for (a, b), n in confusions.most_common(15)],
        "mismatches": mismatches,
        "predictions": predictions,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("mismatches", "predictions")},
                     ensure_ascii=False, indent=2))

    # 阈值扫描：如果只放行 scene_confidence >= t 且未被复核的，静默错误率是多少
    print("\n阈值扫描（放行 = 未复核 且 置信度≥阈值）：")
    print(f"{'阈值':>6}{'放行数':>8}{'覆盖率':>9}{'放行中错判':>12}{'放行准确率':>12}")
    for t in (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        passed = [p for p in predictions
                  if not p["need_review"] and (p["scene_conf"] or 0) >= t]
        wrong = sum(1 for p in passed if p["got"] != p["want"])
        cov = len(passed) / len(predictions) if predictions else 0
        acc = (len(passed) - wrong) / len(passed) if passed else None
        print(f"{t:>6.2f}{len(passed):>8}{cov:>8.1%}{wrong:>12}"
              f"{(f'{acc:.1%}' if acc is not None else '—'):>12}")
    print(f"\n错判 {len(mismatches)} 张")
    if args.show_mismatch:
        for m in mismatches:
            print(f"  {m['file'][:36]:<38} 应为 {m['want']:<14} 得到 {str(m['got']):<14}"
                  f" conf={m.get('scene_conf')} rerouted={m.get('rerouted_from')}")
    print(f"输出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
