"""数据集 / 评测 / 训练曲线可视化与带审计的人工复核接口。

路径安全：所有文件访问都必须落在 ALLOWED_ROOTS 之内，且不能是 `..` 穿越。
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.config import BASE_DIR, settings
from app.services import datasets as dataset_service
from app.services.feedback_store import feedback_store
from app.services.paths import portable_path
from app.services.vision_client import VisionClient

router = APIRouter(tags=["viz"])
VISION = VisionClient(settings.vision_url, settings.scene_timeout, settings.detect_timeout)

ROOT = BASE_DIR
ALLOWED_ROOTS = [
    ROOT / "data/raw/recut",
    ROOT / "data/raw/legacy",
    ROOT / "data/SPM系统AI识别训练照片",
    ROOT / "data/SPM系统AI识别训练照片_新增_20261111-20261113",
    ROOT / "data/processed",
]
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# 这几个目录和其它根重复（同一批图被重新解压/裁剪过），
# 可以浏览，但不计入总数，否则 KPI 会虚高。
DUPLICATE_ROOTS = {"legacy", "SPM系统AI识别训练照片"}

CONFIG = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
CLASS_MAP = CONFIG["class_map"]


@router.get("/viz/api/labeling_guide", response_class=PlainTextResponse)
def get_labeling_guide():
    guide = ROOT / "docs/labeling_guide_v1.md"
    if not guide.is_file():
        raise HTTPException(404, "标注规范文件不存在")
    return PlainTextResponse(guide.read_text(encoding="utf-8"))


def _portable(path_str: str | Path) -> Path:
    """把旧机器 / 旧容器写死的绝对路径重新挂到当前 ROOT 上。"""
    return portable_path(path_str, ROOT)


def _safe(path_str: str) -> Path:
    """只允许访问白名单目录内的文件。"""
    try:
        p = _portable(path_str).resolve()
    except Exception as exc:
        raise HTTPException(400, "路径非法") from exc
    for root in ALLOWED_ROOTS:
        if root.exists() and p.is_relative_to(root.resolve()) and p.is_file():
            return p
    raise HTTPException(403, "路径不在允许范围内")


def _dataset_roots() -> list[Path]:
    return [r for r in ALLOWED_ROOTS if r.exists() and "processed" not in r.parts]


@router.get("/viz/api/classes")
def classes(root: str = ""):
    """列出某个数据集根目录下的 12 个类及张数。"""
    target = _portable(root).resolve() if root else (_dataset_roots() or [None])[0]
    if not target or not target.is_dir():
        raise HTTPException(404, "数据集目录不存在")
    if not any(target == r.resolve() for r in _dataset_roots()):
        raise HTTPException(403, "目录不在允许范围")
    out = []
    for d in sorted(p for p in target.rglob("*") if p.is_dir()):
        if d.name not in CLASS_MAP:
            continue
        n = sum(1 for f in d.iterdir() if f.suffix.lower() in IMG_EXT)
        if n:
            out.append({"name": d.name, "slug": CLASS_MAP[d.name]["slug"],
                        "path": str(d), "count": n})
    return {"root": str(target), "roots": [str(r) for r in _dataset_roots()],
            "classes": out}


@router.get("/viz/api/images")
def images(dir: str):
    d = _portable(dir).resolve()
    if not any(d.is_relative_to(r.resolve()) for r in ALLOWED_ROOTS):
        raise HTTPException(403, "目录不在允许范围")
    if not d.is_dir():
        raise HTTPException(404, "目录不存在")
    files = sorted(f for f in d.iterdir() if f.suffix.lower() in IMG_EXT)
    return {"dir": str(d), "count": len(files), "files": [str(f) for f in files]}


@router.get("/viz/api/file")
def file(path: str):
    return FileResponse(_safe(path))


@router.get("/viz/api/detect")
async def detect(path: str, conf: float = 0.10):
    p = _safe(path)
    raw = p.read_bytes()
    det = await asyncio.to_thread(VISION.detect, raw, p.name, "", conf)
    boxes = []
    for b in det.get("boxes") or []:
        box = b.get("box") or []
        if len(box) >= 4:
            boxes.append({"label": b.get("label"),
                          "score": round(float(b.get("score") or 0), 3),
                          "box": [round(float(v), 1) for v in box[:4]]})
    boxes.sort(key=lambda x: -x["score"])
    return {"path": str(p), "boxes": boxes[:12]}


@router.get("/viz/api/eval")
def eval_summary():
    """读取已有的评测结果文件。"""
    out = {}
    for key, rel in (
        ("folders_new", "docs/reports/eval_folders_new.json"),
        ("folders_old", "docs/reports/eval_folders_old.json"),
        ("api_scene", "docs/reports/eval_api_scene.json"),
        ("fivefold", "docs/reports/fivefold_scene.json"),
        ("categories", "docs/reports/eval_categories.json"),
    ):
        p = ROOT / rel
        if p.is_file():
            try:
                out[key] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001, S112 - stale dashboards are skipped
                continue
    oof_path = ROOT / "docs/reports/verified_oof.json"
    if oof_path.is_file():
        out["oof"] = _oof_summary(_load_json(oof_path))
    out["directory_replay_warning"] = "目录回放集包含训练样本，不是独立泛化评估；仅用于定位图像问题。"
    return out


@router.get("/viz/api/runs")
def runs():
    """训练曲线：读 var/runs/*/results.csv 的 top1 与 loss。"""
    out = []
    base = ROOT / "var/runs"
    if not base.is_dir():
        return {"runs": []}
    for d in sorted(base.iterdir()):
        f = d / "results.csv"
        if not f.is_file():
            continue
        try:
            rows = list(csv.DictReader(f.open(encoding="utf-8")))
        except Exception:  # noqa: BLE001, S112 - one malformed run file does not hide the rest
            continue
        if not rows:
            continue
        rows = [{k.strip(): v for k, v in r.items()} for r in rows]
        top1_key = next((k for k in rows[0] if "top1" in k), None)
        loss_key = next((k for k in rows[0] if "train/loss" in k), None)
        series = []
        for r in rows:
            try:
                series.append({
                    "epoch": int(float(r["epoch"])),
                    "top1": float(r[top1_key]) if top1_key else None,
                    "loss": float(r[loss_key]) if loss_key else None,
                })
            except Exception:  # noqa: BLE001, S112 - skip a malformed metric row
                continue
        if series:
            out.append({"name": d.name, "series": series})
    return {"runs": out}


# ---------------- 错误样本工作台 ----------------

MISTAKE_SOURCES = (
    ("new", "docs/reports/eval_folders_new.json",
     "data/SPM系统AI识别训练照片_新增_20261111-20261113"),
    ("old", "docs/reports/eval_folders_old.json",
     "data/SPM系统AI识别训练照片"),
)

# 人工处理过的样本会记在这里，重跑评测不会覆盖
HARD_CASES = ROOT / "data/processed/hard_cases.json"


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


feedback_store.save_hard_cases(_load_json(HARD_CASES).get("items") or [], reviewer="legacy-import")


def _oof_summary(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "predictions"}


def _reviewer(request: Request) -> str:
    user = getattr(request.state, "user", None) or {}
    return str(user.get("account") or user.get("userName") or "anonymous")


@router.get("/viz/api/mistakes")
def mistakes(source: str = "all"):
    """汇总评测里的误判样本，按「应为 → 实际」分组。"""
    rows = []
    for key, rel, base in MISTAKE_SOURCES:
        if source != "all" and source != key:
            continue
        d = _load_json(ROOT / rel)
        for r in d.get("details") or []:
            if not r.get("got") or r["got"] == r["want"]:
                continue
            rows.append({
                "source": key,
                "path": str(ROOT / base / r["folder"] / r["file"]),
                "folder": r["folder"],
                "want": r["want"],
                "got": r["got"],
                "conf": r.get("conf"),
                "file": r["file"],
            })

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["want"], r["got"]), []).append(r)

    summary = []
    for (want, got), items in groups.items():
        summary.append({
            "want": want, "got": got, "n": len(items),
            "avg_conf": round(sum(i["conf"] or 0 for i in items) / len(items), 3),
            "items": sorted(items, key=lambda x: -(x["conf"] or 0)),
        })
    summary.sort(key=lambda g: -g["n"])

    handled = {it["path"] for it in feedback_store.list_hard_cases()}

    return {"total": len(rows), "groups": summary, "handled": sorted(handled)}


@router.post("/viz/api/hard_cases")
async def save_hard_cases(payload: dict, request: Request):
    """把选中的错误样本登记为"难例"，供后续补标/重训。

    只写清单不搬文件——文件已经在数据集里，搬走反而让评测对不上。
    """
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(400, "items 必须是数组")
    accepted = []
    for it in items:
        try:
            p = _safe(str(it.get("path") or ""))
        except HTTPException:
            continue
        accepted.append({"path": str(p), "want": it.get("want"),
                         "got": it.get("got"), "note": it.get("note") or ""})
    result = feedback_store.save_hard_cases(accepted, reviewer=_reviewer(request))
    return {**result, "file": str(feedback_store.path)}


# ---------------- 总览 ----------------

@router.get("/viz/api/overview")
def overview():
    """控制台首页：模型 / 数据 / 请求 / 精度 四类指标。"""
    from app.metrics import metrics as _m

    model_dir = ROOT / "models/current"
    manifest = _load_json(model_dir / "manifest.json") if model_dir.is_dir() else {}
    registry = _load_json(ROOT / "models/registry.json")

    classes = []
    for root in _dataset_roots():
        for d in sorted(p for p in root.rglob("*") if p.is_dir()):
            if d.name not in CLASS_MAP:
                continue
            n = sum(1 for f in d.iterdir() if f.suffix.lower() in IMG_EXT)
            if n:
                classes.append({"name": d.name, "slug": CLASS_MAP[d.name]["slug"],
                                "count": n, "root": root.name,
                                "counted": root.name not in DUPLICATE_ROOTS})

    stats = {k: dict(v) for k, v in _m._stats.items()}
    lat = {k: sorted(v) for k, v in _m._latencies.items()}
    perf = {}
    for k, s in stats.items():
        v = lat.get(k) or []
        n = s["requests"]
        perf[k] = {
            "requests": n, "errors": s["errors"], "reviews": s["reviews"],
            "p50": round(v[len(v) // 2], 1) if v else None,
            "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))], 1) if v else None,
        }

    folders_new = _load_json(ROOT / "docs/reports/eval_folders_new.json")
    folders_old = _load_json(ROOT / "docs/reports/eval_folders_old.json")
    fivefold = _load_json(ROOT / "docs/reports/fivefold_scene.json")
    hard = feedback_store.list_hard_cases()
    oof_path = ROOT / "docs/reports/verified_oof.json"
    oof = _load_json(oof_path) if oof_path.is_file() else {}
    bucket_metrics = oof.get("per_bucket") or {}

    return {
        "model": {
            "version": manifest.get("model_version")
                       or next(iter(registry.get("versions", {})), "—"),
            "heads": len(manifest.get("roles") or {}),
            "roles": list((manifest.get("roles") or {}).keys()),
            "metrics": (manifest.get("metrics") or {}),
            "fusion_enabled": settings.enable_fusion,
            "notes": manifest.get("notes") or "",
            "models": [{"name": k, "status": "active"} for k in (manifest.get("roles") or {})],
        },
        "dataset": {
            "classes": classes,
            "total": sum(c["count"] for c in classes if c.get("counted", True)),
            "roots": [str(r) for r in _dataset_roots()],
        },
        "perf": perf,
        "eval": {
            "directory_replay_accuracy": folders_new.get("accuracy"),
            "directory_replay_total": folders_new.get("total"),
            "directory_replay_warning": "包含训练样本，不是独立泛化评估",
            "folders_new": folders_new.get("by_folder") or [],
            "folders_old": folders_old.get("by_folder") or [],
            "fivefold_mean": oof.get("top1", fivefold.get("top1_mean")),
            "fivefold_std": fivefold.get("top1_std"),
            "oof": _oof_summary(oof),
            "new_domain": bucket_metrics.get("new"),
            "old_domain": bucket_metrics.get("old"),
            "hard_cases": len(hard),
        },
    }


# ---------------- 改标计划（不直接动文件） ----------------

RELABEL_PLAN = ROOT / "data/processed/relabel_plan.json"
feedback_store.import_legacy_plan(RELABEL_PLAN)


def _export_plan_snapshot() -> None:
    """Keep the offline relabel tool compatible; SQLite remains canonical."""
    RELABEL_PLAN.parent.mkdir(parents=True, exist_ok=True)
    tmp = RELABEL_PLAN.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(feedback_store.list_plan(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(RELABEL_PLAN)


@router.post("/viz/api/relabel_plan")
async def add_relabel_plan(payload: dict, request: Request):
    """登记"这张图应该改到哪一类"。只写计划，不搬文件。

    真正的搬动交给 `scripts/apply_relabel.py`，带 dry-run 和撤销日志。
    这样网页误点不会破坏数据集。
    """
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items 不能为空")

    accepted = []
    for it in items:
        src = str(it.get("src") or "")
        dst = str(it.get("to") or "")
        if not src or not dst:
            continue
        try:
            resolved = _safe(src)
        except HTTPException:
            continue
        if resolved.suffix.lower() not in IMG_EXT:
            continue
        accepted.append({
            "src": str(resolved),
            "to": dst,
            "from": it.get("from") or "",
            "note": it.get("note") or "",
        })
    result = feedback_store.save_plan(accepted, reviewer=_reviewer(request))
    _export_plan_snapshot()
    return {**result, "file": str(RELABEL_PLAN)}


@router.get("/viz/api/relabel_plan")
def get_relabel_plan():
    return feedback_store.list_plan()


# ---------------- 快速标注队列 ----------------

OIL_FAMILY = ["bridge_drain", "bridge_fill", "tire_drain", "tire_fill",
              "filter_none", "filter_has"]
ARRIVAL_FAMILY = ["arrival_plate", "license_vin", "frame_vin", "odometer"]
SHEET_FAMILY = ["service_front", "service_sign"]


@router.get("/viz/api/review_queue")
def review_queue(source: str = "new", family: str = "oil",
                 limit: int = 400, low_first: bool = True):
    """待标注队列：模型预判 + 置信度，低置信在前。

    数据来自已有评测结果（`details` 已含全部图片的预测），不重新跑模型。
    """
    fam = {"oil": OIL_FAMILY, "arrival": ARRIVAL_FAMILY, "sheet": SHEET_FAMILY}.get(family)
    if not fam:
        raise HTTPException(400, "family 只能是 oil / arrival / sheet")

    # 历史记录里存的是导出机器的绝对路径，比较前统一换算到当前 ROOT，
    # 否则换机部署后「已复核」集合永远对不上，同一张图会反复出现。
    done = {str(_portable(p)) for p in feedback_store.latest_paths()}
    out = []
    missing = 0
    oof_path = ROOT / "docs/reports/verified_oof.json"
    oof = _load_json(oof_path) if oof_path.is_file() else {}
    if oof.get("evaluation") == "strict_grouped_out_of_fold":
        for row in oof.get("predictions") or []:
            if row.get("true") not in fam:
                continue
            bucket = str(row.get("bucket") or "unknown")
            source_key = "old" if bucket == "old" else "new"
            if source != "all" and source != source_key:
                continue
            path = str(_portable(row.get("path") or "")) if row.get("path") else ""
            if not path or path in done:
                continue
            if not Path(path).is_file():
                missing += 1
                continue
            if row.get("correct") and float(row.get("confidence") or 0) >= 0.85:
                continue
            out.append({"path": path, "file": row.get("file") or Path(path).name,
                        "folder": row.get("true"), "labeled": row.get("true"),
                        "pred": row.get("pred"), "conf": row.get("confidence"),
                        "top5": row.get("top5") or [],
                        "source": source_key, "fold": row.get("fold"),
                        "correct": bool(row.get("correct"))})
    else:
        # Backward compatibility until a strict OOF report exists.
        for key, rel, base in MISTAKE_SOURCES:
            if source != "all" and source != key:
                continue
            d = _load_json(ROOT / rel)
            for r in d.get("details") or []:
                if r.get("want") not in fam:
                    continue
                path = str(ROOT / base / r["folder"] / r["file"])
                if path in done:
                    continue
                out.append({"path": path, "file": r["file"], "folder": r["folder"],
                            "labeled": r["want"], "pred": r.get("got"),
                            "conf": r.get("conf"), "source": key, "correct": False})

    # 新上传批次的自动标注结果也进队列：它们不在评测集里，但同样需要人工确认。
    for r in feedback_store.list_auto_labels(limit=2000):
        path = str(_portable(r.get("path") or "")) if r.get("path") else ""
        if not path or path in done:
            continue
        if not Path(path).is_file():
            missing += 1
            continue
        leaf = Path(path).parent.name          # 上传时按 slug 建目录，目录名就是原标签
        if leaf not in SLUG_SET:
            continue
        if leaf not in fam:
            continue
        out.append({
            "path": path,
            "file": Path(path).name,
            "folder": "upload:" + str(r.get("batch") or ""),
            "labeled": leaf,
            "pred": r.get("prediction"),
            "conf": r.get("confidence"),
            "source": "upload",
        })

    if low_first:
        out.sort(key=lambda x: (x["conf"] if x["conf"] is not None else 1))
    return {
        "family": family, "candidates": fam + ["other", "uncertain"],
        "total": len(out), "done": len(done), "missing": missing,
        "evaluation_source": "strict_grouped_out_of_fold" if oof.get("evaluation") else "legacy_directory_replay",
        "items": out[:max(1, min(limit, 2000))],
    }


@router.post("/viz/api/relabel_plan_remove")
def remove_relabel_plan(payload: dict, request: Request):
    """从改标计划里移除（撤销用）。"""
    srcs = set(payload.get("srcs") or [])
    if not srcs:
        raise HTTPException(400, "srcs 不能为空")
    result = feedback_store.remove_plan(srcs, reviewer=_reviewer(request))
    _export_plan_snapshot()
    return result


TRAINABLE_LABELS = {meta["slug"] for meta in CLASS_MAP.values()}
EVIDENCE_STATUSES = {"visible", "partial", "not_visible", "unclear", "not_applicable"}
ACTION_EVIDENCE = {"drain", "fill", "none", "unclear", "not_applicable"}


@router.post("/viz/api/annotations")
def save_annotations(payload: dict, request: Request):
    """Save adjudicated labels and audit events; unknowns never enter class training."""
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items 不能为空")
    allowed_labels = TRAINABLE_LABELS | {"other", "uncertain"}
    saved = []
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(400, "items 必须是对象数组")
        label = str(item.get("label") or "")
        if label not in allowed_labels:
            raise HTTPException(400, f"未知标注类别: {label}")
        evidence_status = str(item.get("evidence_status") or "unclear")
        action_evidence = str(item.get("action_evidence") or "unclear")
        if evidence_status not in EVIDENCE_STATUSES:
            raise HTTPException(400, f"未知 evidence_status: {evidence_status}")
        if action_evidence not in ACTION_EVIDENCE:
            raise HTTPException(400, f"未知 action_evidence: {action_evidence}")
        note = str(item.get("note") or "")
        if len(note) > 1000:
            raise HTTPException(400, "note 最长 1000 字符")
        path = _safe(str(item.get("path") or ""))
        if path.suffix.lower() not in IMG_EXT:
            raise HTTPException(400, "只支持图像标注")
        confidence = item.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "confidence 必须为数字") from exc
            if not 0 <= confidence <= 1:
                raise HTTPException(400, "confidence 必须在 0 到 1 之间")
        saved.append(feedback_store.record_annotation({
            "path": str(path),
            "source_dataset": str(item.get("source_dataset") or ""),
            "source_label": str(item.get("source_label") or ""),
            "prediction": str(item.get("prediction") or ""),
            "confidence": confidence,
            "label": label,
            "status": "reviewed" if label not in ("uncertain", "other") else label,
            "note": note,
            "evidence_status": evidence_status,
            "action_evidence": action_evidence,
            "model_version": str(item.get("model_version") or ""),
            "taxonomy_version": "v1",
            "trainable_labels": sorted(TRAINABLE_LABELS),
        }, reviewer=_reviewer(request)))
    _export_plan_snapshot()
    return {"saved": saved, "count": len(saved),
            "training_ready": sum(1 for item in saved if item.get("training_ready"))}


@router.get("/viz/api/annotations")
def get_annotations(limit: int = 500):
    return {"items": feedback_store.list_annotations(max(1, min(limit, 2000)))}


@router.get("/viz/api/annotations/export")
def export_annotations():
    items = feedback_store.list_annotations(100000)
    output = io.StringIO(newline="")
    fields = ["image_path", "source_dataset", "source_label", "model_prediction",
              "model_confidence", "final_label", "status", "evidence_status",
              "action_evidence", "note", "reviewer",
              "model_version", "taxonomy_version", "updated_at"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(items)
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=reviewed_annotations.csv"})


@router.post("/viz/api/annotations/undo")
def undo_annotation(payload: dict, request: Request):
    path = _safe(str(payload.get("path") or ""))
    result = feedback_store.undo_annotation(str(path), reviewer=_reviewer(request))
    _export_plan_snapshot()
    return result


# ---------------- 模型版本 ----------------

def _manifest_ok(version_dir: Path) -> tuple[bool, str]:
    """校验 manifest 里每个文件的 sha256，避免切换到手改过的权重。"""
    man = _load_json(version_dir / "manifest.json")
    if not man:
        return False, "manifest.json 缺失或损坏"
    import hashlib
    for rel, meta in (man.get("files") or {}).items():
        f = version_dir / rel
        if not f.is_file():
            # onnx 只在导出/离线评测时用，运行时只加载 .pt；
            # 清理时删掉 onnx 不应挡住版本切换
            if rel.startswith("onnx/"):
                continue
            return False, f"{rel} 不存在"
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != meta.get("sha256"):
            return False, f"{rel} 校验失败"
    return True, "ok"


def _active_version() -> str | None:
    """当前线上版本：优先看 current 软链，其次看 manifest。

    容器里如果 models/current 被复制成普通目录（不是软链），只看 is_symlink()
    会得到 None，删除校验就会误以为没有在用版本。
    """
    cur = ROOT / "models/current"
    if cur.is_symlink():
        return cur.resolve().name
    manifest = _load_json(cur / "manifest.json")
    return manifest.get("model_version") or None


def _models_writable() -> bool:
    """模型目录是否可写（只读挂载时删除/切换都会失败）。"""
    probe = ROOT / "models" / f".write_probe_{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@router.get("/viz/api/model/versions")
def model_versions():
    reg = _load_json(ROOT / "models/registry.json")
    active = _active_version()
    out = []
    for name, meta in (reg.get("versions") or {}).items():
        d = ROOT / "models" / name
        out.append({
            "version": name,
            "status": meta.get("status"),
            "created_at": meta.get("created_at"),
            "metrics": meta.get("metrics") or {},
            "notes": meta.get("notes") or "",
            "exists": d.is_dir(),
            "active": name == active,
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"active": active, "versions": out, "models_writable": _models_writable()}


@router.post("/viz/api/model/activate")
async def model_activate(payload: dict):
    """切换 models/current 指向。**需要重启 vision 服务才生效**。

    只改指针不动文件，且切换前校验 manifest 的 sha256。
    """
    name = str(payload.get("version") or "").strip()
    if not name or "/" in name or name.startswith("."):
        raise HTTPException(400, "版本名非法")
    reg_path = ROOT / "models/registry.json"
    reg = _load_json(reg_path)
    if name not in (reg.get("versions") or {}):
        raise HTTPException(404, f"未注册的版本: {name}")
    version_dir = ROOT / "models" / name
    if not version_dir.is_dir():
        raise HTTPException(404, f"版本目录不存在: {name}")
    ok, why = _manifest_ok(version_dir)
    if not ok:
        raise HTTPException(400, f"校验失败，拒绝切换：{why}")
    if not _models_writable():
        raise HTTPException(
            409,
            "模型目录是只读挂载，无法切换 current 软链。请把 api 的 models 挂载改成可写"
            "（../models:/app/models）后重建 api。")

    cur = ROOT / "models/current"
    try:
        if cur.is_symlink() or cur.exists():
            cur.unlink()
        cur.symlink_to(name)
    except OSError as exc:
        raise HTTPException(409, f"切换 current 失败（{exc}）。") from exc
    for v, meta in reg["versions"].items():
        meta["status"] = "active" if v == name else "retired"
    from datetime import datetime
    reg["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")

    # 直接让 vision 服务热加载，不需要重启容器
    reload_ok, reload_msg = False, ""
    try:
        r = await asyncio.to_thread(
            httpx.post, f"{settings.vision_url.rstrip('/')}/v1/reload", timeout=120)
        data = r.json() if r.status_code < 500 else {}
        reload_ok = r.status_code == 200 and data.get("ok") is True
        reload_msg = data.get("message") or ("" if reload_ok else f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        reload_msg = f"{type(exc).__name__}: {exc}"

    return {"active": name, "reloaded": reload_ok,
            "restart_required": not reload_ok,
            "message": "已生效" if reload_ok else f"已切换但热加载失败：{reload_msg}"}


# ---------------- 数据集上传 + 自动标注 ----------------

UPLOAD_ROOT = ROOT / "data/raw/uploads"
# 上传的图要能被 /viz/api/file 读到、能被标注队列扫到，所以纳入白名单
if UPLOAD_ROOT not in ALLOWED_ROOTS:
    ALLOWED_ROOTS.append(UPLOAD_ROOT)
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024        # 2 GB
MAX_ENTRIES = 20000
NAME_TO_SLUG = {name: meta["slug"] for name, meta in CLASS_MAP.items()}
SLUG_SET = set(NAME_TO_SLUG.values())


def _safe_zip_name(name: str) -> str | None:
    """防 zip-slip：只接受相对路径，不允许 ..、绝对路径、盘符。"""
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts):
        return None
    if p.suffix.lower() not in IMG_EXT:
        return None
    return "/".join(p.parts)


def _class_of_member(rel: str) -> str | None:
    """从 zip 内的相对路径推断类别：认目录名（中文名或 slug）。"""
    parts = PurePosixPath(rel).parts[:-1]
    for part in reversed(parts):
        if part in NAME_TO_SLUG:
            return NAME_TO_SLUG[part]
        if part in SLUG_SET:
            return part
    return None


def _batch_dir(batch: str) -> Path:
    """上传批次的目录；名字做穿越校验，不存在时返回 404。"""
    name = str(batch or "").strip()
    if not name or "/" in name or name.startswith("."):
        raise HTTPException(400, "批次名非法")
    dest = UPLOAD_ROOT / name
    if not dest.is_dir():
        raise HTTPException(404, f"批次不存在或已删除: {name}")
    return dest


@router.get("/viz/api/dataset/training")
def dataset_training():
    """已准备好的训练集：train/val/test 张数、比例、来源。"""
    return {"datasets": dataset_service.list_datasets()}


@router.post("/viz/api/dataset/build")
def dataset_build(payload: dict):
    """把历史数据或某个上传批次切成 train/val/test 并生成 manifest。

    默认 70/15/15，按工单整组切分（同一工单的照片不会同时出现在训练和测试）。
    """
    source = str(payload.get("source") or "all")
    include_uploads = bool(payload.get("include_uploads", False))
    ratios = {}
    for key in ("train", "val", "test"):
        if payload.get(key) is not None:
            try:
                ratios[key] = float(payload[key])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{key} 必须是数字")
    try:
        manifest = dataset_service.build_dataset(
            source=source, name=str(payload.get("name") or ""), ratios=ratios or None,
            seed=int(payload.get("seed") or 42), include_uploads=include_uploads)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {k: manifest.get(k) for k in
            ("name", "created_at", "source", "ratios", "counts", "total",
             "orders", "classes", "warnings", "path")}


@router.delete("/viz/api/dataset/training/{name}")
def dataset_training_delete(name: str):
    try:
        return dataset_service.delete_dataset(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "数据集不存在") from exc


@router.post("/viz/api/dataset/cleanup")
def dataset_cleanup():
    """清理文件已不存在的自动标注记录（换机/手动删批次后常见）。"""
    removed = feedback_store.prune_missing_auto_labels()
    return {"removed": removed}


@router.get("/viz/api/dataset/batches")
def dataset_batches():
    out = []
    if UPLOAD_ROOT.is_dir():
        for d in sorted(UPLOAD_ROOT.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            meta = _load_json(d / "meta.json")
            if meta:
                out.append({**meta, "batch": d.name, "exists": True})
    # 数据库里有、磁盘上目录没了的旧批次：提示用户清理，而不是在队列里报“图片不存在”
    orphans = []
    for batch, count in feedback_store.auto_label_batches().items():
        if not (UPLOAD_ROOT / batch).is_dir():
            orphans.append({"batch": batch, "count": count})
    return {"batches": out, "orphans": orphans,
            "orphan_images": sum(o["count"] for o in orphans)}


@router.get("/viz/api/dataset/{batch}")
def dataset_detail(batch: str, limit: int = 500, offset: int = 0):
    """批次详情：每张图的目录标签、自动标注、人工结论，供页面逐张复核。"""
    dest = _batch_dir(batch)
    meta = _load_json(dest / "meta.json")
    auto = {str(_portable(r["path"])): r for r in
            feedback_store.list_auto_labels(batch=batch, limit=5000)}
    ann = {}
    for path, row in feedback_store.annotations_for_batch(batch).items():
        ann[str(_portable(path))] = row

    files = [p for p in sorted(dest.rglob("*"))
             if p.is_file() and p.suffix.lower() in IMG_EXT]
    total = len(files)
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    items = []
    for p in files[offset:offset + limit]:
        key = str(p)
        row = auto.get(key) or {}
        a = ann.get(key) or {}
        dir_label = p.parent.name
        final = a.get("final_label") or dir_label
        items.append({
            "path": key,
            "name": p.name,
            "url": f"/viz/api/file?path={key}",
            "dir_label": dir_label,
            "auto_label": row.get("prediction") or "",
            "confidence": row.get("confidence"),
            "final_label": final,
            "reviewed": bool(a),
            "corrected": bool(a) and final != dir_label,
        })

    reviewed = sum(1 for p in files if str(p) in ann)
    corrected = sum(1 for p in files
                    if str(p) in ann and (ann[str(p)].get("final_label") or p.parent.name) != p.parent.name)
    return {
        "batch": batch,
        "meta": meta,
        "total": total,
        "offset": offset,
        "limit": limit,
        "stats": {
            "images": total,
            "auto_labeled": len([p for p in files if str(p) in auto]),
            "reviewed": reviewed,
            "corrected": corrected,
            "classes": dict(Counter(p.parent.name for p in files)),
        },
        "classes": [{"slug": meta["slug"],
                     "name": dataset_service.LABEL_CN.get(meta["slug"], name)}
                    for name, meta in CLASS_MAP.items()],
        "items": items,
    }


@router.post("/viz/api/dataset/{batch}/labels")
def dataset_save_labels(batch: str, payload: dict, request: Request):
    """保存人工确认/修正的标签；训练集构建时会优先采用这些结论。"""
    dest = _batch_dir(batch)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(400, "items 不能为空")
    accepted = []
    for item in raw_items:
        label = str(item.get("label") or "")
        if label not in SLUG_SET and label not in ("uncertain", "exclude"):
            raise HTTPException(422, f"未知标签: {label}")
        try:
            path = _safe(str(item.get("path") or ""))
        except HTTPException:
            continue
        if not path.is_relative_to(dest.resolve()):
            continue
        accepted.append({"path": str(path), "label": label,
                         "source_label": path.parent.name})
    saved = feedback_store.set_manual_labels(batch, accepted, reviewer=_reviewer(request))
    return {"saved": saved, "total": len(accepted)}


@router.delete("/viz/api/dataset/{batch}")
def dataset_delete(batch: str):
    """删除上传批次：目录 + 自动标注 + 人工结论一起删，避免队列里留幽灵记录。"""
    import shutil

    name = batch.strip()
    if not name or "/" in name or name.startswith("."):
        raise HTTPException(400, "批次名非法")
    dest = UPLOAD_ROOT / name
    existed = dest.is_dir()
    if existed:
        shutil.rmtree(dest)
    # 处理后的 8:2 临时目录也一并清掉
    processed = ROOT / "data" / "processed" / "uploads" / name
    if processed.is_dir():
        shutil.rmtree(processed, ignore_errors=True)
    records = feedback_store.delete_batch(name)
    return {"deleted": name, "existed": existed, **records}


@router.post("/viz/api/dataset/upload")
async def dataset_upload(file: UploadFile = File(...)):
    """收一个 zip：目录名映射到 12 类，图片落盘到 data/raw/uploads/<batch>/<slug>/。

    返回每个类的张数、无法归类的原因，供前端立刻看到"这批数据能不能用"。
    """
    import shutil
    import zipfile

    name = file.filename or "upload.zip"
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "只接受 zip（里面按类别建目录）")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch = f"upload_{stamp}"
    dest = UPLOAD_ROOT / batch
    tmp = UPLOAD_ROOT / f".{batch}.zip"
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    written = skipped_dir = skipped_type = 0
    total = 0
    try:
        with tmp.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "压缩包超过 2GB")
                fh.write(chunk)

        counts: Counter = Counter()
        unmapped: Counter = Counter()
        with zipfile.ZipFile(tmp) as zf:
            members = zf.infolist()
            if len(members) > MAX_ENTRIES:
                raise HTTPException(400, f"条目过多（{len(members)}）")
            for info in members:
                if info.is_dir():
                    continue
                rel = _safe_zip_name(info.filename)
                if rel is None:
                    skipped_type += 1
                    continue
                slug = _class_of_member(rel)
                if slug is None:
                    top = PurePosixPath(rel).parts[0] if len(PurePosixPath(rel).parts) > 1 else "（根目录）"
                    unmapped[top] += 1
                    continue
                target = dest / slug / PurePosixPath(rel).name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                counts[slug] += 1
                written += 1
    except zipfile.BadZipFile as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(400, "压缩包损坏") from exc
    finally:
        tmp.unlink(missing_ok=True)

    if written == 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(
            400,
            "没有可用的图片。zip 里请按类别建目录，例如 `01_桥放油/*.jpg` 或 `bridge_drain/*.jpg`")

    meta = {
        "batch": batch,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "original_name": name,
        "images": written,
        "bytes": total,
        "classes": dict(counts),
        "unmapped_top_dirs": dict(unmapped.most_common(10)),
        "skipped": {"non_image": skipped_type},
        "path": str(dest),
    }
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


@router.post("/viz/api/dataset/{batch}/autolabel")
async def dataset_autolabel(batch: str, limit: int = 500):
    """用当前模型给这批图打预标注，结果入库供人工复核。"""
    if "/" in batch or batch.startswith("."):
        raise HTTPException(400, "批次名非法")
    dest = UPLOAD_ROOT / batch
    if not dest.is_dir():
        raise HTTPException(404, "批次不存在")

    files = [p for p in sorted(dest.rglob("*")) if p.is_file() and p.suffix.lower() in IMG_EXT]
    if not files:
        raise HTTPException(400, "这批没有图片")
    files = files[:max(1, min(limit, 2000))]

    version = ""
    try:
        ready = httpx.get(f"{settings.vision_url}/healthz", timeout=5).json()
        version = str(ready.get("model_version") or "")
    except Exception:  # noqa: BLE001
        pass

    items, failed = [], 0
    for p in files:
        try:
            with p.open("rb") as fh:
                r = httpx.post(f"{settings.vision_url}/v1/scene",
                               files={"image": (p.name, fh, "image/png")}, timeout=60)
            # 404/503 也会返回 JSON，必须显式判状态码，否则会把空预测当成功写库
            if r.status_code != 200:
                failed += 1
                continue
            d = r.json()
            label = d.get("class")
            if not label:
                failed += 1
                continue
            items.append({"path": str(p), "prediction": label,
                          "confidence": d.get("confidence")})
            version = version or str(d.get("model_version") or "")
        except Exception:  # noqa: BLE001
            failed += 1

    saved = feedback_store.save_auto_labels(batch, items, version)
    stats = feedback_store.auto_label_stats(batch)
    return {"batch": batch, "saved": saved, "failed": failed,
            "model_version": version, **stats}


@router.post("/viz/api/model/delete")
def model_delete(payload: dict):
    """删除一个模型版本（登记 + 文件）。当前在用的版本不允许删。"""
    import shutil

    name = str(payload.get("version") or "").strip()
    if not name or "/" in name or name.startswith("."):
        raise HTTPException(400, "版本名非法")

    reg_path = ROOT / "models/registry.json"
    reg = _load_json(reg_path)
    if name not in (reg.get("versions") or {}):
        raise HTTPException(404, f"未注册的版本: {name}")

    active = _active_version()
    if name == active:
        raise HTTPException(400, "不能删除正在使用的版本，请先切到别的版本")

    version_dir = ROOT / "models" / name
    delete_files = bool(payload.get("delete_files", True))
    if delete_files:
        if not _models_writable():
            raise HTTPException(
                409,
                "模型目录是只读挂载，权重删不掉。请在服务器上把 "
                "deploy/docker-compose.yml 里 api 的 ../models:/app/models:ro "
                "改成 ../models:/app/models 后重建 api；"
                "或选择“仅从列表移除”（delete_files=false），文件保留。")
        if version_dir.is_dir():
            try:
                shutil.rmtree(version_dir)
            except OSError as exc:
                raise HTTPException(
                    409, f"删除权重目录失败（{exc}）。可改为仅从列表移除，文件保留。") from exc

    reg["versions"].pop(name, None)
    from datetime import datetime
    reg["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            409, f"模型注册表不可写（{exc}）。请检查 models 目录挂载权限。") from exc
    return {"deleted": name, "remaining": sorted(reg["versions"]),
            "files_removed": not version_dir.is_dir()}


@router.post("/viz/api/model/register")
def model_register(payload: dict):
    """把一次训练产出的权重登记为候选版本（不自动上线）。

    scene 头用新训练的，其余头沿用当前版本——避免把没重训的头一起换掉。
    """
    import hashlib
    import shutil

    version = str(payload.get("version") or "").strip()
    weights = str(payload.get("weights") or "").strip()
    if not version or "/" in version or version.startswith("."):
        raise HTTPException(400, "版本名非法（不能含 /，不能以 . 开头）")
    if not version.startswith("v") or "." not in version:
        raise HTTPException(400, "版本名请用 vX.Y.Z 形式")
    src = Path(weights)
    if not src.is_file():
        raise HTTPException(404, f"权重不存在: {weights}")

    dest = ROOT / "models" / version
    if dest.exists():
        raise HTTPException(409, f"版本 {version} 已存在")
    cur = ROOT / "models" / "current"
    if not cur.is_dir():
        raise HTTPException(500, "models/current 不可用")

    (dest / "weights").mkdir(parents=True)
    shutil.copy(src, dest / "weights" / "scene.pt")
    for other in ("group.pt", "action.pt", "bin_axle.pt", "bin_tire.pt",
                  "bin_filter.pt", "det.pt"):
        s = cur / "weights" / other
        if s.is_file():
            shutil.copy(s, dest / "weights" / other)

    files = {}
    for p in sorted((dest / "weights").glob("*.pt")):
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        files[f"weights/{p.name}"] = {"sha256": h.hexdigest(), "bytes": p.stat().st_size}

    manifest = {
        "model_version": version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roles": {"scene": "12 类场景分类", "group": "3 类油液分组", "action": "6 类动作",
                  "bin_axle": "桥 加油/放油", "bin_tire": "轮胎 加油/放油",
                  "bin_filter": "滤芯 有/无", "det": "18 类检测器"},
        "source_dir": str(src),
        "files": files,
        "metrics": payload.get("metrics") or {},
        "notes": str(payload.get("notes") or "由平台训练任务注册的候选版本"),
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")

    reg_path = ROOT / "models" / "registry.json"
    reg = _load_json(reg_path)
    reg.setdefault("versions", {})[version] = {
        "status": "candidate",
        "created_at": manifest["created_at"],
        "n_files": len(files),
        "metrics": manifest["metrics"],
        "notes": manifest["notes"],
    }
    reg["updated_at"] = manifest["created_at"]
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"registered": version, "status": "candidate", "files": len(files)}
