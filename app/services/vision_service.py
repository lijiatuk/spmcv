#!/usr/bin/env python3
"""Vision service: YOLO scene classifier (+ optional detector).

Run:
    VISION_WEIGHTS=var/runs/cls_v1/weights/best.pt \
    uvicorn services.vision_service:app --host 127.0.0.1 --port 8016
"""
from __future__ import annotations

import asyncio
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageOps

from app.limits import read_image

# 权重解析顺序（优先级从高到低）：
#   1. 单个显式 env（VISION_WEIGHTS 等）—— 兼容旧部署与临时实验
#   2. MODEL_DIR/weights/<role>.pt —— 推荐，配合 models/current 软链做版本切换
#   3. 历史默认路径 —— 只在本地开发时可能命中
MODEL_DIR = os.getenv("MODEL_DIR", "")


def _weights(role: str, env_name: str, legacy: str) -> str:
    explicit = os.getenv(env_name, "")
    if explicit:
        return explicit
    if MODEL_DIR:
        candidate = os.path.join(MODEL_DIR, "weights", f"{role}.pt")
        if os.path.isfile(candidate):
            return candidate
    return legacy


CLS_WEIGHTS = _weights("scene", "VISION_WEIGHTS", "var/runs/cls_v1/weights/best.pt")
DET_WEIGHTS = _weights("det", "VISION_DET_WEIGHTS", "")
ACTION_WEIGHTS = _weights("action", "VISION_ACTION_WEIGHTS", "var/runs/action_v4/weights/best.pt")
GROUP_WEIGHTS = _weights("group", "VISION_GROUP_WEIGHTS", "var/runs/group_v1/weights/best.pt")
BINARY_WEIGHTS = {
    "axle": _weights("bin_axle", "VISION_BIN_AXLE", "var/runs/bin3_axle/weights/best.pt"),
    "tire": _weights("bin_tire", "VISION_BIN_TIRE", "var/runs/bin3_filldrain_tire/weights/best.pt"),
    "filter": _weights("bin_filter", "VISION_BIN_FILTER", "var/runs/bin3_filter_state/weights/best.pt"),
}
def _default_device() -> str:
    """按可用性自动选设备。

    之前写死 `mps`：本地 Mac 能跑，**部署到 Linux/L20 上会直接失败**。
    `VISION_DEVICE` 显式设置时仍然优先。
    """
    explicit = os.getenv("VISION_DEVICE", "").strip()
    if explicit:
        return explicit
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 - 设备探测失败就退回 CPU
        pass
    return "cpu"


DEVICE = _default_device()
IMGSZ = int(os.getenv("VISION_IMGSZ", "640"))
ACTION_IMGSZ = int(os.getenv("VISION_ACTION_IMGSZ", "512"))
LOAD_AUXILIARY = os.getenv("VISION_LOAD_AUX", "0").strip().lower() in {"1", "true", "yes", "on"}

CLS_MODEL = None
DET_MODEL = None
ACTION_MODEL = None
GROUP_MODEL = None
BINARY_MODELS = {}
MODEL_VERSION = os.getenv("MODEL_VERSION", "unregistered")

# 每个模型一把锁：不同头可以并行（分类头之间互不相干），
# 同一模型仍然串行（ultralytics 的 predict 不是线程安全的）。
# 原来一把全局锁把所有推理串成一条线，是 5 图工单 40s 的主因。
LOCKS = {name: threading.Lock() for name in
         ("scene", "det", "action", "group", "bin_axle", "bin_tire", "bin_filter")}
BIN_KEY = {"axle": "bin_axle", "tire": "bin_tire", "filter": "bin_filter"}

# 解码/后处理是 CPU 活，线程数给够；GPU 前向由锁保护
MAX_DECODE_WORKERS = int(os.getenv("VISION_DECODE_WORKERS", "8"))


def _verify_manifest() -> None:
    """MODEL_DIR 下若有 manifest.json，启动时校验 sha256。"""
    global MODEL_VERSION
    if not MODEL_DIR:
        return
    manifest_path = os.path.join(MODEL_DIR, "manifest.json")
    if not os.path.isfile(manifest_path):
        return
    import hashlib
    import json as _json

    data = _json.loads(open(manifest_path, encoding="utf-8").read())
    MODEL_VERSION = str(data.get("model_version") or MODEL_VERSION)
    bad = []
    for rel, meta in (data.get("files") or {}).items():
        path = os.path.join(MODEL_DIR, rel)
        if not os.path.isfile(path):
            # onnx 只在导出时用，运行时不加载；缺失不算错误
            if rel.startswith("onnx/"):
                continue
            bad.append((rel, "缺失"))
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != meta.get("sha256"):
            bad.append((rel, "sha256 不匹配"))
    if bad:
        raise RuntimeError(f"模型文件校验失败，拒绝启动: {bad}")
    print(f"[vision] manifest 校验通过（{data.get('model_version')}）", flush=True)


