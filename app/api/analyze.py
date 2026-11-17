from __future__ import annotations

import asyncio
import io
import json
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps

from app.config import settings
from app.limits import read_image
from app.metrics import metrics
from app.schemas import AnalyzeResponse
from app.services.fields import vin_checksum_ok
from app.services.kev_client import KevJudge
from app.services.ocr_client import OCRClient
from app.services.preprocess import normalize
from app.services.vision_client import VisionClient
from app.services.vlx_client import VLXClient

router = APIRouter(tags=["analyze"])

CONFIG = json.loads(settings.config_path.read_text(encoding="utf-8"))
CLASS_MAP = CONFIG["class_map"]
BY_SLUG = {v["slug"]: {**v, "folder": k} for k, v in CLASS_MAP.items()}

VISION = VisionClient(settings.vision_url, settings.scene_timeout,
                      settings.detect_timeout)
OCR = OCRClient(settings.ocr_service_url, settings.ocr_timeout)
VLX = VLXClient(settings.vlx_url, 10.0)
# 兜底用的短超时：VLX 慢的时候（实测 P95 5s+）宁可放弃，不要让末页卡住
VLX_FALLBACK = VLXClient(settings.vlx_url, 4.0)
KEV = KevJudge()
SEM = asyncio.Semaphore(settings.max_concurrent)


def _box_xyxy(b):
    """Normalize detector box payloads: {box:[x1,y1,x2,y2]}, {xmin..}, or [x1,y1,x2,y2]."""
    if isinstance(b, dict):
        if b.get("box") is not None:
            vals = b["box"]
            return [float(v) for v in vals[:4]] if len(vals) >= 4 else None
        keys = ("xmin", "ymin", "xmax", "ymax")
        if all(b.get(k) is not None for k in keys):
            return [float(b[k]) for k in keys]
        return None
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return [float(v) for v in b[:4]]
    return None


def _largest_box(items, wanted):
    matches = []
    for b in items or []:
        label = str(b.get("label") or "")
        if any(w in label for w in wanted):
            xy = _box_xyxy(b)
            if xy:
                matches.append(xy)
    if not matches:
        return None
    return max(matches, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))


def _detect_box(data: bytes, filename: str, text: str, wanted,
                alt_data: bytes | None = None):
    """Locate the target box; retry on exposure-corrected pixels if missing.

    Detection is tuned for untouched photos, so the enhanced frame is only
    consulted when the original produced nothing (common on dark frames).
    """
    for source in ((data, alt_data) if alt_data else (data,)):
        try:
            # 试过把阈值降到 0.10 兜底，实测更差：会框到一个坏框，
            # OCR 在坏框上反复重试，反而把 P95 顶到超时。保持默认阈值。
            det = VISION.detect(source, filename, text)
            box = _largest_box(det.get("boxes") or [], wanted)
            if box:
                return box
        except Exception:
            pass
    # VLX 在内网，未启用时不要碰：它的超时是秒级，防火墙 DROP 时
    # 车牌/里程场景每次都要等满，等于给 P95 挖坑。
    if not settings.use_vlx or not settings.vlx_url:
        return None
    try:
        det = VLX.detect(data, filename, text)
        return _largest_box(det.get("result_bbox_list") or [], wanted)
    except Exception:
        return None


# 服务单末页的签字/盖章检测阈值。
# 签字框通常只有 100~200px，检测器默认 0.25 会把一半真签字漏掉
# （用户 13 张末页实测：0.25 → 7 张，0.10 → 11 张）；盖章本来就稳，保持 0.25。
SIGN_CONF = 0.10
STAMP_CONF = 0.25
# 整页拍歪 90°/270° 时检测器会全空，用旋转兜底；旋转是猜测，
# 所以门槛提高，避免在转错方向时凭空造出一个签字/盖章。
SIGN_ROTATED_CONF = 0.30
STAMP_ROTATED_CONF = 0.50

SIGN_LABELS = ("signature", "stamp", "service_sheet")


def _label_boxes(raw: dict) -> list[dict]:
    """把检测结果收敛成 {label, score, box}，只留末页关心的三类。"""
    out = []
    for b in raw.get("boxes") or []:
        label = str(b.get("label") or "")
        box = list(b.get("box") or [])[:4]
        if label not in SIGN_LABELS or len(box) < 4:
            continue
        out.append({"label": label, "score": float(b.get("score") or 0.0),
                    "box": [float(v) for v in box]})
    return out


