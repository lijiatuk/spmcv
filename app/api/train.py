"""训练任务：从平台发起训练，不用再登服务器敲命令。

设计取舍：
  - **同一时间只允许一个训练任务**：训练会吃满 CPU/GPU，并发只会互相拖死。
  - 任务状态落 JSON 文件（`var/train_jobs/`），进程重启后仍能看到结果。
  - 训练完成后**不自动上线**，只登记为 candidate，由人确认后切版本。
  - 数据来源：`all`（新旧合并，自动 70/15/15）、`built:<名称>`（已生成的数据集）
    或 `upload:<batch>`（某个上传批次，按同样规则切分）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.viz import _load_json
from app.config import BASE_DIR
from app.services import datasets as dataset_service

router = APIRouter(tags=["train"])

ROOT = BASE_DIR
JOBS = ROOT / "var" / "train_jobs"
RUNS = ROOT / "var" / "runs"
PRETRAINED = ROOT / "models" / "pretrained" / "yolo26n-cls.pt"
UNION_CLS = ROOT / "data" / "processed" / "union" / "cls"
DATASETS = ROOT / "data" / "processed" / "datasets"

def _job_path(job_id: str) -> Path:
    return JOBS / f"{job_id}.json"


def _is_test_job(job: dict) -> bool:
    """1–2 轮基本是冒烟测试，不该出现在生产“当前训练”里。"""
    try:
        return int(job.get("epochs") or 0) <= 2
    except (TypeError, ValueError):
        return False


def _ratios(payload: dict) -> dict | None:
    ratios = {}
    for key in ("train", "val", "test"):
        if payload.get(key) is not None:
            try:
                ratios[key] = float(payload[key])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{key} 必须是数字")
    return ratios or None


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


@router.post("/viz/api/train/start")
def train_start(payload: dict):
    """发起训练。同一时间只允许一个任务。"""
    if _active_job():
        raise HTTPException(409, "已有训练任务在跑，等它结束或先取消")

    dataset = str(payload.get("dataset") or "all")
    epochs = int(payload.get("epochs") or 25)
    if not 1 <= epochs <= 200:
        raise HTTPException(422, "epochs 需在 1–200")
    imgsz = int(payload.get("imgsz") or 512)
    fold = int(payload.get("fold") or 0)
    device = str(payload.get("device") or os.environ.get("TRAIN_DEVICE") or "auto")

    # 数据集统一走 datasets.build_dataset：按工单分组、70/15/15、相对软链，
    # 人工改过的标签会覆盖目录标签。页面不需要先登服务器跑脚本。
    dataset_meta: dict = {}
    try:
        if dataset.startswith("built:"):
            name = dataset.split(":", 1)[1]
            data_dir = DATASETS / name
            dataset_meta = _load_json(data_dir / "manifest.json")
        elif dataset.startswith("upload:"):
            dataset_meta = dataset_service.build_dataset(
                source=dataset, include_uploads=False,
                ratios=_ratios(payload))
            data_dir = Path(dataset_meta["path"])
        elif dataset == "union" and (UNION_CLS / f"fold{fold}").is_dir():
            # 兼容旧部署：已经生成过的 5 折目录继续可用
            data_dir = UNION_CLS / f"fold{fold}"
            dataset_meta = {"name": f"union_fold{fold}", "counts": {}}
        elif dataset in ("all", "union", "auto"):
            dataset_meta = dataset_service.build_dataset(
                source="all", include_uploads=bool(payload.get("include_uploads", True)),
                ratios=_ratios(payload))
            data_dir = Path(dataset_meta["path"])
        else:
            raise HTTPException(422, "dataset 只能是 all / built:<名称> / upload:<批次>")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    for part in ("train", "val"):
        if not (data_dir / part).is_dir():
            raise HTTPException(500, f"数据集缺少 {part} 目录: {data_dir}")

    # 训练要 ultralytics + torch。API 镜像用 WITH_TRAIN 控制是否安装；
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
        "dataset_name": dataset_meta.get("name") or data_dir.name,
        "dataset_counts": dataset_meta.get("counts") or {},
        "dataset_hash": dataset_meta.get("content_hash") or "",
        "epochs": epochs,
        "imgsz": imgsz,
        "device": device,
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
           "--device", device,
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
            # 生产页面不展示 1–2 轮的测试跑（历史上留过 epoch 1/1、top1 100% 的记录）。
            files = [p for p in files if not _is_test_job(_load_json(p))]
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

    test = {}
    test_path = RUNS / str(job.get("run_name")) / "test.json"
    if test_path.is_file():
        test = _load_json(test_path)
        job["test"] = test
    return {"job": {**job, "epoch": epoch, "top1": top1, "test": test},
            "running": running, "log_tail": tail}


@router.post("/viz/api/train/cleanup_tests")
def train_cleanup_tests():
    """清理所有 1–2 轮的测试训练记录和 run 目录。"""
    import shutil

    removed = []
    if JOBS.is_dir():
        for p in sorted(JOBS.glob("*.json")):
            job = _load_json(p)
            if not job or not _is_test_job(job):
                continue
            pid = job.get("pid")
            if pid and _alive(pid):
                continue
            p.unlink(missing_ok=True)
            run = RUNS / str(job.get("run_name") or job.get("job_id") or "")
            if run.is_dir():
                shutil.rmtree(run, ignore_errors=True)
            removed.append(job.get("job_id") or p.stem)
    return {"removed": removed}


@router.post("/viz/api/train/delete")
def train_delete(payload: dict):
    """清除一条训练记录（测试跑出来的 epoch 1/100% 不该一直留在生产页面）。"""
    import shutil

    job_id = str(payload.get("job_id") or "").strip()
    if not job_id or "/" in job_id or job_id.startswith("."):
        raise HTTPException(400, "job_id 非法")
    job = _load_json(_job_path(job_id))
    if not job:
        raise HTTPException(404, "任务不存在")
    pid = job.get("pid")
    if pid and _alive(pid):
        raise HTTPException(409, "任务还在运行，请先取消")
    _job_path(job_id).unlink(missing_ok=True)
    if payload.get("delete_run", True):
        run = RUNS / str(job.get("run_name") or job_id)
        if run.is_dir():
            shutil.rmtree(run, ignore_errors=True)
    return {"deleted": job_id}


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
