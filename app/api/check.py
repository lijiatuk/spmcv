"""功能验收接口：单入口 + 场景参数，另保留三个按场景的薄别名。

    POST /v1/check                        scene=arrival|repair|inspection
    POST /v1/check/arrival                等价别名
    POST /v1/check/repair                 等价别名
    POST /v1/check/inspection             等价别名

判定只写在 HTTP 状态码上：200 = 该场景需求满足；422 = 不满足（看 message/missing）。
响应体不再有 ok / result / codes / checks / boxes。
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.categories import ARRIVAL_SCENES, INSPECTION_SCENES, REPAIR_SCENES, _collect
from app.services.checker import REQUIREMENTS, evaluate

router = APIRouter(tags=["check"])

_ALLOWED = {"arrival": ARRIVAL_SCENES, "repair": REPAIR_SCENES, "inspection": INSPECTION_SCENES}


def _error(status: int, scene: str, message: str, missing: list[str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "scene": scene,
        "message": message,
        "missing": missing or [],
        "data": {},
    })


async def _run(scene: str, images: list[UploadFile] | None, *,
               expected_plate: str = "", expected_vin: str = "",
               expected_odometer: str = "", axle_type: str = "") -> JSONResponse:
    if not images:
        return _error(400, scene, "缺少 images 图片")

    odo_expect = None
    if str(expected_odometer).strip():
        try:
            odo_expect = float(expected_odometer)
        except ValueError:
            return _error(422, scene, "expected_odometer 必须是数字")

    try:
        inside, _outside = await _collect(images, _ALLOWED[scene], axle_type=axle_type)
    except HTTPException as exc:
        detail = exc.detail
        message = str(detail.get("message") or detail) if isinstance(detail, dict) else str(detail)
        return _error(exc.status_code, scene, message)
    except Exception:  # noqa: BLE001
        return _error(500, scene, "分析失败，请重试")

    result = evaluate(scene, inside, expected_plate=expected_plate,
                      expected_vin=expected_vin, expected_odometer=odo_expect,
                      axle_type=axle_type)
    return JSONResponse(status_code=200 if result["ok"] else 422, content={
        "scene": scene,
        "message": result["message"],
        "missing": result["missing"],
        "data": result["data"],
    })


@router.post("/check")
async def check(
    images: list[UploadFile] | None = File(None),
    scene: str = Form(""),
    axle_type: str = Form(""),
    expected_plate: str = Form(""),
    expected_vin: str = Form(""),
    expected_odometer: str = Form(""),
):
    """单入口：scene 指定场景，其余参数按场景可选。"""
    scene = (scene or "").strip().lower()
    if scene not in REQUIREMENTS:
        return _error(422, scene, "scene 必须是 arrival / repair / inspection")
    return await _run(scene, images, expected_plate=expected_plate,
                      expected_vin=expected_vin, expected_odometer=expected_odometer,
                      axle_type=axle_type)


@router.post("/check/arrival")
async def check_arrival(
    images: list[UploadFile] | None = File(None),
    expected_plate: str = Form(""),
    expected_vin: str = Form(""),
    expected_odometer: str = Form(""),
):
    """接车场景别名。"""
    return await _run("arrival", images, expected_plate=expected_plate,
                      expected_vin=expected_vin, expected_odometer=expected_odometer)


@router.post("/check/repair")
async def check_repair(
    images: list[UploadFile] | None = File(None), axle_type: str = Form(""),
):
    """维修场景别名。"""
    return await _run("repair", images, axle_type=axle_type)


@router.post("/check/inspection")
async def check_inspection(
    images: list[UploadFile] | None = File(None), expected_vin: str = Form(""),
):
    """检查表场景别名。"""
    return await _run("inspection", images, expected_vin=expected_vin)
