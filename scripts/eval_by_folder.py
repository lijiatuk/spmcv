#!/usr/bin/env python3
"""按**叶子文件夹**逐类验证：每张图调 /v1/analyze，和所在目录的类别对答案。

比 `eval_api_scene.py` 更细：那个按折抽样，这个按你给的目录树全量跑，
输出的就是「每个场景下、每个子文件夹，这一类图处理得怎么样」。

输出三个方面：
  - 每类：张数 / 判对数 / 准确率 / 平均延迟
  - 每类的错判去向（被认成了什么）
  - 每类的前几张错图，便于人工抽查

    python scripts/eval_by_folder.py --root "data/SPM系统AI识别训练照片_新增_20261111-20261113"
    python scripts/eval_by_folder.py --root "data/SPM系统AI识别训练照片" --out docs/reports/eval_folders_old.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def load_slug_map() -> dict[str, str]:
    cfg = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
    return {name: meta["slug"] for name, meta in cfg["class_map"].items()}


def analyze(api: str, path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            r = httpx.post(f"{api}/v1/analyze",
                           files={"image": (path.name, fh, "image/png")},
                           timeout=180)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8017")
    ap.add_argument("--out", default="docs/reports/eval_by_folder.json")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    slug_of = load_slug_map()
    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"目录不存在: {root}")

    # 收集：叶子目录 -> 文件
    tasks = []
    skipped_dirs = []
    for d in sorted(p for p in root.rglob("*") if p.is_dir()):
        slug = slug_of.get(d.name)
        if not slug:
            skipped_dirs.append(str(d.relative_to(root)))
            continue
        files = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXT)
        for f in files:
            tasks.append((str(d.relative_to(root)), slug, f))

    print(f"共 {len(tasks)} 张图，{len({t[0] for t in tasks})} 个类目录")
    if skipped_dirs:
        print(f"跳过 {len(skipped_dirs)} 个非类目录: {skipped_dirs[:5]}")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyze, args.api, f): (folder, slug, f)
                   for folder, slug, f in tasks}
        done = 0
        for fut in futures:
            pass
        for fut, (folder, slug, f) in futures.items():
            d = fut.result()
            done += 1
            if done % 50 == 0:
                print(f"  ... {done}/{len(tasks)}", flush=True)
            got = d.get("scene")
            results.append({
                "folder": folder, "want": slug, "got": got,
                "file": f.name,
                "conf": d.get("scene_confidence"),
                "ms": d.get("latency_ms"),
                "error": d.get("error"),
            })

    # 聚合
    by_folder: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_folder[r["folder"]].append(r)

    summary = []
    for folder in sorted(by_folder):
        rows = by_folder[folder]
        correct = [r for r in rows if r["got"] == r["want"]]
        wrong = [r for r in rows if r["got"] != r["want"]]
        conf = Counter(r["got"] for r in wrong)
        lat = sorted(r["ms"] for r in rows if r["ms"])
        summary.append({
            "folder": folder,
            "expected": rows[0]["want"],
            "n": len(rows),
            "correct": len(correct),
            "accuracy": round(len(correct) / len(rows), 4) if rows else None,
            "avg_ms": round(sum(lat) / len(lat)) if lat else None,
            "confusions": conf.most_common(5),
            "wrong_files": [r["file"] for r in wrong[:5]],
            "errors": sum(1 for r in rows if r["error"]),
        })

    total = len(results)
    total_ok = sum(1 for r in results if r["got"] == r["want"])
    payload = {
        "root": str(root),
        "total": total,
        "correct": total_ok,
        "accuracy": round(total_ok / total, 4) if total else None,
        "by_folder": summary,
        "details": results,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    print(f"\n{'目录':<34}{'张数':>5}{'正确':>5}{'准确率':>8}{'平均ms':>8}")
    for s in summary:
        acc = f"{s['accuracy']:.1%}" if s["accuracy"] is not None else "—"
        print(f"{s['folder']:<34}{s['n']:>5}{s['correct']:>5}{acc:>8}{s['avg_ms'] or 0:>8}")
        if s["confusions"]:
            top = "、".join(f"{k}×{v}" for k, v in s["confusions"][:3])
            print(f"    误判去向: {top}")
    print(f"\n总体 {total_ok}/{total} = {total_ok/total:.1%}")
    print(f"输出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
