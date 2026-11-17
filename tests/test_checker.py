"""功能验收规则的单测（纯函数，不需要模型）。"""
from __future__ import annotations

from app.services.checker import (
    check_arrival,
    check_inspection,
    check_repair,
    evaluate,
)

GROUP = {"bridge_drain": "axle_oil", "bridge_fill": "axle_oil",
         "tire_drain": "tire_oil", "tire_fill": "tire_oil",
         "filter_none": "filter", "filter_has": "filter"}


def item(scene, fields=None, action=None, boxes=None):
    return {
        "scene": scene,
        "scene_group": GROUP.get(scene, scene),
        "fields": fields or {},
        "conclusion": {"action": action} if action else None,
        "evidence": {"boxes": boxes or []},
    }


def arrival_good():
    return [
        item("arrival_plate", {"plate": "晋D08906D"}),
        item("license_vin", {"vin": "HLC3J2VEXSA603850"}),
        item("frame_vin", {"vin": "HLC3J2VEXSA603850"}),
        item("odometer", {"odometer": 182}),
    ]


def test_arrival_pass():
    out = check_arrival(arrival_good(), expected_plate="晋D08906D",
                        expected_vin="HLC3J2VEXSA603850", expected_odometer=180)
    assert out["ok"] is True
    assert out["missing"] == []
    assert out["data"] == {"plate": "晋D08906D", "vin": "HLC3J2VEXSA603850", "odometer": 182.0}
    assert all(c["ok"] for c in out["checks"])


def test_arrival_missing_head_fails():
    out = check_arrival([x for x in arrival_good() if x["scene"] != "arrival_plate"])
    assert out["ok"] is False
    assert "车头及车牌" in out["missing"]


def test_arrival_no_vin_fails():
    out = check_arrival([item("arrival_plate", {"plate": "晋D08906D"}),
                         item("odometer", {"odometer": 182})])
    assert out["ok"] is False
    assert any("未识别到任一 VIN" in c["message"] for c in out["checks"])


def test_arrival_one_vin_is_enough():
    """规格：接收到一个 VIN 就进入下一步。"""
    out = check_arrival([
        item("arrival_plate", {"plate": "晋D08906D"}),
        item("license_vin", {"vin": "HLC3J2VEXSA603850"}),
        item("frame_vin", {}),
        item("odometer", {"odometer": 182}),
    ])
    assert out["ok"] is True


def test_arrival_two_vins_mismatch_fails():
    out = check_arrival([
        item("arrival_plate", {"plate": "晋D08906D"}),
        item("license_vin", {"vin": "HLC3J2VEXSA603850"}),
        item("frame_vin", {"vin": "HLC3J2VEXSA603851"}),
        item("odometer", {"odometer": 182}),
    ])
    assert out["ok"] is False
    assert any("不一致" in c["message"] for c in out["checks"])


def test_arrival_expected_vin_mismatch_fails():
    out = check_arrival(arrival_good(), expected_vin="HLC3J2VEXSA603851")
    assert out["ok"] is False
    assert any("VIN 与工单不一致" in c["message"] for c in out["checks"])


def test_arrival_plate_unread_is_skipped():
    out = check_arrival([
        item("arrival_plate", {}),
        item("license_vin", {"vin": "HLC3J2VEXSA603850"}),
        item("frame_vin", {"vin": "HLC3J2VEXSA603850"}),
        item("odometer", {"odometer": 182}),
    ], expected_plate="晋D08906D")
    assert out["ok"] is True
    assert any("跳过车牌校验" in c["message"] for c in out["checks"])


def test_arrival_plate_mismatch_fails():
    out = check_arrival(arrival_good(), expected_plate="晋A00000")
    assert out["ok"] is False


def test_arrival_odometer_over_5pct_fails():
    out = check_arrival(arrival_good(), expected_odometer=170)
    assert out["ok"] is False
    assert any(">5%" in c["message"] for c in out["checks"])


