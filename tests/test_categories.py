"""三类场景聚合规则的单元测试（纯函数，不需要模型）。"""
from __future__ import annotations

from app.services.categories import (
    summarize_arrival,
    summarize_inspection,
    summarize_repair,
)


GROUP = {"bridge_drain": "axle_oil", "bridge_fill": "axle_oil",
         "tire_drain": "tire_oil", "tire_fill": "tire_oil",
         "filter_none": "filter", "filter_has": "filter"}


def photo(scene, fields=None, action=None, boxes=None):
    """按真实响应构造：scene 是 12 类，scene_group 是粗分组。"""
    return {
        "scene": scene,
        "scene_group": GROUP.get(scene, scene),
        "fields": fields or {},
        "conclusion": {"action": action} if action else None,
        "evidence": {"boxes": boxes or []},
    }


# ---------- arrival ----------

def test_arrival_all_good():
    items = [
        photo("arrival_plate", {"plate": "晋D08906D"}),
        photo("license_vin", {"vin": "HLC3J2VEXSA603850"}),
        photo("frame_vin", {"vin": "HLC3J2VEXSA603850"}),
        photo("odometer", {"odometer": 4534}),
    ]
    out = summarize_arrival(items)
    assert out["ok"] is True
    assert out["vin"]["match"] is True
    assert out["odometer"] == 4534
    assert out["reasons"] == []


def test_arrival_vin_mismatch_blocks():
    items = [
        photo("arrival_plate", {"plate": "晋D08906D"}),
        photo("license_vin", {"vin": "HLC3J2VEXSA603850"}),
        photo("frame_vin", {"vin": "HLC3J2VFXSA603050"}),
        photo("odometer", {"odometer": 182}),
    ]
    out = summarize_arrival(items)
    assert out["ok"] is False
    assert out["vin"]["match"] is False
    assert any("VIN 不一致" in r for r in out["reasons"])


def test_arrival_missing_plate_is_skipped_not_blocking():
    """规格：「匹配到车头，未识别车牌号 → 跳过车牌号校验」，不阻断自动审核。"""
    out = summarize_arrival([
        photo("arrival_plate", {}),                 # 有图但没读出车牌
        photo("license_vin", {"vin": "LJ1ER4DU3T3304941"}),
        photo("frame_vin", {"vin": "LJ1ER4DU3T3304941"}),
    ])
    assert not any("车牌" in r for r in out["reasons"]), "车牌缺失不应阻断"
    assert any("车牌" in h for h in out.get("hints", [])), "应给出提示而不是失败"


def test_arrival_single_plate_photo_does_not_report_missing_vin():
    """只传一张车头照时，不能因为没传行驶证/里程表就判复核。

    现场反馈：大类识别成功（车牌 0.99），页面却提示「未识别到行驶证 VIN /
    车架 VIN / 里程」，用户以为模型坏了。实际是聚合层把“没提交”当成了“没识别到”。
    """
    out = summarize_arrival([photo("arrival_plate", {"plate": "晋D08906D"})])
    assert out["ok"] is True
    assert out["status"] == "partial"
    assert out["complete"] is False
    assert out["reasons"] == []
    assert out["missing_scenes"] == ["license_vin", "frame_vin", "odometer"]
    assert any("行驶证" in r for r in out["missing_reasons"])


def test_arrival_strict_requires_all_four_photos():
    """整单校验（strict）才把缺图算作不通过，且原因说“未提交”而不是“未识别到”。"""
    out = summarize_arrival([photo("arrival_plate", {"plate": "晋D08906D"})],
                            require_complete=True)
    assert out["ok"] is False
    assert out["status"] == "review"
    assert any("未提交里程表照片" in r for r in out["reasons"])
    assert not any("未识别到" in r for r in out["reasons"])


def test_arrival_submitted_but_unreadable_is_still_a_reason():
    out = summarize_arrival([
        photo("arrival_plate", {"plate": "晋D08906D"}),
        photo("license_vin", {}),
    ])
    assert out["ok"] is False
    assert any("未识别到行驶证 VIN" in r for r in out["reasons"])
    assert "license_vin" not in out["missing_scenes"]


def test_arrival_plate_mismatch_with_work_order():
    out = summarize_arrival(
        [photo("arrival_plate", {"plate": "晋D08906D"}),
         photo("license_vin", {"vin": "LJ1ER4DU3T3304941"}),
         photo("frame_vin", {"vin": "LJ1ER4DU3T3304941"})],
        expected_plate="晋D08907D")
    assert out["ok"] is False
    assert any("车牌与工单不一致" in r for r in out["reasons"])


