"""功能验收接口：单入口 + 场景参数，另保留三个按场景的薄别名。

    POST /v1/check                        scene=arrival|repair|inspection
    POST /v1/check/arrival                等价别名
    POST /v1/check/repair                 等价别名
    POST /v1/check/inspection             等价别名

默认（不传 photo_type）校验整个场景的照片是否齐全。
传了 photo_type 则切到“单个细分场景”模式：只校验这一张照片，不再报其它照片缺失。

判定只写在 HTTP 状态码上：200 = 通过；422 = 不通过（看 message/missing）。
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.categories import ARRIVAL_SCENES, INSPECTION_SCENES, REPAIR_SCENES, _collect
from app.services.checker import (
    PHOTO_TYPE_LABEL,
    PHOTO_TYPES,
    REQUIREMENTS,
    check_photo,
    evaluate,
    photo_data,
)

router = APIRouter(tags=["check"])

_ALLOWED = {"arrival": ARRIVAL_SCENES, "repair": REPAIR_SCENES, "inspection": INSPECTION_SCENES}


def _error(status: int, scene: str, message: str, missing: list[str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "scene": scene,
        "message": message,
        "missing": missing or [],
        "data": {},
    })


def _photo_response(scene: str, photo_type: str, item: dict, *,
                    expected_plate: str, expected_vin: str,
                    odo_expect: float | None, axle_type: str) -> JSONResponse:
    """单个细分场景的响应：额外给出 matched / actual_scene / applicable。"""
    actual = str(item.get("scene") or "")
    if actual != photo_type:
        actual_label = PHOTO_TYPE_LABEL.get(actual, actual or "未知")
        want_label = PHOTO_TYPE_LABEL.get(photo_type, photo_type)
        return JSONResponse(status_code=422, content={
            "scene": scene,
            "photo_type": photo_type,
            "matched": False,
            "actual_scene": actual or None,
            "message": f"这张图识别为{actual_label}，不是{want_label}",
            "missing": [],
            "data": photo_data(actual, item),
        })

    result = check_photo(photo_type, item, expected_plate=expected_plate,
                         expected_vin=expected_vin, expected_odometer=odo_expect,
                         axle_type=axle_type)
    return JSONResponse(status_code=200 if result["ok"] else 422, content={
        "scene": scene,
        "photo_type": photo_type,
        "matched": True,
        "actual_scene": actual,
        "message": result["message"],
        "missing": [],
        "data": result["data"],
        "applicable": result["applicable"],
    })


async def _run(scene: str, images: list[UploadFile] | None, *,
               expected_plate: str = "", expected_vin: str = "",
               expected_odometer: str = "", axle_type: str = "",
               photo_type: str = "") -> JSONResponse:
    photo_type = (photo_type or "").strip().lower()
    if photo_type and photo_type not in PHOTO_TYPES[scene]:
        return _error(422, scene,
                      "photo_type 必须是 " + " / ".join(PHOTO_TYPES[scene]) + " 之一")

    if not images:
        return _error(400, scene, "缺少 images 图片")
    if photo_type and len(images) != 1:
        return _error(400, scene, "photo_type 单场景模式只支持 1 张图片")

    odo_expect = None
    if str(expected_odometer).strip():
        try:
            odo_expect = float(expected_odometer)
        except ValueError:
            return _error(422, scene, "expected_odometer 必须是数字")

    try:
        inside, outside = await _collect(images, _ALLOWED[scene], axle_type=axle_type)
    except HTTPException as exc:
        detail = exc.detail
        message = str(detail.get("message") or detail) if isinstance(detail, dict) else str(detail)
        return _error(exc.status_code, scene, message)
    except Exception:  # noqa: BLE001
        return _error(500, scene, "分析失败，请重试")

    if photo_type:
        items = inside or outside
        if not items:
            return _error(500, scene, "分析结果为空")
        return _photo_response(scene, photo_type, items[0],
                               expected_plate=expected_plate,
                               expected_vin=expected_vin,
                               odo_expect=odo_expect, axle_type=axle_type)

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
    photo_type: str = Form(""),
):
    """单入口：scene 指定场景，photo_type 可选指定单个细分场景。"""
    scene = (scene or "").strip().lower()
    if scene not in REQUIREMENTS:
        return _error(422, scene, "scene 必须是 arrival / repair / inspection")
    return await _run(scene, images, expected_plate=expected_plate,
                      expected_vin=expected_vin, expected_odometer=expected_odometer,
                      axle_type=axle_type, photo_type=photo_type)


@router.post("/check/arrival")
async def check_arrival(
    images: list[UploadFile] | None = File(None),
    expected_plate: str = Form(""),
    expected_vin: str = Form(""),
    expected_odometer: str = Form(""),
    photo_type: str = Form(""),
):
    """接车场景别名；photo_type 可选指定单个细分场景。"""
    return await _run("arrival", images, expected_plate=expected_plate,
                      expected_vin=expected_vin, expected_odometer=expected_odometer,
                      photo_type=photo_type)


@router.post("/check/repair")
async def check_repair(
    images: list[UploadFile] | None = File(None), axle_type: str = Form(""),
    photo_type: str = Form(""),
):
    """维修场景别名；photo_type 可选指定单个细分场景。"""
    return await _run("repair", images, axle_type=axle_type, photo_type=photo_type)


@router.post("/check/inspection")
async def check_inspection(
    images: list[UploadFile] | None = File(None), expected_vin: str = Form(""),
    photo_type: str = Form(""),
):
    """检查表场景别名；photo_type 可选指定单个细分场景。"""
    return await _run("inspection", images, expected_vin=expected_vin,
                      photo_type=photo_type)