def _service_sign_boxes(data: bytes, filename: str,
                        deadline: float | None = None) -> tuple[list[dict], int | None]:
    """末页签字/盖章：低阈值检测 → VLX 兜底 → 旋转兜底。

    顺序按性价比排：VLX 缩图后 ~1.5s 且能认出小号手写签字；
    旋转只救"整页拍歪"的个例（~1/34），放在最后，预算不够就跳过。

    ``deadline`` 是 time.monotonic() 的绝对截止时间（来自单图分析预算），
    所有兜底都必须在预算内完成，否则整张图会被判成超时降级。
    """
    def left() -> float:
        return 1e9 if deadline is None else deadline - time.monotonic()

    def scan(payload: bytes) -> list[dict]:
        try:
            return _label_boxes(VISION.detect(payload, filename, "", SIGN_CONF))
        except Exception:
            return []

    boxes = scan(data)

    def found(label: str) -> bool:
        limit = SIGN_CONF if label == "signature" else STAMP_CONF
        return any(b["label"] == label
                   and (b.get("source") == "vlx" or b["score"] >= limit)
                   for b in boxes)

    def missing() -> set[str]:
        return {label for label in ("signature", "stamp") if not found(label)}

    if not missing():
        return boxes, None

    if left() > 1.0:
        boxes = boxes + _vlx_sign_boxes(data, filename, min(4.0, left() - 0.3))

    if not missing():
        return boxes, None

    try:
        width, height = _image_size(data)
    except Exception:
        return boxes, None

    used_angle = None
    for angle in (90, 270):
        if left() < 1.2:
            break
        try:
            alt = scan(_rotate_image(data, angle))
        except Exception:
            continue
        extra = []
        for b in alt:
            if b["label"] not in missing():
                continue
            limit = SIGN_ROTATED_CONF if b["label"] == "signature" else STAMP_ROTATED_CONF
            if b["score"] < limit:
                continue
            x1, y1, x2, y2 = _box_to_original(b["box"], angle, width, height)
            extra.append({**b, "box": [x1, y1, x2, y2]})
        if extra:
            boxes.extend(extra)
            used_angle = angle
        if not missing():
            break
    return boxes, used_angle


VLX_LABELS = {"客户签字": "signature", "服务站盖章": "stamp",
              "签字": "signature", "盖章": "stamp"}
# VLX 的耗时和图片像素线性相关：1080x2270 的末页要 7s+，缩到 1600 长边只要 1.5s，
# 而签字/盖章依然认得出来（再缩到 1000 会丢签字）。
VLX_SIGN_MAX_SIDE = 1600


def _vlx_sign_payload(data: bytes) -> tuple[bytes, float]:
    """把末页缩到 VLX 能快速处理的大小，返回 (bytes, 缩放系数)。"""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    width, height = img.size
    scale = min(1.0, VLX_SIGN_MAX_SIDE / max(width, height))
    if scale < 1.0:
        img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), scale


def _vlx_sign_boxes(data: bytes, filename: str, timeout: float = 4.0) -> list[dict]:
    """末页签字/盖章兜底：VLX 只补本地检测器缺的那一项。

    实测（34 张检测器没见过的末页）：本地 30/34 签字，VLX 33/34；
    但 VLX 在没签字的首页上会误报约 10%，所以**只在缺项时调用**，
    且失败/超时一律忽略，不影响主链路。
    """
    if not settings.vlx_sign_fallback or not settings.vlx_url:
        return []
    try:
        payload, scale = _vlx_sign_payload(data)
        base_timeout = getattr(VLX_FALLBACK, "timeout", 0.0)
        client = VLX_FALLBACK if timeout >= base_timeout else VLXClient(settings.vlx_url, timeout)
        resp = client.recognize(payload, filename, "service_sign")
    except Exception:  # noqa: BLE001 - 兜底失败不能影响主结果
        return []
    out = []
    for b in resp.get("result_bbox_list") or []:
        label = VLX_LABELS.get(str(b.get("label") or "").strip())
        if not label:
            continue
        # 框坐标乘回原图尺度，evidence 里画框不会缩水
        k = 1.0 / scale if scale else 1.0
        out.append({"label": label, "score": 0.0, "source": "vlx",
                    "box": [float(b.get("xmin") or 0) * k, float(b.get("ymin") or 0) * k,
                            float(b.get("xmax") or 0) * k, float(b.get("ymax") or 0) * k]})
    return out


DET_SLUG = [
    (("license_plate", "vehicle_front"), "arrival_plate"),
    (("driving_license",), "license_vin"),
    (("vin_plate",), "frame_vin"),
    (("odometer_display",), "odometer"),
    (("signature", "stamp"), "service_sign"),
    (("service_sheet",), "service_front"),
    (("filter_housing", "filter_element"), "filter"),
    (("axle_housing", "oil_stream", "oil_pan", "oil_gun", "oil_hose",
      "oil_drum", "drain_plug"), "axle_oil"),
    (("wheel_hub",), "tire_oil"),
]

# Scene-level pseudo slugs used only when the detector rescues a low-confidence scene.
# They are not dataset folders; _oil_or_filter handles them without a classifier action.
BY_SLUG.update({
    "axle_oil": {"slug": "axle_oil", "scene": "axle_oil", "group": "axle_oil"},
    "tire_oil": {"slug": "tire_oil", "scene": "tire_oil", "group": "tire_oil"},
    "filter": {"slug": "filter", "scene": "filter", "group": "filter"},
})


def _route_by_detector(data: bytes, filename: str, alt_data: bytes | None = None):
    hits = {}
    for source in ((data, alt_data) if alt_data else (data,)):
        try:
            det = VISION.detect(source, filename, "")
        except Exception:
            continue
        for b in det.get("boxes") or []:
            label = str(b.get("label") or "")
            score = float(b.get("score") or 0.0)
            if score >= 0.35:
                hits[label] = max(hits.get(label, 0.0), score)
        if hits:
            break
    for wanted, slug in DET_SLUG:
        if hits.keys() & set(wanted):
            return slug
    return None