def _load_models():
    global CLS_MODEL, DET_MODEL, ACTION_MODEL, GROUP_MODEL
    from ultralytics import YOLO

    _verify_manifest()

    # 缺失权重必须启动失败：静默降级会让容器「健康」地返回垃圾结果。
    required = {"scene": CLS_WEIGHTS}
    if LOAD_AUXILIARY:
        required.update({
            "action": ACTION_WEIGHTS,
            "group": GROUP_WEIGHTS,
            **{f"bin_{k}": v for k, v in BINARY_WEIGHTS.items()},
        })
    missing = [f"{k}={v}" for k, v in required.items() if not v or not os.path.isfile(v)]
    if missing:
        raise RuntimeError("权重缺失，拒绝启动: " + "; ".join(missing))

    CLS_MODEL = YOLO(CLS_WEIGHTS)
    if LOAD_AUXILIARY:
        ACTION_MODEL = YOLO(ACTION_WEIGHTS)
        GROUP_MODEL = YOLO(GROUP_WEIGHTS)
        for key, path in BINARY_WEIGHTS.items():
            BINARY_MODELS[key] = YOLO(path)

    # 检测器允许缺失（检测只是证据来源之一），但要说清楚
    if DET_WEIGHTS and os.path.isfile(DET_WEIGHTS):
        DET_MODEL = YOLO(DET_WEIGHTS)
    else:
        print(f"[vision] 警告：检测器未加载（{DET_WEIGHTS or '未配置'}），检测类证据将缺失",
              flush=True)

    loaded_heads = 1 + int(DET_MODEL is not None) + (5 if LOAD_AUXILIARY else 0)
    print(f"[vision] 已加载 {loaded_heads} 个头 aux={LOAD_AUXILIARY} "
          f"device={DEVICE} imgsz={IMGSZ}/{ACTION_IMGSZ}", flush=True)


@asynccontextmanager
def _warmup() -> None:
    """预热各头，避免第一个请求承担初始化开销。"""
    try:
        import numpy as np
        img = Image.fromarray(np.full((512, 512, 3), 128, dtype="uint8"))
        if CLS_MODEL is not None:
            _scene_sync(img)
        print("[vision] 预热完成", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] 预热失败（不影响启动）: {type(exc).__name__}: {exc}", flush=True)


async def lifespan(_: FastAPI):
    _load_models()
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=MAX_DECODE_WORKERS))
    await asyncio.to_thread(_warmup)
    yield


