"""功能验收规则：只回答「这批照片满足不满足业务需求」。

规则来自 `data/SPM系统AI识别.xlsx` 的「业务处理逻辑」，不是按接口字段推导的：

- 接车：车头/车牌、行驶证 VIN、车架 VIN、里程表四类照片齐全；至少读到一个 VIN；
  两个 VIN 同时读到必须一致；车牌/ VIN/里程读到且工单给了期望值时才比对；
  车牌读不到按规格跳过；里程表照片存在即可，读到才做 ±5% 比对。
- 维修：非汉德桥要桥放油+桥加油、轮胎放油+轮胎加油，不判滤芯；
  汉德桥要桥加油、无滤芯+有滤芯，不判轮胎。
- 检查表：首页 VIN（完整，或尾段能对上工单 VIN）+ 客户签字 + 服务站盖章。

这里只产出 `ok` / `message` / `checks` / `missing`，不出现 result/codes/scene 等
内部概念，方便上层只判断 HTTP 200（ok）或非 200（不满足）。
"""
from __future__ import annotations

import re
from typing import Any

REQUIREMENTS = ("arrival", "repair", "inspection")

REQUIREMENT_LABEL = {
    "arrival": "接车照片",
    "repair": "故障及修复照片",
    "inspection": "项目检查表",
}

ACTION_LABEL = {"fill": "加油", "drain": "放油", "has": "有滤芯", "none": "无滤芯"}

_VIN_BAD = re.compile(r"[^0-9A-Z]")
_PLATE_BAD = re.compile(r"[^0-9A-Z\u4e00-\u9fa5]")


def norm_vin(value: Any) -> str:
    return _VIN_BAD.sub("", str(value or "").strip().upper())


def norm_plate(value: Any) -> str:
    return _PLATE_BAD.sub("", str(value or "").strip().upper())


def first_scene(items: list[dict], scene: str) -> dict | None:
    for item in items or []:
        if item.get("scene") == scene:
            return item
    return None


def _fields(item: dict | None) -> dict:
    return (item or {}).get("fields") or {}


def _action(item: dict | None) -> str | None:
    return ((item or {}).get("conclusion") or {}).get("action")


def _labels(item: dict | None) -> set[str]:
    boxes = ((item or {}).get("evidence") or {}).get("boxes") or []
    return {str(b.get("label") or "") for b in boxes if isinstance(b, dict)}


def _actions_value(items: list[dict], scenes: tuple[str, ...]) -> str | None:
    """把若干场景的动作拼成 drain+fill / none+has；没有则为 None。"""
    actions: list[str] = []
    for scene in scenes:
        act = _action(first_scene(items, scene))
        if act and act not in actions:
            actions.append(act)
    return "+".join(actions) if actions else None


def _check(item: str, ok: bool, value: Any = "", message: str = "") -> dict:
    return {"item": item, "ok": bool(ok), "value": value, "message": message}


def _finish(requirement: str, checks: list[dict], missing: list[str], success: str,
            data: dict | None = None) -> dict:
    ok = not missing and all(c["ok"] for c in checks)
    if ok:
        message = success
    else:
        parts: list[str] = []
        if missing:
            parts.append("缺少照片：" + "、".join(missing))
        for c in checks:
            if not c["ok"] and c["message"] and not c["message"].startswith("缺少") \
                    and c["message"] not in parts:
                parts.append(c["message"])
        message = "；".join(parts) or "需求不满足"
    return {
        "ok": ok,
        "requirement": requirement,
        "message": message,
        "missing": missing,
        "data": data or {},
        "checks": checks,
    }


# ---------------- 接车 ----------------


