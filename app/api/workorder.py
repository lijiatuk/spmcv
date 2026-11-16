from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.analyze import analyze_bytes
from app.config import settings
from app.limits import read_images
from app.metrics import metrics
from app.services.workorder import summarize

router = APIRouter(tags=["workorder"])


@router.post("/workorder")
async def workorder(images: list[UploadFile] = File(...),
                    work_order_id: str = Form(""),
                    axle_type: str = Form(""),
                    expected_action: str = Form(""),
                    expected_vin: str = Form(""),
                    expected_plate: str = Form(""),
                    expected_odometer: str = Form("")):
    """Analyze every photo of one work order and apply the cross-photo rules."""
    if not images:
        raise HTTPException(400, "至少需要一张照片")
    if len(images) > settings.max_photos:
        raise HTTPException(400, f"单个工单最多 {settings.max_photos} 张照片")

    requested_odometer: float | None = None
    if expected_odometer.strip():
        try:
            requested_odometer = float(expected_odometer)
        except ValueError:
            raise HTTPException(400, "expected_odometer 必须是数字")

    start = time.time()
    # 先一次性校验并读入：单张/总量/格式都在这里兜住，
    # 也避免 20 个并发任务各自把原图读进内存。
    payloads = await read_images(images)
    metrics.start("workorder")

    async def run_one(upload: UploadFile, raw: bytes) -> dict:
        try:
            # The per-photo endpoint already ran the expectation checks; work
            # order level expectations are applied once in summarize().
            result = await analyze_bytes(raw, upload.filename or "image.png",
                                         work_order_id=work_order_id,
                                         axle_type=axle_type,
                                         expected_action=expected_action)
        except HTTPException as exc:
            return {"filename": upload.filename, "scene": "error",
                    "error": str(exc.detail)}
        except Exception as exc:  # noqa: BLE001
            return {"filename": upload.filename, "scene": "error",
                    "error": f"{type(exc).__name__}: {exc}"}
        return {
            "filename": upload.filename,
            "scene": result.get("scene"),
            "scene_confidence": result.get("scene_confidence"),
            "conclusion": result.get("conclusion"),
            "fields": result.get("fields"),
            "review_reasons": list(dict.fromkeys(
                result.get("review_reasons") or [])),
            "latency_ms": result.get("latency_ms"),
        }

    try:
        photos = await asyncio.gather(
            *[run_one(u, raw) for u, raw in zip(images, payloads)])
        summary = summarize(
            photos,
            axle_type=axle_type,
            expected_vin=expected_vin,
            expected_odometer=requested_odometer,
            expected_plate=expected_plate,
        )
    except Exception:
        # 异常路径也要配对 end()，否则 in_flight 会一直涨
        metrics.end((time.time() - start) * 1000, error=True, kind="workorder")
        raise

    elapsed = round((time.time() - start) * 1000)
    metrics.end(elapsed, review=bool(summary["need_review"]), kind="workorder")
    return {
        "work_order_id": work_order_id,
        "auto_approved": summary["auto_approved"],
        "need_review": summary["need_review"],
        "review_reasons": summary["review_reasons"],
        "vin": summary["vin"],
        "mileage": summary["mileage"],
        "plate": summary["plate"],
        "oil": summary["oil"],
        "filter": summary["filter"],
        "service_sign": summary["service_sign"],
        "scenes": summary["scenes"],
        "missing_scenes": summary["missing_scenes"],
        "photos": photos,
        "latency_ms": elapsed,
    }