"""三类场景各一个接口。

调用方按素材目录直接选接口，不用自己判断场景：

    POST /v1/arrival     接车照片（车牌 / 行驶证 VIN / 车架 VIN / 里程）
    POST /v1/repair      故障及修复（桥、轮胎的加油放油；有/无滤芯）
    POST /v1/inspection  项目检查表（服务单首页 VIN、末页签字盖章）

每个接口接收**同类**的多张照片，内部逐张识别后按该类规则聚合。
传错类的照片不会让整体失败，而是放进 `unexpected` 并给出提示。
"""
from __future__ import annotations

import json

import asyncio
import time

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.analyze import analyze_bytes, analyze_with_deadline
from app.config import settings
from app.limits import read_images
from app.services.vision_client import VisionClient
from app.config import settings as _settings
from app.metrics import metrics
from app.services.categories import (
    ARRIVAL_SCENES,
    INSPECTION_SCENES,
    REPAIR_SCENES,
    SCENE_LABEL,
    summarize_arrival,
    summarize_inspection,
    summarize_repair,
)

router = APIRouter(tags=["categories"])
VISION = VisionClient(_settings.vision_url, _settings.scene_timeout, _settings.detect_timeout)

# 一次上传 20 张时，如果 20 个分析同时跑，会互相抢 OCR/GPU，单张反而全部超时。
# 用信号量把并发压在 MAX_CONCURRENT 内，让每张的耗时可控。
# 2 而不是 4：实测 4 路并发时 OCR 线程池互相抢，单张从 2s 涨到 5s+，
# 反而全部撞死线。压到 2 路，单张稳定在 3s 内。
_ANALYZE_SLOTS = asyncio.Semaphore(max(1, min(2, _settings.max_concurrent)))

# 这个张数以内完全串行：并发会让 OCR 抢线程，出现「同样的图有时读得出有时读不出」。
# 控制台实际使用是 1–4 张，串行后每张 ~1s，总时长可接受且结果可复现。
SEQUENTIAL_LIMIT = int(_settings.sequential_photos)


async def _analyze_limited(raw: bytes, filename: str, **kw):
    async with _ANALYZE_SLOTS:
        return await analyze_with_deadline(raw, filename, **kw)


async def _detect_boxes(raw: bytes, filename: str) -> list[dict]:
    """给前端画框用：返回检测器在这次请求里看到的框。"""
    try:
        # 0.10 而不是默认 0.25：车牌这类小目标常落在 0.1~0.25，
        # 可视化需要把它画出来（只影响展示，不影响任何判定）
        det = await asyncio.to_thread(VISION.detect, raw, filename, "", 0.10)
    except Exception:  # noqa: BLE001 - 可视化失败不影响结论
        return []
    out = []
    for b in det.get("boxes") or []:
        box = b.get("box") or []
        if len(box) < 4:
            continue
        out.append({"label": b.get("label"), "score": round(float(b.get("score") or 0), 3),
                    "box": [round(float(v), 1) for v in box[:4]]})
    out.sort(key=lambda x: -x["score"])
    return out[:12]


