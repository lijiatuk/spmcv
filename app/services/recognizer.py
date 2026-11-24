"""单张 / 12 场景识别。

对外只有一个判定：这张图能不能按给定 `scene` 识别出目标内容。

    OK    读到目标字段（车牌 / VIN / 里程 / 动作 / 滤芯 / 签字盖章）
    FAIL  没读到、读到的不是该场景、或超时

只做识别，不做工单比对。工单规则（VIN 是否一致、四件套是否齐）由调用方组合。

性能取舍（实测，2026-11-20）：
- 油液/滤芯 6 类只需要 12 类场景头，单次 ~220ms，不跑检测和 OCR；
- 文本类走「检测框 → 裁图 OCR」，比整图 OCR 快 3–5 倍；
- 已知场景**不跑** 12 类分类头，也不做旋转兜底——省 150–400ms；
- 大图先缩到长边 1280 再推理，实测快 20–30% 且结果不变。
"""
from __future__ import annotations

import asyncio
import io
import time

from PIL import Image

from app.api.analyze import VISION, _character, _service_sign_boxes
from app.config import settings

# 场景 → (类别, 目标值)。油液/滤芯类的目标值由 12 类头直接给出。
OIL_FILTER = {
    "bridge_drain": ("axle_oil", "drain"),
    "bridge_fill": ("axle_oil", "fill"),
    "tire_drain": ("tire_oil", "drain"),
    "tire_fill": ("tire_oil", "fill"),
    "filter_none": ("filter", "none"),
    "filter_has": ("filter", "has"),
}

TEXT_SCENES = ("arrival_plate", "license_vin", "frame_vin", "odometer", "service_front")

SIGN_SCENE = "service_sign"

SCENES = tuple(OIL_FILTER) + TEXT_SCENES + (SIGN_SCENE,)

SCENE_LABEL = {
    "arrival_plate": "车头及车牌号",
    "license_vin": "行驶证 VIN",
    "frame_vin": "车架贴牌 VIN",
    "odometer": "里程表",
    "bridge_drain": "桥放油",
    "bridge_fill": "桥加油",
    "tire_drain": "轮胎放油",
    "tire_fill": "轮胎加油",
    "filter_none": "无滤芯",
    "filter_has": "有滤芯",
    "service_front": "服务单首页 VIN",
    "service_sign": "服务单末页（签字+盖章）",
}

# 12 类头输出的 slug → 人类可读，用于「识别成别的场景」时的提示
SLUG_LABEL = dict(SCENE_LABEL)

# 缩图长边。实测缩到 1280 会让行驶证 VIN 读错行（候选从正确值变成相邻噪声），
# 而 1080×1440 的手机原图本来就在预算内；只对超大图（>2000）做无损缩放。
MAX_SIDE = 2000