def check_arrival(items: list[dict], expected_plate: str = "", expected_vin: str = "",
                  expected_odometer: float | None = None) -> dict:
    checks: list[dict] = []
    missing: list[str] = []

    plate_item = first_scene(items, "arrival_plate")
    if not plate_item:
        missing.append("车头及车牌")
        checks.append(_check("车头及车牌", False, message="缺少车头及车牌照片"))
    else:
        plate = norm_plate(_fields(plate_item).get("plate"))
        checks.append(_check("车头及车牌", True, value=plate or "未识别到车牌"))

    # 行驶证 / 车架：两张照片都要有；VIN 至少要读到一个。
    vins: list[str] = []
    for scene, label in (("license_vin", "行驶证VIN"), ("frame_vin", "车架VIN")):
        item = first_scene(items, scene)
        if not item:
            missing.append(label)
            checks.append(_check(label, False, message=f"缺少{label}照片"))
            continue
        vin = norm_vin(_fields(item).get("vin"))
        if vin:
            vins.append(vin)
        checks.append(_check(label, True, value=vin or "未识别到 VIN",
                            message="" if vin else "照片已上传，未识别到 VIN"))

    if not vins:
        checks.append(_check("VIN 可用", False, message="未识别到任一 VIN，无法进入自动审核"))
    elif len(vins) >= 2 and vins[0] != vins[1]:
        checks.append(_check("VIN 一致", False, value=f"{vins[0]} ≠ {vins[1]}",
                             message=f"行驶证与车架 VIN 不一致：{vins[0]} ≠ {vins[1]}"))
    else:
        checks.append(_check("VIN 一致", True, value=vins[0]))

    if expected_vin:
        want = norm_vin(expected_vin)
        if not vins:
            checks.append(_check("与工单 VIN 一致", False, message="未识别到 VIN，无法与工单比对"))
        elif any(v != want for v in vins):
            checks.append(_check("与工单 VIN 一致", False, value=f"{vins[0]} ≠ {want}",
                                 message=f"VIN 与工单不一致：{vins[0]} ≠ {want}"))
        else:
            checks.append(_check("与工单 VIN 一致", True, value=vins[0]))
    else:
        checks.append(_check("与工单 VIN 一致", True, value="未提供 expected_vin",
                             message="未传 expected_vin，仅校验 VIN 一致性，未与工单比对"))

    plate = norm_plate(_fields(plate_item).get("plate")) if plate_item else ""
    if plate and expected_plate:
        want_plate = norm_plate(expected_plate)
        if plate != want_plate:
            checks.append(_check("与工单车牌一致", False, value=f"{plate} ≠ {want_plate}",
                                 message=f"车牌与工单不一致：{plate} ≠ {want_plate}"))
        else:
            checks.append(_check("与工单车牌一致", True, value=plate))
    elif not plate and expected_plate:
        checks.append(_check("与工单车牌一致", True, value="未识别到车牌",
                             message="未识别到车牌，已按规格跳过车牌校验"))

    odo_item = first_scene(items, "odometer")
    odo_val: float | None = None
    if not odo_item:
        missing.append("里程表")
        checks.append(_check("里程表", False, message="缺少里程表照片"))
    else:
        raw = _fields(odo_item).get("odometer")
        odo = _as_float(raw)
        odo_val = odo
        if odo is None:
            checks.append(_check("里程表", True, value="未识别到里程",
                                 message="里程表照片已上传，未读出里程"))
        elif expected_odometer:
            diff = abs(odo - expected_odometer) / expected_odometer * 100 if expected_odometer else 0
            if diff > 5:
                checks.append(_check("与工单里程一致", False, value=odo,
                                     message=f"里程与工单相差 {diff:.1f}%（>5%）：{odo} vs {expected_odometer}"))
            else:
                checks.append(_check("与工单里程一致", True, value=odo))
        else:
            checks.append(_check("里程表", True, value=odo))

    vin_common = vins[0] if vins and (len(vins) == 1 or vins[0] == vins[1]) else None
    data = {"plate": plate or None, "vin": vin_common, "odometer": odo_val}
    return _finish("arrival", checks, missing,
                   "接车照片满足需求：四类照片齐全，VIN 校验通过", data=data)


# ---------------- 维修 ----------------