def _rotate_image(data: bytes, angle: int) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if angle:
        img = img.rotate(angle, expand=True)
    buf = io.BytesIO()
    # PNG 在 2000px 以上的手机上会到 16MB，直接超过 vision 服务 15MB 上限，
    # 旋转兜底会静默失败（表现为"转了也没用"）。统一用 JPEG q92。
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _orient(data: bytes, filename: str):
    """Return (bytes, scene, angle) for the most likely upright orientation.

    Phones frequently upload 90/180/270-rotated photos; the classifier is
    trained on upright shots, so retry rotations only when confidence is low.
    """
    scene = VISION.scene(data, filename)
    if float(scene.get("confidence") or 0.0) >= settings.min_scene_conf:
        return data, scene, 0
    best_data, best_scene, best_angle = data, scene, 0
    best_conf = float(scene.get("confidence") or 0.0)
    for angle in (180, 90, 270):
        try:
            rotated = _rotate_image(data, angle)
            candidate = VISION.scene(rotated, filename)
        except Exception:
            continue
        conf = float(candidate.get("confidence") or 0.0)
        if conf > best_conf:
            best_data, best_scene, best_angle, best_conf = rotated, candidate, angle, conf
        if best_conf >= 0.85:
            break
    return best_data, best_scene, best_angle


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def _box_to_original(box, angle: int, width: int, height: int):
    """Map a box from the rotated frame back to the uploaded image frame."""
    if not box or not angle:
        return box
    x1, y1, x2, y2 = box
    if angle == 180:  # (x,y) -> (W-x, H-y)
        return [width - x2, height - y2, width - x1, height - y1]
    if angle == 90:  # (x,y) -> (y, W-x)
        return [width - y2, x1, width - y1, x2]
    if angle == 270:  # (x,y) -> (H-y, x)
        return [y1, height - x2, y2, height - x1]
    return box


def _map_boxes_to_original(result: dict, angle: int, width: int, height: int):
    evidence = result.get("evidence") or {}
    if evidence.get("box"):
        evidence["box"] = _box_to_original(evidence["box"], angle, width, height)
    for key in ("boxes",):
        items = evidence.get(key)
        if isinstance(items, list):
            for b in items:
                if isinstance(b, dict) and b.get("xmin") is not None:
                    mapped = _box_to_original(
                        [b["xmin"], b["ymin"], b["xmax"], b["ymax"]],
                        angle, width, height)
                    b["xmin"], b["ymin"], b["xmax"], b["ymax"] = mapped
    evidence["orientation"] = angle
    result["evidence"] = evidence


def _expand_box(box, ratio: float, width: int | None = None, height: int | None = None):
    if not box:
        return None
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * ratio, (y2 - y1) * ratio
    out = [max(0.0, x1 - px), max(0.0, y1 - py), x2 + px, y2 + py]
    if width:
        out[2] = min(width, out[2])
    if height:
        out[3] = min(height, out[3])
    return out


def _detector_hits(data: bytes, filename: str) -> dict[str, float]:
    try:
        det = VISION.detect(data, filename, "")
    except Exception:
        return {}
    out: dict[str, float] = {}
    for b in det.get("boxes") or []:
        label = str(b.get("label") or "")
        score = float(b.get("score") or 0.0)
        out[label] = max(out.get(label, 0.0), score)
    return out


def _detector_state(det: dict[str, float], scene: str):
    """Turn detector labels into a fill/drain or has/none vote (or None)."""
    def score(*names):
        return max((det.get(n, 0.0) for n in names), default=0.0)

    if scene == "filter":
        if score("filter_element") >= 0.45:
            return "has"
        if score("filter_housing") >= 0.45:
            return "none"
        return None
    fill = score("oil_gun", "oil_hose")
    drain = score("oil_stream", "oil_pan", "drain_plug")
    if max(fill, drain) < 0.40 or abs(fill - drain) < 0.15:
        return None
    return "fill" if fill > drain else "drain"