def _downscale(data: bytes) -> bytes:
    """长边超过 MAX_SIDE 才缩放，返回无损 PNG；失败时原样返回。

    必须用 PNG：实测把 1080×1440 的 JPEG 重编码后，车牌小字被压缩噪声糊掉，
    识别直接失败（jpg 变体 plate=None，png 变体正确）。PNG 变体还更快
    （2.4s vs 4.3s），因为后面的检测/OCR 都少解码了 1440 的高度。
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            if max(img.size) <= MAX_SIDE:
                return data
            img = img.convert("RGB")
            scale = MAX_SIDE / max(img.size)
            img = img.resize((max(1, int(img.width * scale)),
                              max(1, int(img.height * scale))), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            out = buf.getvalue()
            # 极端情况下 PNG 可能比原 JPEG 还大：那就用原图，别让内网多传
            return out if len(out) < len(data) else data
    except Exception:  # noqa: BLE001 - 缩放失败不阻塞识别
        return data


def _fail(scene: str, message: str, data: dict) -> dict:
    return {"ok": False, "scene": scene, "message": message, "data": data}


def _ok(scene: str, data: dict) -> dict:
    return {"ok": True, "scene": scene, "message": "识别成功", "data": data}


def _recognize_oil(data: bytes, filename: str, scene: str) -> dict:
    """油液 / 滤芯：12 类场景头直接给动作或滤芯状态。"""
    try:
        res = VISION.scene(data, filename)
    except Exception as exc:  # noqa: BLE001
        return _fail(scene, f"识别服务异常：{type(exc).__name__}", _oil_data(scene, None))

    got = str(res.get("class") or "")
    expected_value = OIL_FILTER[scene][1]
    if scene in ("filter_none", "filter_has"):
        recognized = {"filter_none": "none", "filter_has": "has"}.get(got)
        data = {"filter_state": recognized}
    else:
        recognized = {"bridge_drain": "drain", "bridge_fill": "fill",
                      "tire_drain": "drain", "tire_fill": "fill"}.get(got)
        data = {"action": recognized}

    if got == scene:
        return _ok(scene, data)
    if recognized is None:
        return _fail(scene, f"未识别到「{SCENE_LABEL[scene]}」", data)
    # 同一族但方向相反，或不同部件：说清楚识别成了什么
    return _fail(scene, f"识别为「{SLUG_LABEL.get(got, got)}」，与所选场景「{SCENE_LABEL[scene]}」不一致", data)


def _oil_data(scene: str, value: str | None) -> dict:
    key = "filter_state" if scene in ("filter_none", "filter_has") else "action"
    return {key: value}


def _recognize_sign(data: bytes, filename: str, deadline: float) -> dict:
    """服务单末页：检测器给签字 / 盖章框。"""
    try:
        boxes, _rotated = _service_sign_boxes(data, filename, deadline)
    except Exception as exc:  # noqa: BLE001
        return _fail("service_sign", f"识别服务异常：{type(exc).__name__}",
                     {"signature": False, "stamp": False})

    has_sig = any(b["label"] == "signature" and (b.get("source") == "vlx" or b["score"] >= 0.10)
                  for b in boxes)
    has_stamp = any(b["label"] == "stamp" and (b.get("source") == "vlx" or b["score"] >= 0.25)
                    for b in boxes)
    data = {"signature": has_sig, "stamp": has_stamp}
    if has_sig and has_stamp:
        return _ok("service_sign", data)
    missing = [name for name, got in (("客户签字", has_sig), ("服务站盖章", has_stamp)) if not got]
    return _fail("service_sign", "未检测到" + "、".join(missing), data)


def _recognize_text(data: bytes, filename: str, scene: str, deadline: float) -> dict:
    """车牌 / VIN / 里程 / 首页 VIN：检测框 + 裁图 OCR。"""
    try:
        res = _character(scene, data, filename, None, deadline)
    except Exception as exc:  # noqa: BLE001
        return _fail(scene, f"识别服务异常：{type(exc).__name__}", _text_data(scene, {}))

    fields = res.get("fields") or {}

    if scene == "arrival_plate":
        plate = fields.get("plate")
        data = {"plate": plate}
        if plate:
            return _ok(scene, data)
        return _fail(scene, "未识别到车牌，请靠近或正对车头重拍", data)

    if scene in ("license_vin", "frame_vin"):
        vin = fields.get("vin")
        data = {"vin": vin}
        if vin:
            return _ok(scene, data)
        message = "未识别到 VIN，请对准证件/铭牌重拍"
        if fields.get("vin_partial"):
            message = f"仅识别到尾段 {fields['vin_partial']}，请重拍完整 VIN"
        return _fail(scene, message, data)

    if scene == "odometer":
        odometer = fields.get("odometer")
        data = {"odometer": odometer}
        if odometer is not None:
            return _ok(scene, data)
        return _fail(scene, "未识别到里程，请对准仪表盘重拍", data)

    # service_front
    vin, partial = fields.get("vin"), fields.get("vin_partial")
    data = {"vin": vin, "vin_partial": partial}
    if vin or partial:
        return _ok(scene, data)
    return _fail(scene, "未识别到服务单首页 VIN，请对准首页重拍", data)


def _text_data(scene: str, fields: dict) -> dict:
    if scene == "arrival_plate":
        return {"plate": fields.get("plate")}
    if scene == "odometer":
        return {"odometer": fields.get("odometer")}
    if scene == "service_front":
        return {"vin": fields.get("vin"), "vin_partial": fields.get("vin_partial")}
    return {"vin": fields.get("vin")}


async def recognize(data: bytes, filename: str, scene: str,
                    budget_s: float | None = None) -> dict:
    """识别入口。返回 {ok, scene, message, data}；不抛业务异常。"""
    budget = float(budget_s if budget_s is not None else settings.request_deadline_s)
    deadline = time.monotonic() + max(0.5, budget)

    data = await asyncio.to_thread(_downscale, data) if len(data) > 400 * 1024 else data

    if scene in OIL_FILTER:
        return await asyncio.to_thread(_recognize_oil, data, filename, scene)
    if scene == SIGN_SCENE:
        return await asyncio.to_thread(_recognize_sign, data, filename, deadline)
    return await asyncio.to_thread(_recognize_text, data, filename, scene, deadline)