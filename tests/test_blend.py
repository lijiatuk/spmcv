from app.api.analyze import IN_GROUP_CLASSES, _blend_in_group


def test_blend_prefers_sharper_binary_head():
    head = {"class": "bridge_drain", "confidence": 0.9,
            "top5": [{"class": "bridge_drain", "confidence": 0.9},
                     {"class": "bridge_fill", "confidence": 0.1}]}
    six = {"class": "bridge_fill", "confidence": 0.7,
           "top5": [{"class": "bridge_fill", "confidence": 0.7},
                    {"class": "bridge_drain", "confidence": 0.2}]}
    winner, conf = _blend_in_group("axle_oil", head, six)
    assert winner == "bridge_drain"
    assert 0 < conf <= 1


def test_blend_respects_strong_six_way_signal():
    head = {"class": "bridge_drain", "confidence": 0.55,
            "top5": [{"class": "bridge_drain", "confidence": 0.55},
                     {"class": "bridge_fill", "confidence": 0.45}]}
    six = {"class": "bridge_fill", "confidence": 0.995,
           "top5": [{"class": "bridge_fill", "confidence": 0.995},
                    {"class": "bridge_drain", "confidence": 0.005}]}
    winner, _ = _blend_in_group("axle_oil", head, six)
    assert winner == "bridge_fill"


def test_blend_unknown_group_returns_none():
    winner, conf = _blend_in_group("odometer", {"top5": []}, {"top5": []})
    assert winner is None and conf is None


def test_groups_cover_six_action_classes():
    assert set(IN_GROUP_CLASSES) == {"axle_oil", "tire_oil", "filter"}
    flat = {c for v in IN_GROUP_CLASSES.values() for c in v}
    assert len(flat) == 6