def _oil_or_filter(slug: str, data: bytes, filename: str,
                   axle_type: str, expected_action: str,
                   confidence: float = 0.0,
                   scene_confidence: float | None = None) -> dict:
    meta = BY_SLUG[slug]
    scene = meta["scene"]
    reasons: list[str] = []

    classifier_action = {"bridge_drain": "drain", "bridge_fill": "fill",
                         "tire_drain": "drain", "tire_fill": "fill"}.get(slug)
    classifier_installed = {"filter_has": True, "filter_none": False}.get(slug)
    confident = confidence >= settings.strong_scene_conf

    vlx = None
    kev_judgment = None
    det = _detector_hits(data, filename)
    det_state = _detector_state(det, scene)
    sources: list[str] = []

    def vlx_value():
        nonlocal vlx
        if not settings.use_vlx or vlx is False:
            return None
        try:
            vlx = VLX.roi(data, filename, scene)
        except Exception as exc:
            vlx = False
            reasons.append(f"VLX 调用失败: {type(exc).__name__}")
            return None
        evidence = vlx.get("evidence") or {}
        if scene == "filter":
            installed = evidence.get("filter_installed")
            if installed is None and vlx.get("verdict") in ("has", "none"):
                installed = vlx["verdict"] == "has"
            return "has" if installed else ("none" if installed is False else None)
        fill, drain = evidence.get("fill"), evidence.get("drain")
        if fill is None and drain is None and vlx.get("verdict") in ("fill", "drain"):
            fill, drain = vlx["verdict"] == "fill", vlx["verdict"] == "drain"
        if fill and not drain:
            return "fill"
        if drain and not fill:
            return "drain"
        return None

    # Strong classifier: decide locally, do not pay the VLX round trip.
    if confident and (classifier_action or classifier_installed is not None):
        value = classifier_action if scene != "filter" else (
            "has" if classifier_installed else "none")
        sources.append("classifier")
        if det_state == value:
            sources.append("detector")
    else:
        value = vlx_value()
        if value:
            sources.append("vlx")
        if not value and classifier_action:
            value = classifier_action
            sources.append("classifier")
        if not value and classifier_installed is not None:
            value = "has" if classifier_installed else "none"
            sources.append("classifier")
        if det_state and not value:
            value = det_state
            sources.append("detector")
        elif det_state and value == det_state:
            sources.append("detector")

        if det_state and value and det_state != value:
            reasons.append("检测器与判定结果冲突")
        if value is None:
            reasons.append("动作/状态证据不足")

    # kev is a text-only arbiter: only useful when VLX produced two-sided scores.
    if value is None and KEV.enabled and scene != "filter" and isinstance(vlx, dict):
        ev = vlx.get("evidence") or {}
        try:
            kev_judgment = KEV.arbitrate_oil({
                "fill": ev.get("fill"), "drain": ev.get("drain"),
                "detector_labels": [k for k, v in det.items() if v >= 0.40],
            })
        except Exception as exc:
            kev_judgment = {"error": f"{type(exc).__name__}: {exc}"}
        if (kev_judgment and kev_judgment.get("action") in ("drain", "fill")
                and float(kev_judgment.get("confidence") or 0) >= 0.60):
            value = kev_judgment["action"]
            sources.append("kev")
            reasons.append("kev 文本仲裁采纳")

    label = {"fill": "加油", "drain": "放油",
             "has": "有滤芯", "none": "无滤芯"}.get(value, "待判定")
    if value is not None and not confident:
        reasons.append(f"动作置信度 {confidence:.2f} 偏低，建议复核")
    # Softer gate than the action-confidence one: a low scene score with a
    # confident in-group head is usually right, so only flag clearly weak scenes.
    if (value is not None and scene_confidence is not None
            and scene_confidence < settings.weak_scene_conf):
        reasons.append(f"场景置信度 {scene_confidence:.2f} 偏低，建议复核")

    if expected_action and value and value != expected_action:
        reasons.append(f"与工单声明动作({expected_action})不一致")
    if axle_type == "hande" and value == "drain" and scene == "axle_oil":
        reasons.append("汉德桥不应出现桥放油动作")
    if axle_type == "hande" and scene == "tire_oil":
        reasons.append("汉德桥不做轮胎油液判定")
    if axle_type and axle_type != "hande" and scene == "filter":
        reasons.append("非汉德桥不做滤芯判定")

    return {
        # 对外回传 12 类场景（与素材目录一一对应），粗粒度分组单独给 scene_group。
        # 早期这里只回传分组（bridge_drain -> axle_oil），调用方拿目录标签对不上。
        "scene": slug,
        "scene_group": scene,
        "conclusion": {"action": value, "label": label},
        "evidence": {
            "classifier": slug,
            "classifier_confidence": round(confidence, 4),
            "source": sources,
            "detector": {k: round(v, 3) for k, v in det.items()} if det else {},
            "vlx": (vlx.get("evidence") if isinstance(vlx, dict) else None),
            "vlx_answers": (vlx.get("answers") if isinstance(vlx, dict) else None),
            "kev": kev_judgment,
            "boxes": (vlx.get("result_bbox_list") or []) if isinstance(vlx, dict) else [],
            "anchor_box": (vlx.get("anchor_box") if isinstance(vlx, dict) else None),
        },
        "need_review": bool(reasons),
        "review_reasons": reasons,
        "statement": f"视觉分类：{slug}（{confidence:.2f}）；判定：{label}"
                     + (f"；证据：{'+'.join(sources)}" if sources else ""),
    }


OIL_GROUPS = ("axle_oil", "tire_oil", "filter")
BINARY_KEY = {"axle_oil": "axle", "tire_oil": "tire", "filter": "filter"}
BINARY_SLUG = {"bridge_drain": "bridge_drain", "bridge_fill": "bridge_fill",
               "tire_drain": "tire_drain", "tire_fill": "tire_fill",
               "filter_has": "filter_has", "filter_none": "filter_none"}
GROUP_WEIGHT_12 = 0.25


