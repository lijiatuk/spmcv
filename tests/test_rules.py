from app.services.rules import combine_action, decide_filter, decide_oil


def test_oil_fill():
    assert decide_oil(True, False)[0] == "fill"
    assert decide_oil(True, None)[0] == "fill"


def test_oil_drain():
    assert decide_oil(False, True)[0] == "drain"
    assert decide_oil(None, True)[0] == "drain"


def test_oil_conflict():
    assert decide_oil(True, True)[0] is None


def test_filter():
    assert decide_filter(True)[0] == "has"
    assert decide_filter(False)[0] == "none"
    assert decide_filter(None)[0] is None


def test_combine_conflict():
    out = combine_action("axle_oil", "drain", "fill")
    assert out["action"] is None
    assert out["need_review"] is True


def test_hande_rule():
    out = combine_action("axle_oil", "drain", "drain", axle_type="hande")
    assert out["need_review"] is True
