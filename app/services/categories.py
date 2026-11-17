"""按目录三大类聚合结果。

产品把素材分成三类，业务上也是三段不同的校验逻辑：

    01_接车照片    → 车牌 / 行驶证 VIN / 车架 VIN / 里程（四者要能对上）
    02_故障及修复  → 桥与轮胎的加油/放油、有/无滤芯（同一动作只能有一个结论）
    03_项目检查表  → 服务单首页 VIN、末页签字与盖章

所以对外给三个接口，而不是让调用方自己去拼。这里只放纯函数，方便单测。
"""
from __future__ import annotations

import re

from app.services.fields import vin_checksum_ok
from app.services.workorder import _norm, dedupe

ARRIVAL_SCENES = ("arrival_plate", "license_vin", "frame_vin", "odometer")
REPAIR_SCENES = ("axle_oil", "tire_oil", "filter")
INSPECTION_SCENES = ("service_front", "service_sign")

SCENE_LABEL = {
    "arrival_plate": "车头及车牌", "license_vin": "行驶证VIN",
    "frame_vin": "车架贴牌VIN", "odometer": "里程表",
    "axle_oil": "桥油液", "tire_oil": "轮胎油液", "filter": "滤芯",
    "service_front": "服务单首页", "service_sign": "服务单末页",
}

# 逐图结果是按 12 类 slug 给出的，SCENE_LABEL 却是按 group 建的键。
# 少了这张表，bridge_drain/filter_has 会原样回英文 slug，前端直接展示成
# "bridge_drain"，和文档里的"场景中文名"不符。
PHOTO_LABEL = {
    "arrival_plate": "车头及车牌", "license_vin": "行驶证VIN",
    "frame_vin": "车架贴牌VIN", "odometer": "里程表",
    "bridge_drain": "桥放油", "bridge_fill": "桥加油",
    "tire_drain": "轮胎放油", "tire_fill": "轮胎加油",
    "filter_none": "无滤芯", "filter_has": "有滤芯",
    "service_front": "服务单首页", "service_sign": "服务单末页",
}

ACTION_LABEL = {"fill": "加油", "drain": "放油", "has": "有滤芯", "none": "无滤芯"}


def _first(items: list[dict], scene: str) -> dict | None:
    for it in items:
        if it.get("scene") == scene:
            return it
    return None


def _fields(item: dict | None) -> dict:
    return (item or {}).get("fields") or {}


def _labels(item: dict | None) -> set[str]:
    """检测器给出的标签集合（服务单末页用来判断签字/盖章）。"""
    boxes = ((item or {}).get("evidence") or {}).get("boxes") or []
    return {str(b.get("label") or "") for b in boxes if isinstance(b, dict)}


def _trusted_vin(item: dict | None) -> tuple[str | None, bool]:
    """返回 (可信 VIN, 是否有被丢弃的 VIN)。

    校验位不过的值一律不参与一致性判断——否则一张仪表盘上拼出来的
    假 VIN 就能把整单判成"VIN 不一致"。
    """
    fields = _fields(item)
    value = _norm(fields.get("vin"))
    if not value:
        return None, False
    ok = fields.get("vin_checksum_ok")
    if ok is None:
        ok = vin_checksum_ok(value)
    return (value, False) if ok else (None, True)


def _action(item: dict | None) -> str | None:
    return ((item or {}).get("conclusion") or {}).get("action")


ARRIVAL_SCENE_ORDER = ("arrival_plate", "license_vin", "frame_vin", "odometer")
ARRIVAL_SCENE_LABEL = {
    "arrival_plate": "车头及车牌照片",
    "license_vin": "行驶证照片",
    "frame_vin": "车架铭牌照片",
    "odometer": "里程表照片",
}