def test_arrival_odometer_tolerance_5_percent():
    """规格：里程与工单误差不超过 5%。"""
    base = [photo("arrival_plate", {"plate": "晋D08906D"}),
            photo("license_vin", {"vin": "LJ1ER4DU3T3304941"}),
            photo("frame_vin", {"vin": "LJ1ER4DU3T3304941"})]
    ok = summarize_arrival(base + [photo("odometer", {"odometer": 1020})],
                           expected_odometer=1000)
    assert not any("里程与工单" in r for r in ok["reasons"]), "3% 误差应通过"
    bad = summarize_arrival(base + [photo("odometer", {"odometer": 1100})],
                            expected_odometer=1000)
    assert any("里程与工单" in r for r in bad["reasons"]), "10% 误差应拦下"


def test_repair_non_hande_requires_both_drain_and_fill():
    """规格：非汉德桥需同时存在桥放油和桥加油。"""
    only_drain = summarize_repair([photo("axle_oil", action="drain")], axle_type="other")
    assert only_drain["ok"] is False
    assert any("缺少桥加油" in r for r in only_drain["reasons"])
    both = summarize_repair([photo("axle_oil", action="drain"),
                             photo("axle_oil", action="fill")], axle_type="other")
    assert both["ok"] is True


# ---------- repair ----------

def test_repair_normal():
    items = [photo("axle_oil", action="drain"), photo("tire_oil", action="fill"),
             photo("filter", action="has")]
    out = summarize_repair(items)
    assert out["ok"] is True
    assert out["oil"]["axle"] == "drain"
    assert out["filter"]["state"] == "has"


def test_repair_hande_drain_is_error():
    out = summarize_repair([photo("axle_oil", action="drain")], axle_type="hande")
    assert out["ok"] is False
    assert any("汉德桥" in r for r in out["reasons"])


def test_repair_drain_then_fill_is_normal():
    """一次换油必然先放后加，两个动作同时出现是正常流程，不是矛盾。

    生产模拟里 5/5 个完整工单曾被判成"矛盾结论"，全部误杀。
    """
    items = [photo("axle_oil", action="drain"), photo("axle_oil", action="fill")]
    out = summarize_repair(items)
    assert out["ok"] is True
    assert out["oil"]["axle_actions"] == ["drain", "fill"]
    assert out["oil"]["axle_label"] == "放油+加油"
    assert out["oil"]["axle"] is None, "两个动作时不给单一值，避免下游误读"


def test_repair_without_axle_photo_is_not_an_error():
    """只拍了轮胎照片，不该被判"未判定桥"——那是没拍，不是没判出来。"""
    out = summarize_repair([photo("tire_oil", action="fill")])
    assert out["ok"] is True
    assert not any("桥" in r for r in out["reasons"])


def test_repair_axle_photo_without_verdict_fails():
    out = summarize_repair([photo("axle_oil", action=None)])
    assert out["ok"] is False
    assert any("桥" in r for r in out["reasons"])


# ---------- inspection ----------

def test_inspection_all_good():
    items = [
        photo("service_front", {"vin": "HLC3J2VEXSA603850"}),
        photo("service_sign", boxes=[{"label": "signature"}, {"label": "stamp"}]),
    ]
    out = summarize_inspection(items)
    assert out["ok"] is True
    assert out["signature"] and out["stamp"]


def test_inspection_missing_stamp():
    items = [
        photo("service_front", {"vin_partial": "83182"}),
        photo("service_sign", boxes=[{"label": "signature"}]),
    ]
    out = summarize_inspection(items)
    assert out["ok"] is False
    assert out["stamp"] is False
    assert any("盖章" in r for r in out["reasons"])
    assert out["service_front_vin_partial"] == "83182"


def test_inspection_missing_sign_page():
    out = summarize_inspection([photo("service_front", {"vin": "LJ1ER4DU3T3304941"})])
    assert out["ok"] is False
    assert any("缺少服务单末页" in r for r in out["reasons"])


def test_arrival_ignores_vin_failing_checksum():
    """仪表盘上拼出来的假 VIN 不能制造"不一致"结论。

    真实案例（2026-11-13）：一张新能源车仪表盘被兜底成 license_vin，
    OCR 从屏幕数字里拼出 1047117BARKMH2311，导致车架 VIN 被判冲突。
    """
    items = [
        photo("arrival_plate", {"plate": "晋D08906D"}),
        photo("license_vin", {"vin": "1047117BARKMH2311", "vin_checksum_ok": False}),
        photo("frame_vin", {"vin": "HLC3J2VF9TA604594"}),
        photo("odometer", {"odometer": 182}),
    ]
    out = summarize_arrival(items)
    assert out["vin"]["license"] is None, "校验位不过的值必须被丢弃"
    assert out["vin"]["match"] is False
    assert not any("不一致" in r for r in out["reasons"]), out["reasons"]
    assert any("校验位" in r for r in out["reasons"])