async def _collect(images: list[UploadFile], allowed: tuple[str, ...],
                   axle_type: str = "", expected_action: str = "",
                   with_boxes: bool = False) -> tuple[list[dict], list[dict]]:
    """逐张识别，返回 (本类照片, 不属于本类的照片)。"""
    if len(images) > settings.max_photos:
        raise HTTPException(400, f"最多 {settings.max_photos} 张照片")
    payloads = await read_images(images)
    start = time.time()
    metrics.start("workorder")
    try:
        # 少图（≤2 张）串行：并发会让 OCR 抢线程、超预算后静默放弃，
        # 表现为「同样两张图，有时读出车牌有时读不出」——比慢更糟。
        # 多图才并发，并用信号量限制。
        jobs = [
            (upload.filename or "image.jpg", raw)
            for upload, raw in zip(images, payloads)
        ]
        if len(jobs) <= SEQUENTIAL_LIMIT:
            results = []
            for name, raw in jobs:
                results.append(await analyze_with_deadline(
                    raw, name, axle_type=axle_type, expected_action=expected_action))
        else:
            results = await asyncio.gather(*[
                _analyze_limited(raw, name, axle_type=axle_type,
                                 expected_action=expected_action)
                for name, raw in jobs
            ])
    except Exception:
        metrics.end((time.time() - start) * 1000, error=True, kind="workorder")
        raise

    inside: list[dict] = []
    outside: list[dict] = []
    for idx, (upload, res) in enumerate(zip(images, results)):
        scene = res.get("scene")
        group = res.get("scene_group") or scene
        brief = {
            "index": idx,
            "filename": upload.filename,
            "scene": scene,
            "scene_label": SCENE_LABEL.get(scene, scene),
            "confidence": res.get("scene_confidence"),
            "model_version": res.get("model_version"),
            "latency_ms": res.get("latency_ms"),
            "need_review": bool(res.get("need_review")),
            "review_reasons": res.get("review_reasons") or [],
            # 逐图结论：调用方需要知道是哪张图驱动了最终判定
            "action": (res.get("conclusion") or {}).get("action"),
            "field_summary": res.get("fields") or {},
            # OCR 原文（截断）：上层排查"没拍到"还是"拍到了没读对"时必需
            "ocr_text": (res.get("evidence") or {}).get("ocr_text", "")[:400],
        }
        ocr_code = ocr_error_code(res.get("ocr_error"))
        if ocr_code:
            brief["ocr_error_code"] = ocr_code
            message = ("OCR 服务超时，请重试或人工录入" if ocr_code == "OCR_TIMEOUT"
                       else "OCR 服务暂不可用，请重试或人工录入")
            if message not in brief["review_reasons"]:
                brief["review_reasons"].append(message)
            brief["need_review"] = True
        if res.get("timed_out"):
            # 超时必须显式暴露，否则上层只会看到"未识别到车牌"，误以为是拍摄问题
            brief["timed_out"] = True
            res.setdefault("review_reasons", []).append("分析超时，已降级")
        if with_boxes:
            brief["boxes"] = await _detect_boxes(payloads[idx], upload.filename or "image.png")
        (inside if group in allowed else outside).append({**res, "_brief": brief})

    metrics.end((time.time() - start) * 1000,
                review=any(r.get("need_review") for r in results), kind="workorder")
    return inside, outside


def _briefs(items: list[dict]) -> list[dict]:
    return [it["_brief"] for it in items]


def _finalize(payload: dict) -> dict:
    """把内部 ok/status 收敛成对外唯一结论字段 ``result``。

    上层只认这一个字段，避免同时出现 ok / status / decision 三个近似含义：

      pass     本次请求的业务校验全部通过（arrival 还要求四件套齐全且 VIN 一致）
      partial  已提交的照片通过，但本场景照片没交齐（仅 arrival 会出现）
      review   存在不通过原因，需人工；具体看 reasons / codes / photos
    """
    ok = bool(payload.pop("ok", False))
    status = payload.pop("status", None)
    payload.pop("decision", None)    # 旧字段，统一收敛到 result
    payload.pop("need_review", None)  # 顶层不再给第二个结论，逐图仍保留
    if not ok:
        payload["result"] = "review"
    elif status == "partial":
        payload["result"] = "partial"
    else:
        payload["result"] = "pass"
    return payload


@router.post("/arrival")
async def arrival(images: list[UploadFile] = File(...), boxes: bool = False,
                  expected_plate: str = Form(""), expected_odometer: str = Form(""),
                  strict: bool = Form(False)):
    """接车照片：车牌、行驶证与车架 VIN、里程。

    默认只校验本次**实际提交**的照片：只传一张车头照时，不会因为没传
    行驶证/里程表而报「未识别到 VIN」。需要整单四件套齐备才能通过时，
    传 `strict=1`（或改用 `/v1/workorder`）。

    按规格支持工单比对：`expected_plate` 车牌一致性、`expected_odometer` 里程误差 ≤5%。
    """
    inside, outside = await _collect(images, ARRIVAL_SCENES, with_boxes=boxes)
    odo_expect = None
    if str(expected_odometer).strip():
        try:
            odo_expect = float(expected_odometer)
        except ValueError:
            raise HTTPException(422, detail={"code": "INVALID_PARAMS",
                                             "message": "expected_odometer 必须是数字"})
    out = summarize_arrival(inside, expected_plate=expected_plate,
                            expected_odometer=odo_expect,
                            require_complete=bool(strict))
    out["photos"] = _briefs(inside)
    out["unexpected"] = _briefs(outside)
    out["codes"] = reason_codes(out.get("reasons") or [])
    _include_ocr_errors(out, inside + outside)
    return _finalize(out)


