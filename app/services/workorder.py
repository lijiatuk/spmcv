"""Work-order level validation across the whole photo set.

The per-photo endpoint answers "what is in this picture". The business rules in
the SPM spec are statements about *the set*: the driving-licence VIN and the
frame VIN must agree, the mileage must be within 5% of what the order says, a
Hande axle never drains oil, and so on. This module only contains pure functions
so it can be unit-tested without any model.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.services.fields import vin_checksum_ok

MILEAGE_TOLERANCE = 0.05

SCENE_LABELS = {
    "arrival_plate": "接车照片",
    "odometer": "里程表照片",
    "service_sign": "服务单末页",
}

# Scenes that must be present before a work order can be auto-approved.
REQUIRED_SCENES = ("arrival_plate", "odometer", "service_sign")
VIN_SCENES = ("license_vin", "frame_vin", "service_front")


def dedupe(items: Iterable[str]) -> list[str]:
    """Preserve order, drop repeats: several rules can flag the same photo."""
    return list(dict.fromkeys(i for i in items if i))


def _norm(value: str | None) -> str:
    return (value or "").strip().upper()


def check_vin_consistency(items: Iterable[dict]) -> dict:
    """Compare every VIN-bearing photo and the work order's own VIN."""
    found: dict[str, str] = {}
    partials: dict[str, str] = {}
    for item in items:
        scene = item.get("scene")
        fields = item.get("fields") or {}
        vin = _norm(fields.get("vin"))
        partial = _norm(fields.get("vin_partial"))
        # 校验位不过的值不参与一致性判断
        if vin and fields.get("vin_checksum_ok") is False:
            vin = ""
        elif vin and fields.get("vin_checksum_ok") is None and not vin_checksum_ok(vin):
            vin = ""
        if vin:
            found[scene] = vin
        elif partial:
            partials[scene] = partial

    reasons: list[str] = []
    distinct = sorted(set(found.values()))
    if len(distinct) > 1:
        detail = "、".join(f"{s}={v}" for s, v in sorted(found.items()))
        reasons.append(f"VIN 不一致（{detail}）")

    confidence = "none"
    if distinct:
        confidence = "full"
    elif partials:
        confidence = "partial"

    return {
        "vin": distinct[0] if len(distinct) == 1 else None,
        "by_scene": found,
        "partials": partials,
        "confidence": confidence,
        "reasons": reasons,
    }


def check_mileage(item: dict | None, expected_odometer: float | None) -> dict:
    """Apply the spec's 5% tolerance between photo mileage and the order."""
    reasons: list[str] = []
    if not item:
        # summarize() already reports the missing scene; avoid a duplicate line.
        return {"odometer": None, "reasons": [], "ok": False, "present": False}

    fields = item.get("fields") or {}
    odometer = fields.get("odometer")
    if odometer is None:
        return {"odometer": None, "reasons": ["未识别到里程"], "ok": False}

    if expected_odometer in (None, ""):
        return {"odometer": float(odometer), "reasons": [], "ok": True}

    try:
        expected = float(expected_odometer)
    except (TypeError, ValueError):
        return {"odometer": float(odometer), "reasons": [], "ok": True}

    if expected <= 0:
        return {"odometer": float(odometer), "reasons": [], "ok": True}

    deviation = abs(float(odometer) - expected) / expected
    ok = deviation <= MILEAGE_TOLERANCE
    if not ok:
        reasons.append(
            f"里程 {float(odometer):.0f} 与工单 {expected:.0f} 偏差 "
            f"{deviation:.1%}，超过 {MILEAGE_TOLERANCE:.0%}"
        )
    return {
        "odometer": float(odometer),
        "expected": expected,
        "deviation": round(deviation, 4),
        "ok": ok,
        "reasons": reasons,
    }


def check_oil(items: Iterable[dict], axle_type: str = "") -> dict:
    """Apply the Hande-axle matrix from the spec to the oil/tyre photos."""
    reasons: list[str] = []
    seen = {"axle_oil": [], "tire_oil": []}
    for item in items:
        # 用粗粒度分组判断（scene 现在是 12 类，油液细分为 bridge_drain 等）
        group = item.get("scene_group") or item.get("scene")
        if group in seen:
            action = (item.get("conclusion") or {}).get("action")
            seen[group].append(action)

    if axle_type == "hande":
        if any(a == "drain" for a in seen["axle_oil"]):
            reasons.append("汉德桥不应出现桥放油动作")
        if seen["tire_oil"]:
            reasons.append("汉德桥不做轮胎油液判定")

    return {"session": seen, "reasons": reasons}


def check_filter(items: Iterable[dict], axle_type: str = "") -> dict:
    reasons: list[str] = []
    states = [
        (item.get("conclusion") or {}).get("action")
        for item in items
        if (item.get("scene_group") or item.get("scene")) == "filter"
    ]
    if axle_type and axle_type != "hande" and states:
        reasons.append("非汉德桥不做滤芯判定")
    return {"states": states, "reasons": reasons}


def check_service_sign(item: dict | None) -> dict:
    """Signature/stamp presence; a missing file is reported by summarize()."""
    if not item:
        return {"reasons": [], "ok": False, "present": False}
    detail = list(item.get("review_reasons") or [])
    return {"ok": not detail, "reasons": detail, "present": True}


def summarize(items: list[dict], axle_type: str = "",
              expected_vin: str = "", expected_odometer: float | None = None,
              expected_plate: str = "") -> dict:
    """Roll a work order's photos up into one approval decision."""
    by_scene: dict[str, dict] = {}
    for item in items:
        by_scene.setdefault(item.get("scene"), item)

    reasons: list[str] = []
    missing = [s for s in REQUIRED_SCENES if s not in by_scene]
    reasons.extend(f"缺少{SCENE_LABELS.get(s, s)}" for s in missing)

    vin = check_vin_consistency(items)
    reasons.extend(vin["reasons"])

    exp_vin = _norm(expected_vin)
    if exp_vin:
        if vin["confidence"] == "full" and vin["vin"] and vin["vin"] != exp_vin:
            reasons.append(f"VIN 与工单不一致：{vin['vin']} ≠ {exp_vin}")
        elif vin["confidence"] == "partial":
            tails = list(vin["partials"].values())
            if tails and not all(exp_vin.endswith(t) for t in tails):
                reasons.append(f"VIN 尾段 {tails[0]} 与工单不一致")

    mileage = check_mileage(by_scene.get("odometer"), expected_odometer)
    reasons.extend(mileage["reasons"])

    oil = check_oil(items, axle_type)
    reasons.extend(oil["reasons"])

    filters = check_filter(items, axle_type)
    reasons.extend(filters["reasons"])

    sign = check_service_sign(by_scene.get("service_sign"))
    reasons.extend(sign["reasons"])

    plate = _norm(((by_scene.get("arrival_plate") or {}).get("fields") or {}).get("plate"))
    exp_plate = _norm(expected_plate)
    if exp_plate and plate and plate != exp_plate:
        reasons.append(f"车牌与工单不一致：{plate} ≠ {exp_plate}")

    reasons = dedupe(reasons)
    return {
        "auto_approved": not reasons,
        "need_review": bool(reasons),
        "review_reasons": reasons,
        "vin": vin,
        "mileage": mileage,
        "oil": oil,
        "filter": filters,
        "service_sign": sign,
        "plate": plate or None,
        "scenes": sorted(by_scene),
        "missing_scenes": missing,
    }