def summarize_arrival(items: list[dict], expected_plate: str = "",
                      expected_odometer: float | None = None,
                      require_complete: bool = False) -> dict:
    """接车四件套：车牌、双 VIN、里程。

    只判断**实际提交了**的照片：单拍一张车头照时，不会因为「没交行驶证/
    里程表」而把这张好图判成复核。缺哪些照片放在 ``missing_scenes``，
    整单校验由 ``require_complete=True`` 或 ``/v1/workorder`` 负责。

    这样区分了三种情况，人工看到的提示才不会误导：
      - 没交这类照片       → ``missing_scenes``（软提示，不阻断单张判定）
      - 交了但 OCR 没读到 → ``reasons``（这张照片有问题）
      - 读到了但不一致     → ``reasons``（跨图冲突）
    """
    plate_item = _first(items, "arrival_plate")
    lic = _first(items, "license_vin")
    frame = _first(items, "frame_vin")
    odo = _first(items, "odometer")

    plate = _norm(_fields(plate_item).get("plate")) or None
    vin_lic, bad_lic = _trusted_vin(lic)
    vin_frame, bad_frame = _trusted_vin(frame)
    raw_lic = _norm(_fields(lic).get("vin")) or None
    raw_frame = _norm(_fields(frame).get("vin")) or None
    odometer = _fields(odo).get("odometer")

    present = [it.get("scene") for it in (plate_item, lic, frame, odo) if it]
    missing = [s for s in ARRIVAL_SCENE_ORDER if s not in present]
    missing_reasons = [f"未提交{ARRIVAL_SCENE_LABEL[s]}" for s in missing]

    reasons: list[str] = []
    # 把「没读到」和「读到了但校验位不过」分开说：前者是拍摄问题，后者是错字，
    # 人工核对的方式完全不同。混成一句"未识别到"会让人误以为 OCR 没工作。
    if bad_lic:
        reasons.append(f"行驶证 VIN 校验位不通过（{raw_lic}），需人工核对")
    if bad_frame:
        reasons.append(f"车架 VIN 校验位不通过（{raw_frame}），需人工核对")
    # 规格原文：「匹配到车头，未识别车牌号 → 跳过车牌号校验」——不阻断自动审核。
    # 只有"连车头都没匹配到"才算接车照片不合格，由 hints 提示，不阻断。
    hints: list[str] = []
    if not plate:
        hints.append("未识别到车牌，已按规格跳过车牌校验")
    # 只有「交了这张照片」才把读不出字段算问题；没交的照片进 missing_scenes。
    if lic is not None and not vin_lic and not bad_lic:
        reasons.append("未识别到行驶证 VIN")
    if frame is not None and not vin_frame and not bad_frame:
        reasons.append("未识别到车架 VIN")
    if vin_lic and vin_frame and vin_lic != vin_frame:
        reasons.append(f"VIN 不一致：{vin_lic} ≠ {vin_frame}")
    if odo is not None and odometer is None:
        reasons.append("未识别到里程")

    # 规格：车牌与工单不一致 → 不进入自动审核
    if plate and expected_plate:
        want = re.sub(r"[^0-9A-Z\u4e00-\u9fa5]", "", expected_plate.upper())
        got = re.sub(r"[^0-9A-Z\u4e00-\u9fa5]", "", plate.upper())
        if want and got and want != got:
            reasons.append(f"车牌与工单不一致：{plate} ≠ {expected_plate}")
    # 规格：识别到里程需与工单填写里程比对，误差不超过 5%
    if odometer is not None and expected_odometer:
        try:
            diff = abs(float(odometer) - float(expected_odometer)) / float(expected_odometer)
        except (TypeError, ValueError, ZeroDivisionError):
            diff = None
        if diff is not None and diff > 0.05:
            reasons.append(
                f"里程与工单相差 {diff:.1%}（识别 {odometer}，工单 {expected_odometer}），超过 5%")

    # 两个 VIN 都在且一致才算"整单对上了"；只有一个时说明不完整，不算冲突。
    vin_ok = bool(vin_lic and vin_frame and vin_lic == vin_frame)
    complete = not missing and vin_ok
    if require_complete:
        reasons.extend(missing_reasons)

    final_reasons = dedupe(reasons)
    ok = not final_reasons
    if not ok:
        status = "review"
    elif complete:
        status = "pass"
    else:
        status = "partial"

    return {
        "category": "arrival",
        "ok": ok,
        "complete": complete,
        "status": status,
        "plate": plate,
        "vin": {"license": vin_lic, "frame": vin_frame, "match": vin_ok,
                # 供人工核对：校验位不过的原始读数
                "license_unverified": raw_lic if bad_lic else None,
                "frame_unverified": raw_frame if bad_frame else None},
        "odometer": odometer,
        "present": present,
        "missing_scenes": missing,
        "missing_reasons": missing_reasons,
        "reasons": final_reasons,
        "hints": dedupe(hints),
    }


