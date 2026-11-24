"""对外唯一接口：单张图片 + 12 个场景之一。

    POST /v1/recognize

- `200` 识别成功（响应体 `data` 是解析后的字段）
- `422` 识别不成功（`message` 说明原因，可直接给现场看）
- `400/413` 请求本身有问题
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.limits import read_image
from app.metrics import metrics
from app.services.image_fetch import fetch_image
from app.services.recognizer import SCENES, SCENE_LABEL, recognize

router = APIRouter(tags=["recognize"])


def _body(scene: str, message: str, data: dict | None = None) -> dict:
    return {"scene": scene, "message": message, "data": data or {}}


def _error(status: int, scene: str, message: str, data: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=_body(scene, message, data))


@router.post("/recognize")
async def recognize_one(
    scene: str = Form(""),
    image: UploadFile | None = File(None),
    image_url: str = Form(""),
):
    """识别一张图片属于哪个细分场景、并返回解析后的字段。

    `scene` 12 选 1；`image` 与 `image_url` 二选一。
    """
    scene = (scene or "").strip()
    if scene not in SCENES:
        return _error(400, scene or "unknown",
                      f"scene 必须是以下之一：{', '.join(SCENES)}")

    if image is None and not image_url.strip():
        return _error(400, scene, "必须提供 image 或 image_url")
    if image is not None and image_url.strip():
        return _error(400, scene, "image 与 image_url 只能二选一")

    loop_started = time.monotonic()
    deadline_s = max(0.5, settings.request_deadline_s)
    started = time.time()
    metrics.start("image")
    try:
        if image is not None:
            data = await read_image(image)
            filename = image.filename or "image.png"
        else:
            # 下载也算在 5s 预算里：给 3s 上限，剩下留给识别
            fetch_budget = min(3.0, deadline_s)
            try:
                data, filename = await asyncio.wait_for(fetch_image(image_url),
                                                        timeout=fetch_budget)
            except asyncio.TimeoutError:
                metrics.end((time.time() - started) * 1000, error=True)
                return _error(422, scene, f"图片下载超时（>{fetch_budget:.0f}s），请重试",
                              _empty_data(scene))

        # 剩余预算给识别；已用掉太多就直接判超时，不再硬跑
        remaining = deadline_s - (time.monotonic() - loop_started)
        if remaining < 0.5:
            metrics.end((time.time() - started) * 1000, error=True)
            return _error(422, scene, f"识别超时（>{deadline_s:.0f}s），请重拍或重试",
                          _empty_data(scene))

        budget = max(0.3, remaining - 0.2)
        try:
            result = await asyncio.wait_for(recognize(data, filename, scene, budget),
                                            timeout=max(0.5, remaining))
        except asyncio.TimeoutError:
            metrics.end((time.time() - started) * 1000, error=True)
            return _error(422, scene, f"识别超时（>{deadline_s:.0f}s），请重拍或重试",
                          _empty_data(scene))

        metrics.end((time.time() - started) * 1000,
                    error=not result["ok"], review=not result["ok"])
        status = 200 if result["ok"] else 422
        return JSONResponse(status_code=status, content=_body(
            result["scene"], result["message"], result["data"]))
    except HTTPException as exc:
        metrics.end((time.time() - started) * 1000, error=True)
        detail = exc.detail
        message = str(detail.get("message") or detail) if isinstance(detail, dict) else str(detail)
        return _error(exc.status_code, scene, message)
    except Exception:  # noqa: BLE001
        metrics.end((time.time() - started) * 1000, error=True)
        return _error(500, scene, "服务内部错误，请重试")


def _empty_data(scene: str) -> dict:
    if scene == "arrival_plate":
        return {"plate": None}
    if scene in ("license_vin", "frame_vin"):
        return {"vin": None}
    if scene == "odometer":
        return {"odometer": None}
    if scene in ("bridge_drain", "bridge_fill", "tire_drain", "tire_fill"):
        return {"action": None}
    if scene in ("filter_none", "filter_has"):
        return {"filter_state": None}
    if scene == "service_front":
        return {"vin": None, "vin_partial": None}
    return {"signature": False, "stamp": False}


@router.get("/scenes")
async def list_scenes():
    """12 个场景的枚举与中文名（给控制台和上层做下拉用）。"""
    return {"scenes": [{"scene": s, "label": SCENE_LABEL[s]} for s in SCENES]}