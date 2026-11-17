from app.services.workorder import (
    check_mileage,
    check_oil,
    check_vin_consistency,
    summarize,
)


def _photo(scene, **fields):
    return {"scene": scene, "fields": fields, "conclusion": None,
            "review_reasons": []}


def test_vin_consistency_full_match():
    items = [_photo("license_vin", vin="HLC3J2VF9TA604594"),
             _photo("frame_vin", vin="HLC3J2VF9TA604594")]
    out = check_vin_consistency(items)
    assert out["vin"] == "HLC3J2VF9TA604594"
    assert out["confidence"] == "full"
    assert out["reasons"] == []


def test_vin_consistency_mismatch():
    items = [_photo("license_vin", vin="HLC3J2VF9TA604594"),
             _photo("frame_vin", vin="LJ1ER4DU5T3305296")]
    out = check_vin_consistency(items)
    assert out["vin"] is None
    assert out["reasons"]


def test_vin_consistency_partial_only():
    items = [_photo("service_front", vin_partial="T604594")]
    out = check_vin_consistency(items)
    assert out["confidence"] == "partial"
    assert out["partials"] == {"service_front": "T604594"}


def test_mileage_within_tolerance():
    out = check_mileage(_photo("odometer", odometer=195), 200)
    assert out["ok"] is True
    assert out["reasons"] == []


def test_mileage_outside_tolerance():
    out = check_mileage(_photo("odometer", odometer=182), 200)
    assert out["ok"] is False
    assert "5%" in out["reasons"][0]


def test_mileage_missing_photo():
    out = check_mileage(None, 200)
    assert out["ok"] is False


def test_hande_axle_rejects_bridge_drain():
    items = [{"scene": "axle_oil", "conclusion": {"action": "drain"}}]
    out = check_oil(items, axle_type="hande")
    assert out["reasons"]


def test_hande_axle_skips_tyre():
    items = [{"scene": "tire_oil", "conclusion": {"action": "fill"}}]
    out = check_oil(items, axle_type="hande")
    assert out["reasons"]


def test_summarize_clean_work_order_auto_approves():
    items = [
        {**_photo("arrival_plate", plate="晋D08906D"), "conclusion": None},
        _photo("license_vin", vin="HLC3J2VF9TA604594"),
        _photo("frame_vin", vin="HLC3J2VF9TA604594"),
        _photo("odometer", odometer=182),
        {"scene": "axle_oil", "conclusion": {"action": "fill"}, "fields": {},
         "review_reasons": []},
        {"scene": "service_sign", "conclusion": None, "fields": {},
         "review_reasons": []},
    ]
    out = summarize(items, expected_vin="HLC3J2VF9TA604594",
                    expected_odometer=182, expected_plate="晋D08906D")
    assert out["auto_approved"] is True
    assert out["review_reasons"] == []


def test_summarize_flags_missing_scenes():
    out = summarize([_photo("frame_vin", vin="HLC3J2VF9TA604594")])
    assert out["auto_approved"] is False
    assert any("缺少" in r for r in out["review_reasons"])


def test_summarize_flags_vin_work_order_mismatch():
    items = [
        {**_photo("arrival_plate", plate="晋D08906D")},
        _photo("license_vin", vin="HLC3J2VF9TA604594"),
        _photo("frame_vin", vin="HLC3J2VF9TA604594"),
        _photo("odometer", odometer=182),
        {"scene": "service_sign", "conclusion": None, "fields": {},
         "review_reasons": []},
    ]
    out = summarize(items, expected_vin="LJ1ER4DU5T3305296")
    assert out["auto_approved"] is False
    assert any("VIN 与工单不一致" in r for r in out["review_reasons"])

def test_missing_scene_reason_not_duplicated():
    items = [_photo("frame_vin", vin="HLC3J2VF9TA604594")]
    out = summarize(items)
    assert len(out["review_reasons"]) == len(set(out["review_reasons"]))
    assert "缺少接车照片" in out["review_reasons"]
    assert "缺少里程表照片" in out["review_reasons"]


def test_mileage_missing_does_not_double_report():
    items = [
        {**_photo("arrival_plate", plate="晋D08906D")},
        _photo("frame_vin", vin="HLC3J2VF9TA604594"),
        {"scene": "service_sign", "conclusion": None, "fields": {},
         "review_reasons": []},
    ]
    out = summarize(items)
    assert sum("里程" in r for r in out["review_reasons"]) == 1