def test_arrival_odometer_unread_passes():
    items = [item("arrival_plate", {"plate": "晋D08906D"}),
             item("license_vin", {"vin": "HLC3J2VEXSA603850"}),
             item("frame_vin", {"vin": "HLC3J2VEXSA603850"}),
             item("odometer", {})]
    out = check_arrival(items, expected_odometer=182)
    assert out["ok"] is True


# ---------------- 维修 ----------------

def repair_non_hande():
    return [
        item("bridge_drain", action="drain"),
        item("bridge_fill", action="fill"),
        item("tire_drain", action="drain"),
        item("tire_fill", action="fill"),
    ]


def test_repair_non_hande_pass():
    out = check_repair(repair_non_hande())
    assert out["ok"] is True
    assert out["data"] == {"axle": "drain+fill", "tire": "drain+fill", "filter": None}


def test_repair_non_hande_missing_tire_fill_fails():
    out = check_repair([x for x in repair_non_hande() if x["scene"] != "tire_fill"])
    assert out["ok"] is False
    assert "轮胎加油" in out["missing"]


def test_repair_non_hande_ignores_filter():
    items = repair_non_hande() + [item("filter_has", action="has")]
    out = check_repair(items)
    assert out["ok"] is True
    assert any(c["item"] == "滤芯" and c["ok"] for c in out["checks"])


def test_repair_hande_pass_and_ignores_tire():
    items = [
        item("bridge_fill", action="fill"),
        item("filter_none", action="none"),
        item("filter_has", action="has"),
        item("tire_drain", action="drain"),
        item("bridge_drain", action="drain"),
    ]
    out = check_repair(items, axle_type="hande")
    assert out["ok"] is True
    assert out["data"] == {"axle": "fill", "tire": None, "filter": "none+has"}
    assert any(c["item"] == "轮胎" and c["ok"] for c in out["checks"])


def test_repair_hande_missing_filter_none_fails():
    out = check_repair([item("bridge_fill", action="fill"),
                        item("filter_has", action="has")], axle_type="hande")
    assert out["ok"] is False
    assert "无滤芯" in out["missing"]


def test_repair_action_undetected_fails():
    out = check_repair([item("bridge_drain", action=None),
                        item("bridge_fill", action="fill"),
                        item("tire_drain", action="drain"),
                        item("tire_fill", action="fill")])
    assert out["ok"] is False
    assert any("未判定" in c["message"] for c in out["checks"])


# ---------------- 检查表 ----------------

def inspection_good():
    return [
        item("service_front", {"vin": "HLC3J2VEXSA603850"}),
        item("service_sign", boxes=[{"label": "signature"}, {"label": "stamp"}]),
    ]


def test_inspection_pass():
    out = check_inspection(inspection_good(), expected_vin="HLC3J2VEXSA603850")
    assert out["ok"] is True
    assert out["data"] == {"vin": "HLC3J2VEXSA603850", "vin_partial": None,
                           "signature": True, "stamp": True}


def test_inspection_partial_with_expected_tail_passes():
    out = check_inspection([
        item("service_front", {"vin_partial": "603850"}),
        item("service_sign", boxes=[{"label": "signature"}, {"label": "stamp"}]),
    ], expected_vin="HLC3J2VEXSA603850")
    assert out["ok"] is True


def test_inspection_partial_without_expected_fails():
    out = check_inspection([
        item("service_front", {"vin_partial": "603850"}),
        item("service_sign", boxes=[{"label": "signature"}, {"label": "stamp"}]),
    ])
    assert out["ok"] is False


def test_inspection_expected_vin_mismatch_fails():
    out = check_inspection(inspection_good(), expected_vin="HLC3J2VEXSA603851")
    assert out["ok"] is False


def test_inspection_missing_stamp_fails():
    out = check_inspection([
        item("service_front", {"vin": "HLC3J2VEXSA603850"}),
        item("service_sign", boxes=[{"label": "signature"}]),
    ])
    assert out["ok"] is False


def test_inspection_missing_front_fails():
    out = check_inspection([item("service_sign", boxes=[{"label": "signature"}, {"label": "stamp"}])])
    assert out["ok"] is False
    assert "服务单首页" in out["missing"]


def test_unknown_requirement():
    out = evaluate("foo", [])
    assert out["ok"] is False