@router.post("/repair")
async def repair(images: list[UploadFile] = File(...),
                 axle_type: str = Form(""),
                 expected_action: str = Form(""),
                 boxes: bool = False):
    """故障及修复：桥/轮胎加油放油、有/无滤芯。`axle_type=hande` 会启用汉德桥规则。"""
    inside, outside = await _collect(images, REPAIR_SCENES,
                                     axle_type=axle_type,
                                     expected_action=expected_action,
                                     with_boxes=boxes)
    out = summarize_repair(inside, axle_type=axle_type)
    out["photos"] = _briefs(inside)
    out["unexpected"] = _briefs(outside)
    out["codes"] = reason_codes(out.get("reasons") or [])
    _include_ocr_errors(out, inside + outside)
    return _finalize(out)


@router.post("/inspection")
async def inspection(images: list[UploadFile] = File(...),
                     expected_vin: str = Form(""),
                     boxes: bool = False):
    """项目检查表：服务单首页 VIN、末页签字与盖章。"""
    inside, outside = await _collect(images, INSPECTION_SCENES, with_boxes=boxes)
    out = summarize_inspection(inside)
    out["photos"] = _briefs(inside)
    out["unexpected"] = _briefs(outside)
    out["codes"] = reason_codes(out.get("reasons") or [])
    _include_ocr_errors(out, inside + outside)
    return _finalize(out)


# ---------------- 对外统一入口 ----------------

# 对外错误码：中文原因里带业务细节，机器只认这里的码。
REASON_CODES = [
    ("OCR 服务超时", "OCR_TIMEOUT"),
    ("OCR 服务暂不可用", "OCR_UNAVAILABLE"),
    ("未识别到车牌", "MISSING_PLATE"),
    ("未识别到行驶证 VIN", "MISSING_LICENSE_VIN"),
    ("未识别到车架 VIN", "MISSING_FRAME_VIN"),
    ("行驶证 VIN 校验位不通过", "VIN_CHECKSUM_FAILED"),
    ("车架 VIN 校验位不通过", "VIN_CHECKSUM_FAILED"),
    ("有 VIN 未通过校验位", "VIN_CHECKSUM_FAILED"),
    ("VIN 不一致", "VIN_MISMATCH"),
    ("车牌与工单不一致", "PLATE_MISMATCH"),
    ("里程与工单相差", "ODOMETER_MISMATCH"),
    ("非汉德桥缺少桥放油", "MISSING_DRAIN_PHOTO"),
    ("非汉德桥缺少桥加油", "MISSING_FILL_PHOTO"),
    ("未识别到里程", "MISSING_ODOMETER"),
    ("桥的加油/放油未判定", "OIL_VERDICT_MISSING"),
    ("汉德桥不应出现桥放油", "RULE_HANDE_DRAIN"),
    ("汉德桥不做轮胎油液判定", "RULE_HANDE_TIRE"),
    ("非汉德桥不做滤芯判定", "RULE_FILTER_UNEXPECTED"),
    ("服务单首页未识别到 VIN", "MISSING_SERVICE_VIN"),
    ("服务单 VIN 与预期不一致", "SERVICE_VIN_MISMATCH"),
    ("未检测到客户签字", "MISSING_SIGNATURE"),
    ("未检测到服务站盖章", "MISSING_STAMP"),
    ("未指定 category", "CATEGORY_REQUIRED"),
    ("分析超时", "TIMEOUT"),
    ("分析失败", "INTERNAL_ERROR"),
]


