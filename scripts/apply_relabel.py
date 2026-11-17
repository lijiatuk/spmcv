#!/usr/bin/env python3
"""执行改标计划：把图片从错误类别搬到正确类别。

设计原则：
  - **默认 dry-run**，必须显式 `--apply` 才动文件
  - 每次搬动都写撤销日志，出问题能原样搬回来
  - 目标目录必须是一个已知的类别目录，不允许凭空创建

用法：
    python scripts/apply_relabel.py                 # 预览，不动
    python scripts/apply_relabel.py --apply         # 执行
    python scripts/apply_relabel.py --undo          # 撤销上一次
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "data/processed/relabel_plan.json"
UNDO_DIR = ROOT / "data/processed/undo"
CONFIG = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
# class_map 的 key 是目录名（"01_桥放油"），value.slug 是英文（bridge_drain）。
# 网页传的是 slug，这里两种都认。
SLUG_TO_DIR = {meta["slug"]: name for name, meta in CONFIG["class_map"].items()}
CLASS_NAMES = set(CONFIG["class_map"]) | set(SLUG_TO_DIR)


def find_class_dir(root: Path, class_name: str) -> Path | None:
    """在数据集根下找这个类别的目录（只认叶子目录）。"""
    for d in root.rglob(class_name):
        if d.is_dir() and d.name == class_name:
            return d
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的搬动文件")
    ap.add_argument("--undo", action="store_true", help="撤销上一次执行")
    ap.add_argument("--plan", default=str(PLAN))
    args = ap.parse_args()

    if args.undo:
        logs = sorted(UNDO_DIR.glob("undo_*.json")) if UNDO_DIR.is_dir() else []
        if not logs:
            print("没有可撤销的记录")
            return 0
        log = json.loads(logs[-1].read_text(encoding="utf-8"))
        moved = 0
        for m in reversed(log["moves"]):
            src, dst = Path(m["dst"]), Path(m["src"])
            if dst.exists() and not src.exists():
                shutil.move(str(dst), str(src))
                moved += 1
        print(f"已撤销 {moved} 个文件（来自 {logs[-1].name}）")
        logs[-1].rename(logs[-1].with_suffix(".undone"))
        return 0

    plan_path = Path(args.plan)
    if not plan_path.is_file():
        print(f"计划文件不存在: {plan_path}")
        return 1
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    items = plan.get("items") or []
    if not items:
        print("计划为空")
        return 0

    # 校验 + 归类
    to_move = []
    problems = []
    for it in items:
        src = Path(it["src"])
        target_cls = it.get("to")
        target_cls = SLUG_TO_DIR.get(target_cls, target_cls)   # slug -> 目录名
        if target_cls not in CLASS_NAMES:
            problems.append(f"未知目标类别 {target_cls}: {src.name}")
            continue
        if not src.is_file():
            problems.append(f"源文件不存在: {src}")
            continue
        dst_dir = find_class_dir(src.parents[3] if len(src.parents) > 3 else ROOT, target_cls)
        if dst_dir is None:
            problems.append(f"找不到类别目录 {target_cls}")
            continue
        if src.parent == dst_dir:
            problems.append(f"已经在该类，跳过: {src.name}")
            continue
        to_move.append((src, dst_dir / src.name))

    print(f"计划 {len(items)} 条，可执行 {len(to_move)} 条，问题 {len(problems)} 条")
    if problems:
        for p in problems[:10]:
            print("  ⚠", p)
        if len(problems) > 10:
            print(f"  …还有 {len(problems) - 10} 条")

    by_pair = Counter((s.parent.name, d.parent.name) for s, d in to_move)
    print("\n搬动统计：")
    for (a, b), n in by_pair.most_common():
        print(f"  {a} → {b}: {n} 张")

    if not args.apply:
        print("\n[dry-run] 没有动任何文件。加 --apply 才会执行。")
        return 0

    UNDO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    undo = {"created_at": stamp, "moves": []}
    done = 0
    for src, dst in to_move:
        if dst.exists():
            print(f"  ⚠ 目标已存在，跳过: {dst.name}")
            continue
        shutil.move(str(src), str(dst))
        undo["moves"].append({"src": str(src), "dst": str(dst)})
        done += 1

    (UNDO_DIR / f"undo_{stamp}.json").write_text(
        json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已搬动 {done} 个文件。撤销：python scripts/apply_relabel.py --undo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