def _fuse_group(slug: str, data: bytes, filename: str, scene_probs: dict | None):
    """Resolve an oil/filter scene with the dedicated 3-way group head.

    Measured on the held-out split: the 12-class head only reaches 75% on the
    axle/tyre/filter split, while the group head reaches 88%. They disagree
    mostly on genuinely ambiguous photos, so a disagreement is also surfaced
    as a review reason instead of silently trusting the winner.
    """
    scene = BY_SLUG[slug]["scene"]
    if scene not in OIL_GROUPS:
        return None, None, None
    try:
        grp = VISION.group(data, filename)
    except Exception as exc:
        return None, None, {"error": f"{type(exc).__name__}: {exc}"}

    group = grp.get("class")
    if group not in OIL_GROUPS:
        return None, None, grp

    # Marginalise the 12-class distribution onto the three oil groups.
    probs = scene_probs or {}
    from12 = []
    for g in OIL_GROUPS:
        total = 0.0
        for name, p in probs.items():
            meta = BY_SLUG.get(name)
            if meta and meta["scene"] == g:
                total += float(p)
        from12.append(total)
    total12 = sum(from12) or 1.0
    from12 = [v / total12 for v in from12]

    from_group = [0.0, 0.0, 0.0]
    for item in grp.get("top5") or []:
        name = item.get("class")
        if name in OIL_GROUPS:
            from_group[OIL_GROUPS.index(name)] = float(item.get("confidence") or 0.0)
    tot = sum(from_group) or 1.0
    from_group = [v / tot for v in from_group]

    fused = [GROUP_WEIGHT_12 * a + (1 - GROUP_WEIGHT_12) * b
             for a, b in zip(from12, from_group)]
    winner = OIL_GROUPS[max(range(3), key=lambda i: fused[i])]
    disagreed = winner != scene
    return winner, grp, {"disagreed": disagreed, "winner": winner,
                         "vision_scene": scene, "group": grp}


IN_GROUP_CLASSES = {
    "axle_oil": ("bridge_drain", "bridge_fill"),
    "tire_oil": ("tire_drain", "tire_fill"),
    "filter": ("filter_none", "filter_has"),
}
# How much to trust the in-group 2-way head over the flat 6-way head.
IN_GROUP_WEIGHT = {"axle_oil": 0.5, "tire_oil": 0.5, "filter": 0.6}


def _blend_in_group(group: str, head: dict, six: dict):
    """Fuse the 2-way in-group head with the 6-way head renormalised to the group."""
    classes = IN_GROUP_CLASSES.get(group)
    if not classes:
        return None, None
    weight = IN_GROUP_WEIGHT.get(group, 0.5)

    from_head = {}
    for item in head.get("top5") or []:
        name = item.get("class")
        if name in classes:
            from_head[name] = float(item.get("confidence") or 0.0)
    total = sum(from_head.values()) or 1.0
    from_head = {k: v / total for k, v in from_head.items()}

    from_six = {}
    for item in six.get("top5") or []:
        name = item.get("class")
        if name in classes:
            from_six[name] = float(item.get("confidence") or 0.0)
    total = sum(from_six.values()) or 1.0
    from_six = {k: v / total for k, v in from_six.items()}

    fused = {c: weight * from_head.get(c, 0.0) + (1 - weight) * from_six.get(c, 0.0)
             for c in classes}
    winner = max(fused, key=lambda c: fused[c])
    return winner, round(fused[winner], 4)


ACTION_SCENES = {"axle_oil", "tire_oil", "filter"}


def _resolve_action(slug: str, data: bytes, filename: str, confidence: float,
                    oriented_data: bytes, angle: int):
    """Refine an oil/filter scene with the dedicated 6-class action model."""
    scene = BY_SLUG[slug]["scene"]
    if scene not in ACTION_SCENES:
        return slug, confidence, None, None
    try:
        # Preferred path: the group head fixes the family, then a 2-way in-group
        # head only has to separate fill/drain (or has/none). Measured on the
        # held-out split this is decisive: 80.7% vs 75.4% for the flat 6-way head.
        group = BY_SLUG[slug]["scene"]
        head = None
        try:
            head = VISION.binary(data, filename, BINARY_KEY[group])
        except Exception:
            head = None
        if head and head.get("class") in BINARY_SLUG:
            # Blend the 2-way head with the flat 6-way head restricted to this
            # group: the 2-way head is sharper, the 6-way head is steadier on
            # rare framings, and their blend measured best on the held-out split.
            six = None
            try:
                six = VISION.action(data, filename)
            except Exception:
                six = None
            act_slug_forced = head["class"]
            act_conf = float(head.get("confidence") or 0.0)
            if six:
                fused, conf = _blend_in_group(group, head, six)
                if fused:
                    act_slug_forced, act_conf = fused, conf
            act = head
            conflict = (f"分组({group})内判定({act_slug_forced})与场景({slug})不一致"
                        if act_slug_forced != slug else None)
            return act_slug_forced, act_conf, act, conflict

        act = VISION.action(data, filename)
        # If the orchestrator rotated the photo, the action model may see the
        # correct evidence only on the rotated copy (upside-down shots).
        if angle:
            rotated = VISION.action(oriented_data, filename)
            if (BY_SLUG.get(rotated.get("class"), {}).get("scene") == scene
                    and rotated.get("class") == slug):
                act = rotated
    except Exception as exc:
        return slug, confidence, {"error": f"{type(exc).__name__}: {exc}"}, None
    act_slug = act.get("class")
    act_conf = float(act.get("confidence") or 0.0)
    if BY_SLUG.get(act_slug, {}).get("scene") != scene:
        return slug, confidence, act, f"动作模型({act_slug})与场景({scene})不一致"
    conflict = (f"场景分类({slug})与动作模型({act_slug})不一致"
                if act_slug != slug else None)
    return act_slug, act_conf, act, conflict


