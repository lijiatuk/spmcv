"""训练任务：从平台发起训练，不用再登服务器敲命令。

设计取舍：
  - **同一时间只允许一个训练任务**：训练会吃满 CPU/GPU，并发只会互相拖死。
  - 任务状态落 JSON 文件（`var/train_jobs/`），进程重启后仍能看到结果。
  - 训练完成后**不自动上线**，只登记为 candidate，由人确认后切版本。
  - 数据来源两种：`union`（已整理的合并数据集）或 `upload:<batch>`（页面上传的批次）。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import BASE_DIR
from app.api.viz import UPLOAD_ROOT, IMG_EXT, CLASS_MAP, _load_json

router = APIRouter(tags=["train"])

ROOT = BASE_DIR
JOBS = ROOT / "var" / "train_jobs"
RUNS = ROOT / "var" / "runs"
PRETRAINED = ROOT / "models" / "pretrained" / "yolo26n-cls.pt"
UNION_CLS = ROOT / "data" / "processed" / "union" / "cls"

SLUG_TO_DIR = {meta["slug"]: name for name, meta in CLASS_MAP.items()}


def _job_path(job_id: str) -> Path:
    return JOBS / f"{job_id}.json"


def _active_job() -> dict | None:
    if not JOBS.is_dir():
        return None
    for p in sorted(JOBS.glob("*.json"), reverse=True):
        d = _load_json(p)
        if d and d.get("status") in ("running", "preparing"):
            pid = d.get("pid")
            if pid and not _alive(pid):
                # 进程退出不一定是失败：先看产物有没有落盘
                best = RUNS / str(d.get("run_name")) / "weights" / "best.pt"
                d["status"] = "completed" if best.is_file() else "failed"
                d["weights"] = str(best) if best.is_file() else ""
                if d["status"] == "failed":
                    d["error"] = "进程已退出且没有产出权重（可能是内存不足或崩溃）"
                p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
            return d
    return None


# 子进程句柄：Popen 不 wait 会留下僵尸，os.kill(pid,0) 仍然成功，
# 状态就会永远停在 running。用 poll() 才能拿到真实退出状态。
_PROCS: dict[str, subprocess.Popen] = {}


def _alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _prepare_upload_dataset(batch: str) -> Path:
    """把上传批次按 8:2 切成 train/val，供分类训练使用。"""
    src = UPLOAD_ROOT / batch
    if not src.is_dir():
        raise HTTPException(404, f"上传批次不存在: {batch}")
    dest = ROOT / "data" / "processed" / "uploads" / batch
    if dest.exists():
        shutil.rmtree(dest)
    rng = random.Random(42)
    total = 0
    for slug_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        slug = slug_dir.name
        if slug not in SLUG_TO_DIR:
            continue
        files = [f for f in slug_dir.iterdir() if f.suffix.lower() in IMG_EXT]
        if len(files) < 2:
            continue
        rng.shuffle(files)
        cut = max(1, int(len(files) * 0.8))
        for part, chunk in (("train", files[:cut]), ("val", files[cut:] or files[-1:])):
            d = dest / part / slug
            d.mkdir(parents=True, exist_ok=True)
            for f in chunk:
                target = d / f.name
                if not target.exists():
                    target.symlink_to(f)
                total += 1
    if total == 0:
        raise HTTPException(400, "这批数据没有可训练的类别目录")
    return dest


@router.post("/viz/api/train/start")
def train_start(payload: dict):
    """发起训练。同一时间只允许一个任务。"""
    if _active_job():
        raise HTTPException(409, "已有训练任务在跑，等它结束或先取消")

    dataset = str(payload.get("dataset") or "union")
    epochs = int(payload.get("epochs") or 25)
    if not 1 <= epochs <= 200:
        raise HTTPException(422, "epochs 需在 1–200")
    imgsz = int(payload.get("imgsz") or 512)
    fold = int(payload.get("fold") or 0)

    if dataset == "union":
        data_dir = UNION_CLS / f"fold{fold}"
        if not data_dir.is_dir():
            raise HTTPException(
                404, f"找不到 {data_dir}，先在服务器上跑 scripts/build_union.py --fold {fold}")
    elif dataset.startswith("upload:"):
        data_dir = _prepare_upload_dataset(dataset.split(":", 1)[1])
    else:
        raise HTTPException(422, "dataset 只能是 union 或 upload:<批次>")

    # 训练要 ultralytics + torch。API 镜像默认不带（镜像小一半），
    # 没装就明确告知，而不是等子进程报 ModuleNotFoundError。
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        raise HTTPException(
            503,
            "当前运行环境没有 ultralytics，无法训练。"
            "请在 vision 容器或装了 requirements-vision.txt 的环境里运行，"
            "或设置 TRAIN_BACKEND=remote 指向独立训练服务。")

    if not PRETRAINED.is_file():
        raise HTTPException(500, f"缺少预训练权重: {PRETRAINED}（离线环境需提前放好）")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"train_{stamp}"
    JOBS.mkdir(parents=True, exist_ok=True)
    log_path = JOBS / f"{name}.log"
    job = {
        "job_id": name,
        "status": "preparing",
        "dataset": dataset,
        "data_dir": str(data_dir),
        "epochs": epochs,
        "imgsz": imgsz,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "log": str(log_path),
        "run_name": name,
    }

    cmd = [sys.executable, str(ROOT / "scripts" / "train_scene_cls.py"),
           "--model", str(PRETRAINED),
           "--data", str(data_dir),
           "--epochs", str(epochs),
           "--imgsz", str(imgsz),
           "--batch", str(int(payload.get("batch") or 8)),
           "--name", name,
           "--project", str(RUNS)]
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with log_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)

    job.update({"status": "running", "pid": proc.pid, "cmd": " ".join(cmd)})
    _PROCS[name] = proc
    _job_path(name).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


@router.get("/viz/api/train/status")
def train_status(job_id: str = ""):
    """查看任务进度：从 results.csv 读 epoch，从日志尾部读最近输出。"""
    if job_id:
        job = _load_json(_job_path(job_id))
        if not job:
            raise HTTPException(404, "任务不存在")
    else:
        active = _active_job()
        if active:
            job = active
        else:
            files = sorted(JOBS.glob("*.json"), reverse=True) if JOBS.is_dir() else []
            if not files:
                return {"job": None}
            job = _load_json(files[0]) or {}

    job_id = job.get("job_id", "")
    pid = job.get("pid")
    proc = _PROCS.get(job_id)
    if proc is not None:
        running = proc.poll() is None and job.get("status") == "running"
    else:
        # 服务重启后没有句柄：用产物是否落盘判断，避免僵尸 pid 误判
        best_now = RUNS / str(job.get("run_name")) / "weights" / "best.pt"
        running = bool(pid) and _alive(pid) and job.get("status") == "running" and not best_now.is_file()

    epoch, total, top1 = 0, job.get("epochs", 0), None
    csv_path = RUNS / str(job.get("run_name")) / "results.csv"
    if csv_path.is_file():
        try:
            lines = [ln for ln in csv_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(lines) > 1:
                epoch = len(lines) - 1
                header = [h.strip() for h in lines[0].split(",")]
                last = [v.strip() for v in lines[-1].split(",")]
                if "metrics/accuracy_top1" in header:
                    top1 = round(float(last[header.index("metrics/accuracy_top1")]), 4)
        except Exception:  # noqa: BLE001
            pass

    tail = ""
    log_path = Path(job.get("log") or "")
    if log_path.is_file():
        try:
            raw = log_path.read_text(encoding="utf-8", errors="replace")
            # ultralytics 进度条用 \r，取最后几段有效行
            parts = [p.strip() for p in raw.replace("\r", "\n").split("\n") if p.strip()]
            tail = "\n".join(parts[-8:])[-1500:]
        except Exception:  # noqa: BLE001
            pass

    if job.get("status") == "running" and not running:
        best = RUNS / str(job.get("run_name")) / "weights" / "best.pt"
        job["status"] = "completed" if best.is_file() else "failed"
        if proc is not None and proc.returncode not in (0, None):
            job["status"] = "failed"
            job["error"] = f"训练进程退出码 {proc.returncode}"
        job["weights"] = str(best) if best.is_file() else ""
        _job_path(job_id).write_text(json.dumps(job, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    return {"job": {**job, "epoch": epoch, "top1": top1}, "running": running,
            "log_tail": tail}


@router.post("/viz/api/train/cancel")
def train_cancel(payload: dict):
    job_id = str(payload.get("job_id") or "")
    job = _load_json(_job_path(job_id))
    if not job:
        raise HTTPException(404, "任务不存在")
    pid = job.get("pid")
    if pid and _alive(pid):
        try:
            os.killpg(os.getpgid(int(pid)), 15)
        except OSError:
            os.kill(int(pid), 15)
        time.sleep(1)
    job["status"] = "cancelled"
    _job_path(job_id).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"cancelled": job_id}