def summarize_repair(items: list[dict], axle_type: str = "") -> dict:
    """修复区：桥/轮胎的加油放油、有/无滤芯。

    **一次完整换油必然同时出现「放油」和「加油」**——先放旧油再加新油。
    早期版本把它判成"矛盾结论"，生产模拟里 5/5 个工单全被误杀。
    所以这里改成：把该场景出现过的动作都列出来，双动作视为正常换油流程，
    只在业务规则被违反（汉德桥放油、非汉德桥判滤芯）或完全判不出时才不给通过。
    """
    def group_of(it: dict) -> str:
        # scene 是 12 类（bridge_drain…），分组要看 scene_group；老数据没有该字段时回退
        return it.get("scene_group") or it.get("scene") or ""

    def actions_of(scene: str) -> list[str]:
        out: list[str] = []
        for it in items:
            if group_of(it) != scene:
                continue
            act = _action(it)
            if act and act not in out:
                out.append(act)
        return out

    axle_actions = actions_of("axle_oil")
    tire_actions = actions_of("tire_oil")
    filter_actions = actions_of("filter")

    def label(acts: list[str]) -> str | None:
        if not acts:
            return None
        return "+".join(ACTION_LABEL.get(a, a) for a in acts)

    reasons: list[str] = []

    # 只有「拍了桥的图但没判出动作」才算问题；压根没拍桥的图不该报"未判定"——
    # 否则只传了轮胎/滤芯照片也会被判失败，用户看到的是一句莫名其妙的话。
    axle_seen = any(group_of(it) == "axle_oil" for it in items)
    if not axle_actions and axle_seen:
        reasons.append("桥的加油/放油未判定")
    if axle_type == "hande" and "drain" in axle_actions:
        reasons.append("汉德桥不应出现桥放油")
    if axle_type == "hande" and tire_actions:
        reasons.append("汉德桥不做轮胎油液判定")
    if axle_type and axle_type != "hande" and filter_actions:
        reasons.append("非汉德桥不做滤芯判定")
    # 规格：非汉德桥需同时存在「桥放油」和「桥加油」才算完整
    if axle_type and axle_type != "hande" and axle_seen:
        if "drain" not in axle_actions:
            reasons.append("非汉德桥缺少桥放油照片")
        if "fill" not in axle_actions:
            reasons.append("非汉德桥缺少桥加油照片")

    present = [it.get("scene") for it in items
               if group_of(it) in ("axle_oil", "tire_oil", "filter")]

    return {
        "category": "repair",
        "ok": not reasons,
        # 单一动作（兼容旧调用方）：只有一个时给值，两个都给时留 None
        "oil": {
            "axle": axle_actions[0] if len(axle_actions) == 1 else None,
            "axle_actions": axle_actions,
            "axle_label": label(axle_actions),
            "tire": tire_actions[0] if len(tire_actions) == 1 else None,
            "tire_actions": tire_actions,
            "tire_label": label(tire_actions),
        },
        "filter": {
            "state": filter_actions[0] if len(filter_actions) == 1 else None,
            "actions": filter_actions,
            "label": label(filter_actions),
        },
        "present": present,
        "reasons": dedupe(reasons),
    }


def summarize_inspection(items: list[dict], expected_vin: str = "") -> dict:
    """检查表：首页 VIN（常见只手写尾段）+ 末页签字盖章。

    ``expected_vin`` 是工单声明的 VIN：只在首页读到完整 VIN 时比对；
    读不出或只有尾段时仍按原规则转复核，不拿空值与工单比。
    """
    front = _first(items, "service_front")
    sign = _first(items, "service_sign")

    fields = _fields(front)
    vin = _norm(fields.get("vin")) or None
    partial = _norm(fields.get("vin_partial")) or None

    labels = _labels(sign)
    signature = "signature" in labels
    stamp = "stamp" in labels

    reasons: list[str] = []
    if not vin and not partial:
        reasons.append("服务单首页未识别到 VIN")
    elif not vin and partial:
        reasons.append(f"VIN 仅识别到尾段 {partial}")
    if vin and fields.get("vin_checksum_ok") is False:
        reasons.append(f"服务单 VIN 校验位不通过（{vin}），需人工核对")
    if vin and expected_vin:
        want = _norm(expected_vin)
        if want and want != vin:
            reasons.append(f"服务单 VIN 与预期不一致：{vin} ≠ {expected_vin}")
    if sign is None:
        reasons.append("缺少服务单末页")
    else:
        if not signature:
            reasons.append("未检测到客户签字")
        if not stamp:
            reasons.append("未检测到服务站盖章")

    return {
        "category": "inspection",
        "ok": not reasons,
        "service_front_vin": vin,
        "service_front_vin_partial": partial,
        "signature": signature,
        "stamp": stamp,
        "present": [it.get("scene") for it in (front, sign) if it],
        "reasons": dedupe(reasons),
    }