def _character(slug: str, data: bytes, filename: str,
               alt_data: bytes | None = None,
               deadline: float | None = None) -> dict:
    meta = BY_SLUG[slug]
    scene = meta["scene"]
    reasons: list[str] = []

    if scene in ("license_vin", "frame_vin", "service_front"):
        keep = "vin"
        require_label = scene == "service_front"
        # 先裁到证件/铭牌/服务单再 OCR。
        # 整张 1080×1440 直接 OCR 要 0.9s，一个角度就顶满预算；
        # 裁到目标区域后单次只要 0.1–0.2s，4 个角度都跑得起。
        wanted = {"license_vin": ("driving_license", "行驶证"),
                  "frame_vin": ("vin_plate", "车架铭牌"),
                  "service_front": ("service_sheet", "服务单")}[scene]
        box = _detect_box(data, filename, wanted[1], wanted, 
                          alt_data if scene != "service_front" else None)
        if box:
            box = _expand_box(box, 0.06)
    elif scene == "arrival_plate":
        keep = "plate"
        require_label = False
        box = _detect_box(data, filename, "车牌", ("license_plate", "车牌"), alt_data)
        if box:
            box = _expand_box(box, 0.12)
    elif scene == "odometer":
        keep = "odometer"
        require_label = False
        box = _detect_box(data, filename, "里程表", ("odometer_display", "里程表", "仪表盘"))
        if box:
            box = _expand_box(box, 0.18)

    else:
        if settings.use_vlx:
            vlx = VLX.recognize(data, filename, "service_sign")
            return {
                "scene": "service_sign", "scene_group": "service_sign",
                "conclusion": None,
                "evidence": {"classifier": slug,
                             "boxes": vlx.get("result_bbox_list") or []},
                "fields": None,
                "need_review": bool(vlx.get("need_review", True)),
                "review_reasons": [],
                "statement": vlx.get("message") or "服务单末页检测完成",
            }
        raw_boxes, rotated = _service_sign_boxes(data, filename, deadline)
        boxes = [{"label": b["label"], "score": round(b["score"], 4),
                  "source": b.get("source", "detector"),
                  "xmin": b["box"][0], "ymin": b["box"][1],
                  "xmax": b["box"][2], "ymax": b["box"][3]}
                 for b in raw_boxes]
        # VLX 兜底框没有检测分数，按「已确认」处理，不再过阈值
        has_sig = any(b["label"] == "signature"
                      and (b.get("source") == "vlx" or b["score"] >= SIGN_CONF)
                      for b in raw_boxes)
        has_stamp = any(b["label"] == "stamp"
                        and (b.get("source") == "vlx" or b["score"] >= STAMP_CONF)
                        for b in raw_boxes)
        reasons = []
        if not has_sig:
            reasons.append("未检测到客户签字")
        if not has_stamp:
            reasons.append("未检测到服务站盖章")
        return {
            "scene": "service_sign", "scene_group": "service_sign",
            "conclusion": None,
            "evidence": {"classifier": slug, "boxes": boxes,
                         # 旋转兜底命中时记录角度，便于排查"框画歪了"
                         "rotated_detection": rotated},
            "fields": None,
            "need_review": bool(reasons),
            "review_reasons": reasons,
            "statement": f"服务单末页：签字{'有' if has_sig else '未检出'}，"
                         f"盖章{'有' if has_stamp else '未检出'}。",
        }

    try:
        ocr = OCR.extract(data, filename, keep=keep, box=box,
                          require_vin_label=require_label)
        fields = ocr.get("fields") or {}
        text = ocr.get("text") or ""
        error = ocr.get("error")
    except Exception as exc:
        fields, text, error = {"vin": None, "plate": None, "odometer": None}, "", f"{type(exc).__name__}: {exc}"

    # Plates: exposure correction helps on dark/glare frames but can hurt on
    # well-exposed ones, so retry with the enhanced pixels only when the
    # untouched frame produced nothing.
    if keep == "plate" and not fields.get(keep) and alt_data:
        try:
            alt = OCR.extract(alt_data, filename, keep=keep, box=box,
                              require_vin_label=require_label)
            alt_fields = alt.get("fields") or {}
            if alt_fields.get(keep):
                fields, text = alt_fields, alt.get("text") or text
                error = alt.get("error")
        except Exception:
            pass

    # A dashboard photo never carries a 17-char VIN. When the odometer read
    # fails but the frame does contain one, the picture is almost certainly a
    # driving-licence shot that the classifier routed to the odometer class.
    #
    # 但这条兜底必须先过校验位：新能源车仪表盘上满是 128.3 / 210 / 25.2V 这类
    # 数字，拼出来的 17 位串能通过"形状"检查，却过不了 ISO 校验位。
    # 实测一张电动车仪表盘被兜底成 license_vin，并给整车 VIN 制造了假冲突。
    if (keep == "odometer" and not fields.get("odometer")
            and settings.odometer_vin_fallback):
        try:
            probe = OCR.extract(data, filename, keep="vin")
            probe_fields = probe.get("fields") or {}
            candidate = probe_fields.get("vin")
            if candidate and vin_checksum_ok(candidate):
                looked = _character("license_vin", data, filename)
                looked["rerouted_from"] = "odometer"
                looked["evidence"]["ocr_text"] = probe.get("text") or ""
                return looked
        except Exception:
            pass

    value = fields.get(keep)
    partial = fields.get("vin_partial") if keep == "vin" else None
    if not value and partial:
        reasons.append(f"VIN 仅识别到尾段 {partial}，需人工补全")
    elif not value:
        reasons.append(f"未识别到{keep}")
    elif keep == "vin":
        # 结构像 VIN 但校验位不对：大多是把车架贴牌上的说明文字读成了 VIN。
        # 不丢弃（部分国产 VIN 不遵守校验位），但必须转人工，不能静默放行。
        # 这里本地再算一次，不依赖 OCR 服务回传该字段——两个服务版本不一致时
        # 旧 OCR 镜像不会带 vin_checksum_ok，静默放行的风险比多算一次更大。
        ok = fields.get("vin_checksum_ok")
        if ok is None:
            ok = vin_checksum_ok(value)
        if not ok:
            reasons.append(f"VIN 校验位不通过（{value}），疑似误读，需人工确认")

    if value and keep == "plate" and len(value) == 8 and value[-1] not in "DF":
        reasons.append("新能源车牌末位应为 D/F，识别结果需复核")

    if keep == "plate":
        statement = (f"匹配接车照片，识别到车牌号 {value}" if value
                     else "匹配接车照片，未识别到车牌号")
    elif keep == "odometer":
        statement = (f"识别到行驶里程 {value} km" if value
                     else "未识别到里程，无法进入自动审核")
    elif value:
        statement = f"识别到 VIN：{value}"
    elif partial:
        statement = f"仅识别到 VIN 尾段 {partial}，服务单未记录完整 VIN"
    else:
        statement = "未识别到 VIN，无法进入自动审核"

    return {
        "scene": slug,
        "scene_group": scene,
        "conclusion": None,
        "evidence": {"classifier": slug, "ocr_text": text, "box": box},
        "fields": fields,
        "need_review": bool(reasons),
        "review_reasons": reasons,
        "statement": statement,
        "ocr_error": error,
    }


