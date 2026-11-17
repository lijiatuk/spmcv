from app.api.analyze import _apply_expectations


def _result(**fields):
    return {"fields": fields, "review_reasons": [], "need_review": False}


def test_vin_match_has_no_review():
    r = _result(vin="HLC3J2VF9TA604594")
    _apply_expectations(r, "HLC3J2VF9TA604594", "", "")
    assert r["review_reasons"] == []
    assert r["need_review"] is False


def test_vin_mismatch_flags_review():
    r = _result(vin="HLC3J2VF9TA604594")
    _apply_expectations(r, "LJ1ER4DU5T3305296", "", "")
    assert r["need_review"] is True
    assert "不一致" in r["review_reasons"][0]


def test_partial_vin_suffix_matches_work_order():
    r = _result(vin_partial="T3307010")
    _apply_expectations(r, "LJ1ER4DU5T3307010", "", "")
    assert r["review_reasons"] == []


def test_odometer_five_percent_rule():
    r = _result(odometer=182)
    _apply_expectations(r, "", "", "200")
    assert r["need_review"] is True
    assert "5%" in r["review_reasons"][0]

    r = _result(odometer=195)
    _apply_expectations(r, "", "", "200")
    assert r["review_reasons"] == []


def test_plate_mismatch():
    r = _result(plate="晋D08906D")
    _apply_expectations(r, "", "晋D00000D", "")
    assert r["need_review"] is True