def check_repair(items: list[dict], axle_type: str = "") -> dict:
    hande = str(axle_type or "").strip().lower() == "hande"
    checks: list[dict] = []
    missing: list[str] = []

    if hande:
        required = (
            ("bridge_fill", "桥加油", "fill"),
            ("filter_none", "无滤芯", "none"),
            ("filter_has", "有滤芯", "has"),
        )
        success = "汉德桥照片满足需求：桥加油、无滤芯、有滤芯齐全"
        ignored_label, ignored_scenes = "轮胎", ("tire_drain", "tire_fill")
    else:
        required = (
            ("bridge_drain", "桥放油", "drain"),
            ("bridge_fill", "桥加油", "fill"),
            ("tire_drain", "轮胎放油", "drain"),
            ("tire_fill", "轮胎加油", "fill"),
        )
        success = "非汉德桥照片满足需求：桥放油/加油、轮胎放油/加油齐全"
        ignored_label, ignored_scenes = "滤芯", ("filter_none", "filter_has")

    for scene, label, wanted in required:
        item = first_scene(items, scene)
        if not item:
            missing.append(label)
            checks.append(_check(label, False, message=f"缺少{label}照片"))
            continue
        got = _action(item)
        if got != wanted:
            checks.append(_check(label, False, value=ACTION_LABEL.get(got or "", got or "未判定"),
                                 message=f"识别为「{ACTION_LABEL.get(got or '', got or '未判定')}」，"
                                         f"不满足「{ACTION_LABEL[wanted]}」"))
        else:
            checks.append(_check(label, True, value=ACTION_LABEL[wanted]))

    # 规格明确不判的部分：上传了也不参与判定，避免调用方误以为失败。
    if any(first_scene(items, s) for s in ignored_scenes):
        checks.append(_check(ignored_label, True, value="已上传，按规格不判定"))

    if hande:
        data = {"axle": _actions_value(items, ("bridge_fill",)),
                "tire": None,
                "filter": _actions_value(items, ("filter_none", "filter_has"))}
    else:
        data = {"axle": _actions_value(items, ("bridge_drain", "bridge_fill")),
                "tire": _actions_value(items, ("tire_drain", "tire_fill")),
                "filter": None}

    return _finish("repair", checks, missing, success, data=data)


# ---------------- 检查表 ----------------


def check_inspection(items: list[dict], expected_vin: str = "") -> dict:
    checks: list[dict] = []
    missing: list[str] = []
    vin_full: str | None = None
    vin_partial: str | None = None
    signed = False
    stamped = False

    front = first_scene(items, "service_front")
    if not front:
        missing.append("服务单首页")
        checks.append(_check("服务单首页", False, message="缺少服务单首页照片"))
    else:
        vin_full = norm_vin(_fields(front).get("vin")) or None
        vin_partial = norm_vin(_fields(front).get("vin_partial")) or None
        want = norm_vin(expected_vin)
        if vin_full:
            checks.append(_check("首页 VIN", True, value=vin_full))
            if want and vin_full != want:
                checks.append(_check("与工单 VIN 一致", False, value=f"{vin_full} ≠ {want}",
                                     message=f"VIN 与工单不一致：{vin_full} ≠ {want}"))
            elif want:
                checks.append(_check("与工单 VIN 一致", True, value=vin_full))
            else:
                checks.append(_check("与工单 VIN 一致", True, value="未提供 expected_vin",
                                     message="未传 expected_vin，仅校验 VIN 存在，未与工单比对"))
        elif vin_partial:
            if want and want.endswith(vin_partial):
                checks.append(_check("首页 VIN", True, value=f"尾段 {vin_partial}",
                                     message="仅识别到尾段，与工单 VIN 尾段匹配"))
                checks.append(_check("与工单 VIN 一致", True, value=vin_partial))
            else:
                checks.append(_check("首页 VIN", False, value=vin_partial,
                                     message=f"仅识别到尾段 {vin_partial}，无法与工单完整 VIN 比对"))
        else:
            checks.append(_check("首页 VIN", False, message="未识别到首页 VIN"))

    sign = first_scene(items, "service_sign")
    if not sign:
        missing.append("服务单末页")
        checks.append(_check("服务单末页", False, message="缺少服务单末页照片"))
    else:
        labels = _labels(sign)
        signed = "signature" in labels
        stamped = "stamp" in labels
        checks.append(_check("客户签字", signed, value="已签字" if signed else "",
                             message="" if signed else "未检测到客户签字"))
        checks.append(_check("服务站盖章", stamped, value="已盖章" if stamped else "",
                             message="" if stamped else "未检测到服务站盖章"))

    data = {"vin": vin_full, "vin_partial": vin_partial,
            "signature": signed, "stamp": stamped}
    return _finish("inspection", checks, missing,
                   "检查表照片满足需求：首页 VIN、客户签字、服务站盖章齐全", data=data)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate(requirement: str, items: list[dict], *, expected_plate: str = "",
             expected_vin: str = "", expected_odometer: float | None = None,
             axle_type: str = "") -> dict:
    if requirement == "arrival":
        return check_arrival(items, expected_plate=expected_plate, expected_vin=expected_vin,
                             expected_odometer=expected_odometer)
    if requirement == "repair":
        return check_repair(items, axle_type=axle_type)
    if requirement == "inspection":
        return check_inspection(items, expected_vin=expected_vin)
    return {"ok": False, "requirement": requirement, "message": "未知 requirement",
            "missing": [], "checks": []}