def _apply_expectations(result: dict, expected_vin: str, expected_plate: str,
                        expected_odometer: str):
    """Compare extracted fields with the work-order values (spec 5% mileage rule)."""
    fields = result.get("fields") or {}
    reasons = result.setdefault("review_reasons", [])

    exp_vin = (expected_vin or "").strip().upper()
    if exp_vin:
        got = (fields.get("vin") or "").upper()
        partial = (fields.get("vin_partial") or "").upper()
        if got and got != exp_vin:
            reasons.append(f"VIN 与工单不一致：{got} ≠ {exp_vin}")
        elif not got and partial and not exp_vin.endswith(partial):
            reasons.append(f"VIN 尾段 {partial} 与工单不一致")

    exp_plate = (expected_plate or "").strip().upper()
    if exp_plate:
        got = (fields.get("plate") or "").upper()
        if got and got != exp_plate:
            reasons.append(f"车牌与工单不一致：{got} ≠ {exp_plate}")

    if expected_odometer:
        try:
            got = float(fields.get("odometer"))
            exp = float(expected_odometer)
            deviation = abs(got - exp) / exp if exp else 1.0
            if deviation > 0.05:
                reasons.append(f"里程 {got:.0f} 与工单 {exp:.0f} 偏差 {deviation:.1%}，超过 5%")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    if reasons:
        result["need_review"] = True