def reason_codes(reasons: list[str]) -> list[str]:
    """把中文原因映射成稳定英文码（去重保序）。"""
    out: list[str] = []
    for r in reasons or []:
        code = next((c for prefix, c in REASON_CODES if r.startswith(prefix)), "OTHER")
        if code not in out:
            out.append(code)
    return out


def ocr_error_code(error: str | None) -> str | None:
    """Expose a stable status without leaking upstream exception text."""
    if not error:
        return None
    if "timeout" in error.lower():
        return "OCR_TIMEOUT"
    return "OCR_UNAVAILABLE"


def _include_ocr_errors(payload: dict, items: list[dict]) -> None:
    messages = [
        "OCR 服务超时，请重试或人工录入"
        if item["_brief"].get("ocr_error_code") == "OCR_TIMEOUT"
        else "OCR 服务暂不可用，请重试或人工录入"
        for item in items if item["_brief"].get("ocr_error_code")
    ]
    if not messages:
        return
    reasons = list(payload.get("reasons") or [])
    reasons.extend(messages)
    payload["reasons"] = list(dict.fromkeys(reasons))
    payload["codes"] = reason_codes(payload["reasons"])
    # OCR 异常一票记为 review：_finalize 会据此把 result 置为 review
    if "ok" in payload:
        payload["ok"] = False


SCENE_BY_CATEGORY = {
    "arrival": ARRIVAL_SCENES,
    "repair": REPAIR_SCENES,
    "inspection": INSPECTION_SCENES,
}


@router.post("/scan")
async def scan(
    images: list[UploadFile] = File(...),
    category: str = Form(""),
    axle_type: str = Form(""),
    expected_action: str = Form(""),
    expected_vin: str = Form(""),
    expected_plate: str = Form(""),
    expected_odometer: str = Form(""),
    strict: bool = Form(False),
    boxes: bool = False,
):
    """对外统一入口：1..N 张图 + 可选 category。

    和三个分场景接口的区别：
      - `category` 可空。传了就按该场景的规则校验（能判"这张不属于本步骤"）；
        不传就只做场景识别，不做业务校验。
      - 单张时也能调用，返回单图结论，不会因为"四件套不齐"直接失败。

    产品化的关键区别：**VIN/字段读不出不再一票否决**，而是返回
    `result=review` + 具体原因码，让上层自己决定要不要拦。
    """
    if category and category not in SCENE_BY_CATEGORY:
        raise HTTPException(422, detail={"code": "INVALID_PARAMS",
                                         "message": f"category 只能是 {list(SCENE_BY_CATEGORY)} 或留空"})

    allowed = SCENE_BY_CATEGORY.get(category) or tuple(
        {meta["scene"] for meta in json.loads(
            (Path(__file__).resolve().parents[1] / "scenes.json").read_text(encoding="utf-8")
        )["class_map"].values()}
    )

    inside, outside = await _collect(images, allowed, axle_type=axle_type,
                                     expected_action=expected_action,
                                     with_boxes=boxes)

    payload: dict = {
        "category": category or None,
        "photos": [p["_brief"] for p in inside],
        "unexpected": [p["_brief"] for p in outside],
    }

    if category == "arrival":
        odo_expect = None
        if str(expected_odometer).strip():
            try:
                odo_expect = float(expected_odometer)
            except ValueError:
                raise HTTPException(422, detail={"code": "INVALID_PARAMS",
                                                 "message": "expected_odometer 必须是数字"})
        summary = summarize_arrival(inside, expected_plate=expected_plate,
                                    expected_odometer=odo_expect,
                                    require_complete=bool(strict))
        payload.update(summary)
    elif category == "repair":
        summary = summarize_repair(inside, axle_type)
        payload.update(summary)
    elif category == "inspection":
        summary = summarize_inspection(inside, expected_vin)
        payload.update(summary)
    else:
        # 没传 category：只给每张图是什么，不判定业务
        payload["reasons"] = ["未指定 category，仅返回场景识别结果"]

    payload["codes"] = reason_codes(payload.get("reasons") or [])
    _include_ocr_errors(payload, inside + outside)
    return _finalize(payload)
