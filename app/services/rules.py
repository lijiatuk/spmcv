"""Business rules shared by the orchestrator and tests."""
from __future__ import annotations


def decide_oil(fill: bool | None, drain: bool | None) -> tuple[str | None, str]:
    if fill is True and drain is not True:
        return "fill", "加油"
    if drain is True and fill is not True:
        return "drain", "放油"
    if fill is False and drain is False:
        return None, "待判定"
    return None, "待判定"


def decide_filter(installed: bool | None) -> tuple[str | None, str]:
    if installed is True:
        return "has", "有滤芯"
    if installed is False:
        return "none", "无滤芯"
    return None, "待判定"


def combine_action(
    scene: str,
    classifier_action: str | None,
    vlx_verdict: str | None,
    expected_action: str | None = None,
    axle_type: str | None = None,
) -> dict:
    """Combine classifier + VLX ROI; disagreement becomes review."""
    reasons: list[str] = []
    action = vlx_verdict or classifier_action

    if vlx_verdict and classifier_action and vlx_verdict != classifier_action:
        action = None
        reasons.append(f"视觉分类({classifier_action})与VLX({vlx_verdict})冲突")

    if expected_action and action and action != expected_action:
        reasons.append(f"与工单声明动作({expected_action})不一致")

    if axle_type == "hande" and action == "drain":
        reasons.append("汉德桥不应出现放油动作")

    if action not in ("drain", "fill"):
        reasons.append("动作证据不足")

    return {
        "action": action,
        "need_review": bool(reasons),
        "review_reasons": reasons,
    }


def need_review_for_fields(fields: dict) -> list[str]:
    reasons = []
    if not fields.get("vin"):
        reasons.append("未识别到VIN")
    if fields.get("vin") and len(fields["vin"]) != 17:
        reasons.append("VIN长度异常")
    return reasons