async def analyze_bytes(data: bytes, filename: str = "image.png",
                        work_order_id: str = "", axle_type: str = "",
                        expected_action: str = "", expected_vin: str = "",
                        expected_plate: str = "",
                        expected_odometer: str = "") -> dict:
    """Core single-photo analysis, shared by /v1/analyze and /v1/workorder."""
    t0 = time.time()
    metrics.start()
    try:
        async with SEM:
            try:
                original_data = data
                original_size = _image_size(data)
                # Size-preserving exposure fix. Only the classification heads
                # see the enhanced pixels: measured on the held-out split, the
                # detector and OCR do better on the untouched frame.
                cls_src, pre_report = await asyncio.to_thread(
                    normalize, data, settings.preprocess)
                cls_data, scene, angle = await asyncio.to_thread(
                    _orient, cls_src, filename)
                data = await asyncio.to_thread(
                    _rotate_image, original_data, angle) if angle else original_data
            except Exception as exc:
                raise HTTPException(502, f"vision service failed: {exc}")

            slug = scene.get("class")
            if slug not in BY_SLUG:
                raise HTTPException(400, f"unknown classifier class: {slug}")

            confidence = float(scene.get("confidence") or 0.0)
            if confidence < settings.min_scene_conf:
                routed = await asyncio.to_thread(
                    _route_by_detector, data, filename,
                    cls_data if pre_report.get("applied") else None)
                if not routed:
                    result = {
                        "scene": "unknown_scene",
                        "scene_group": "unknown_scene",
                        "scene_confidence": confidence,
                        "model_version": scene.get("model_version"),
                        "scene_top5": scene.get("top5"),
                        "conclusion": None,
                        "evidence": {"classifier": slug},
                        "need_review": True,
                        "review_reasons": [f"场景置信度 {confidence:.2f} < {settings.min_scene_conf}"],
                        "statement": "场景不确定，建议人工确认或补拍。",
                    }
                    result["preprocess"] = pre_report
                    result["work_order_id"] = work_order_id
                    metrics.end((time.time() - t0) * 1000, review=True)
                    result["latency_ms"] = round((time.time() - t0) * 1000)
                    return result
                slug = routed
                confidence = 0.55
                scene = {"class": slug, "confidence": confidence,
                         "top5": scene.get("top5")}

            # Oil/tyre/filter is a 3-way problem; the dedicated group head is
            # markedly better at it than the 12-class head (88% vs 75%).
            fused, group_payload, fusion = (None, None, None)
            if settings.enable_fusion:
                fused, group_payload, fusion = await asyncio.to_thread(
                    _fuse_group, slug, original_data, filename,
                    scene.get("probs"))
            # 注：试过「仅在 12 类头置信度低时才允许覆盖」，折外反而从 84% 掉到 69%
            # ——分组头被拦下后，动作头仍用旧权重翻 drain/fill，两处不一致。
            # 结论：辅助头没在新分布上重训之前，直接关掉融合（ENABLE_FUSION=0）。
            if fused and BY_SLUG.get(slug, {}).get("scene") != fused:
                if fusion and fusion.get("disagreed"):
                    slug = {"axle_oil": "bridge_drain", "tire_oil": "tire_drain",
                            "filter": "filter_none"}[fused]
                    scene["group_conflict"] = {
                        "vision": fusion["vision_scene"], "group": fused}
                    scene["confidence"] = min(confidence, 0.8)

            meta_conf = BY_SLUG[slug]
            if meta_conf["scene"] in ("axle_oil", "tire_oil", "filter"):
                if settings.enable_fusion:
                    eff_slug, eff_conf, act_payload, act_reason = await asyncio.to_thread(
                        _resolve_action, slug, original_data, filename, confidence,
                        data, angle)
                else:
                    eff_slug, eff_conf, act_payload, act_reason = slug, confidence, None, None
                result = await asyncio.to_thread(
                    _oil_or_filter, eff_slug, data, filename, axle_type,
                    expected_action, eff_conf, confidence)
                result["evidence"]["action_model"] = act_payload
                result["evidence"]["group_model"] = (
                    group_payload.get("class") if isinstance(group_payload, dict) else None)
                result["evidence"]["group_conflict"] = scene.get("group_conflict")
                if scene.get("group_conflict"):
                    result["review_reasons"].append(
                        f"12类分类({scene['group_conflict']['vision']})与分组头"
                        f"({scene['group_conflict']['group']})不一致")
                    result["need_review"] = True
                if act_reason:
                    result["review_reasons"].append(act_reason)
                    result["need_review"] = True
            else:
                # 单图预算剩余时间交给末页逻辑：旋转/VLX 兜底不能把整张图拖超时
                budget_left = settings.request_deadline_s - (time.time() - t0) - 0.8
                result = await asyncio.to_thread(
                    _character, slug, data, filename,
                    cls_data if pre_report.get("applied") else None,
                    time.monotonic() + max(0.5, budget_left))
            result.update({
                "scene_confidence": scene.get("confidence"),
                "scene_top5": scene.get("top5"),
                "model_version": scene.get("model_version"),
                "orientation": angle,
                "preprocess": pre_report,
            })
            # Uniform gate: a weak scene score always goes to a human, whatever
            # the scene type (character scenes have no other reason to speak up).
            low = float(scene.get("confidence") or 0.0)
            if low < settings.weak_scene_conf:
                reason = f"场景置信度 {low:.2f} 偏低，建议复核"
                if reason not in result.get("review_reasons", []):
                    result.setdefault("review_reasons", []).append(reason)
                result["need_review"] = True
            _apply_expectations(result, expected_vin, expected_plate,
                                expected_odometer)
            if angle:
                _map_boxes_to_original(result, angle, *original_size)
            result["work_order_id"] = work_order_id
            metrics.end((time.time() - t0) * 1000,
                        review=bool(result.get("need_review")))
            result["latency_ms"] = round((time.time() - t0) * 1000)
            return result
    except HTTPException:
        metrics.end((time.time() - t0) * 1000, error=True)
        raise
    except Exception:
        metrics.end((time.time() - t0) * 1000, error=True)
        raise
    finally:
        pass


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(image: UploadFile = File(...),
                  work_order_id: str = Form(""),
                  axle_type: str = Form(""),
                  expected_action: str = Form(""),
                  expected_vin: str = Form(""),
                  expected_plate: str = Form(""),
                  expected_odometer: str = Form("")):
    data = await read_image(image)
    return await analyze_bytes(
        data, image.filename or "image.png", work_order_id, axle_type,
        expected_action, expected_vin, expected_plate, expected_odometer)


async def analyze_with_deadline(raw: bytes, filename: str, timeout: float | None = None,
                                **kwargs) -> dict:
    """带硬超时的单图分析。超时返回可用的降级结果，而不是让请求一直挂着。

    「实时」定位下，宁可早返回一个 review，也不要让上层等 8 秒。
    """
    limit = timeout if timeout is not None else settings.request_deadline_s
    started = time.time()
    try:
        return await asyncio.wait_for(analyze_bytes(raw, filename, **kwargs), timeout=limit)
    except asyncio.TimeoutError:
        return {
            "scene": "unknown_scene",
            "scene_group": "unknown_scene",
            "scene_label": "未判定（超时）",
            "scene_confidence": 0.0,
            "conclusion": None,
            "evidence": {"timeout": True},
            "fields": {},
            "need_review": True,
            "review_reasons": [f"分析超时（>{limit:.1f}s），请重拍或转人工"],
            "latency_ms": round((time.time() - started) * 1000),
            "timed_out": True,
        }
