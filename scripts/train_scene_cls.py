#!/usr/bin/env python3
"""Train the 12-class scene classifier with Ultralytics YOLO-cls.

训练完成后会自动在 `<data>/test` 上跑一次留出评测，写 `test.json`：
平台据此显示“测试集准确率”，不再拿训练集/验证集数字当验收结果。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from ultralytics import YOLO

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def resolve_device(name: str) -> str:
    if name and name != "auto":
        return name
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def eval_test(weights: Path, data: Path, imgsz: int, device: str) -> dict:
    root = data / "test"
    if not root.is_dir():
        return {"available": False, "reason": "数据集没有 test 目录"}
    files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in IMG_EXT]
    if not files:
        return {"available": False, "reason": "test 目录为空"}

    model = YOLO(str(weights))
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    n = correct = 0
    per_class: dict[str, Counter] = {}
    for p in files:
        want = p.parent.name
        result = model.predict(source=str(p), imgsz=imgsz, device=device, verbose=False)
        if not result:
            continue
        probs = result[0].probs
        got = names.get(int(probs.top1), str(probs.top1))
        n += 1
        ok = got == want
        correct += int(ok)
        bucket = per_class.setdefault(want, Counter())
        bucket["n"] += 1
        bucket["correct"] += int(ok)
        if not ok:
            bucket["pred_" + str(got)] += 1

    report = {
        "available": True,
        "n": n,
        "correct": correct,
        "top1": round(correct / n, 6) if n else None,
        "imgsz": imgsz,
        "device": device,
        "per_class": {
            k: {"n": v["n"], "correct": v["correct"],
                "recall": round(v["correct"] / v["n"], 4) if v["n"] else None,
                "confusions": {kk[5:]: vv for kk, vv in v.items() if kk.startswith("pred_")}}
            for k, v in sorted(per_class.items())
        },
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    # 默认用仓库内的本地权重，避免依赖 CWD 下临时下载的文件
    ap.add_argument("--model", default="models/pretrained/yolo26n-cls.pt")
    ap.add_argument("--data", default="data/processed/datasets/latest")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="auto", help="auto / cpu / cuda / cuda:0 / mps")
    ap.add_argument("--project", default="var/runs")
    ap.add_argument("--name", default="cls_v1")
    ap.add_argument("--lr0", type=float, default=0.01)
    ap.add_argument("--freeze", type=int, default=0)
    args = ap.parse_args()

    device = resolve_device(args.device)
    data = Path(args.data)
    print(f"[train] device={device} data={data} epochs={args.epochs} imgsz={args.imgsz}",
          flush=True)

    model = YOLO(args.model)
    model.train(
        data=str(data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=str(Path(args.project).resolve()),
        name=args.name,
        exist_ok=True,
        pretrained=True,
        optimizer="auto",
        patience=15,
        workers=2,
        lr0=args.lr0,
        freeze=args.freeze or None,
    )

    run_dir = Path(args.project).resolve() / args.name
    best = run_dir / "weights" / "best.pt"
    if best.is_file():
        print("[train] 开始在 test 留出集上评测…", flush=True)
        try:
            report = eval_test(best, data, args.imgsz, device)
        except Exception as exc:  # noqa: BLE001
            report = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        report["weights"] = str(best)
        (run_dir / "test.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report.get("available"):
            print(f"[train] test top1={report['top1']} n={report['n']}", flush=True)
        else:
            print(f"[train] test 未评测：{report.get('reason')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