app = FastAPI(title="SPM Vision", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read(image_bytes: bytes) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")


@app.get("/healthz")
def healthz():
    return {
        "ok": CLS_MODEL is not None,
        "model_version": MODEL_VERSION,
        "auxiliary_enabled": LOAD_AUXILIARY,
        "classifier": CLS_WEIGHTS if CLS_MODEL is not None else None,
        "detector": DET_WEIGHTS if DET_MODEL is not None else None,
        "action_model": ACTION_WEIGHTS if ACTION_MODEL is not None else None,
        "group_model": GROUP_WEIGHTS if GROUP_MODEL is not None else None,
        "binary_heads": sorted(BINARY_MODELS),
        "device": DEVICE,
        "classes": list(getattr(CLS_MODEL, "names", {}).values()) if CLS_MODEL else [],
        "action_classes": list(getattr(ACTION_MODEL, "names", {}).values()) if ACTION_MODEL else [],
    }


def _scene_sync(img):
    with LOCKS["scene"]:
        return CLS_MODEL.predict(img, imgsz=IMGSZ, device=DEVICE, verbose=False)[0]


@app.post("/v1/scene")
async def scene(image: UploadFile = File(...)):
    if CLS_MODEL is None:
        raise HTTPException(503, f"classifier not loaded: {CLS_WEIGHTS}")
    img = _read(await read_image(image))
    result = await asyncio.to_thread(_scene_sync, img)
    probs = result.probs
    top1 = int(probs.top1)
    top5 = [int(i) for i in probs.top5]
    names = result.names
    return {
        "class": names[top1],
        "confidence": round(float(probs.top1conf), 4),
        "model_version": MODEL_VERSION,
        "top5": [
            {"class": names[i], "confidence": round(float(probs.data[i]), 4)}
            for i in top5
        ],
        # Full distribution: the orchestrator marginalises it onto oil groups.
        "probs": {names[i]: round(float(probs.data[i]), 5)
                  for i in range(len(probs.data))},
    }


def _action_sync(img):
    with LOCKS["action"]:
        return ACTION_MODEL.predict(img, imgsz=ACTION_IMGSZ, device=DEVICE, verbose=False)[0]


@app.post("/v1/action")
async def action(image: UploadFile = File(...)):
    if ACTION_MODEL is None:
        raise HTTPException(503, f"action model not loaded: {ACTION_WEIGHTS}")
    img = _read(await read_image(image))
    result = await asyncio.to_thread(_action_sync, img)
    probs = result.probs
    top1 = int(probs.top1)
    names = result.names
    return {
        "class": names[top1],
        "confidence": round(float(probs.top1conf), 4),
        "top5": [
            {"class": names[int(i)], "confidence": round(float(probs.data[i]), 4)}
            for i in top5_ids(probs)
        ],
    }


def top5_ids(probs):
    return [int(i) for i in probs.top5]


def _group_sync(img):
    with LOCKS["group"]:
        return GROUP_MODEL.predict(img, imgsz=ACTION_IMGSZ, device=DEVICE, verbose=False)[0]


@app.post("/v1/group")
async def group(image: UploadFile = File(...)):
    """3-way oil group head: axle_oil / tire_oil / filter."""
    if GROUP_MODEL is None:
        raise HTTPException(503, f"group model not loaded: {GROUP_WEIGHTS}")
    img = _read(await read_image(image))
    result = await asyncio.to_thread(_group_sync, img)
    probs = result.probs
    names = result.names
    return {
        "class": names[int(probs.top1)],
        "confidence": round(float(probs.top1conf), 4),
        "top5": [
            {"class": names[int(i)], "confidence": round(float(probs.data[i]), 4)}
            for i in probs.top5
        ],
    }


def _binary_sync(model, img, key: str):
    with LOCKS[BIN_KEY.get(key, "bin_filter")]:
        return model.predict(img, imgsz=ACTION_IMGSZ, device=DEVICE, verbose=False)[0]


@app.post("/v1/binary")
async def binary(image: UploadFile = File(...), group: str = Form(...)):
    """In-group 2-way head: axle/tire = fill|drain, filter = has|none."""
    model = BINARY_MODELS.get(group)
    if model is None:
        raise HTTPException(503, f"binary head not loaded: {group}")
    img = _read(await read_image(image))
    result = await asyncio.to_thread(_binary_sync, model, img, group)
    probs = result.probs
    names = result.names
    return {
        "class": names[int(probs.top1)],
        "confidence": round(float(probs.top1conf), 4),
        "top5": [
            {"class": names[int(i)], "confidence": round(float(probs.data[i]), 4)}
            for i in probs.top5
        ],
    }


def _detect_sync(img, conf):
    with LOCKS["det"]:
        return DET_MODEL.predict(img, imgsz=IMGSZ, device=DEVICE,
                                 conf=conf, verbose=False)[0]


@app.post("/v1/detect")
async def detect(image: UploadFile = File(...), conf: float = 0.25):
    if DET_MODEL is None:
        raise HTTPException(503, "detector not loaded")
    img = _read(await read_image(image))
    result = await asyncio.to_thread(_detect_sync, img, conf)
    names = result.names
    boxes = []
    for b in result.boxes:
        boxes.append({
            "label": names[int(b.cls)],
            "score": round(float(b.conf), 4),
            "box": [round(float(v), 1) for v in b.xyxy[0].tolist()],
        })
    return {"boxes": boxes}


@app.post("/v1/reload")
async def reload_models():
    """重新加载当前 `models/current` 指向的权重（热切换）。

    切换版本后无需重启容器：前端点「切换」→ 调这里 → 新权重立刻生效。
    加载期间用各模型锁挡住推理请求，避免半加载状态被读到。
    """
    acquired = []
    try:
        for lock in LOCKS.values():
            lock.acquire()
            acquired.append(lock)
        before = MODEL_VERSION
        await asyncio.to_thread(_load_models)
        return {"ok": True, "before": before, "after": MODEL_VERSION}
    except Exception as exc:  # noqa: BLE001
        # 加载失败不能让服务停在半死状态：保留旧权重继续服务
        return JSONResponse(status_code=500,
                            content={"ok": False,
                                     "message": f"重新加载失败，仍使用 {before}: {exc}"})
    finally:
        for lock in reversed(acquired):
            lock.release()
