import io

import numpy as np
from PIL import Image

from app.services.preprocess import plan_gamma, normalize


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gray(value):
    return Image.new("RGB", (64, 64), (value, value, value))


def test_well_exposed_image_is_untouched():
    data = _png(_gray(140))
    out, report = normalize(data)
    assert out is data
    assert report["applied"] is False
    assert report["reason"] == "well_exposed"


def test_dark_image_gets_gamma():
    data = _png(_gray(60))
    out, report = normalize(data)
    assert report["applied"] is True
    assert 0 < report["gamma"] < 1
    assert out is not data


def test_severe_underexposure_uses_stronger_gamma():
    assert plan_gamma({"mean": 40}) == 0.45
    assert plan_gamma({"mean": 65}) == 0.6
    assert plan_gamma({"mean": 85}) == 0.8
    assert plan_gamma({"mean": 120}) is None


def test_disabled_mode_is_passthrough():
    data = _png(_gray(30))
    out, report = normalize(data, mode="off")
    assert out is data
    assert report["reason"] == "disabled"


def test_gamma_preserves_size():
    img = Image.new("RGB", (120, 90), (50, 50, 50))
    out, report = normalize(_png(img))
    assert report["applied"] is True
    with Image.open(io.BytesIO(out)) as decoded:
        assert decoded.size == (120, 90)


def test_brightness_increases():
    data = _png(_gray(50))
    out, _ = normalize(data)
    with Image.open(io.BytesIO(out)) as decoded:
        assert np.asarray(decoded).mean() > 50


def test_group_fusion_prefers_strong_group_head():
    import asyncio

    from app.api.analyze import _fuse_group

    class FakeVision:
        def group(self, data, filename):
            return {"class": "filter", "confidence": 0.96,
                    "top5": [{"class": "filter", "confidence": 0.96},
                             {"class": "tire_oil", "confidence": 0.03},
                             {"class": "axle_oil", "confidence": 0.01}]}

    import app.api.analyze as routes_analyze
    routes_analyze.VISION = FakeVision()

    probs = {"filter_none": 0.48, "bridge_drain": 0.30, "bridge_fill": 0.22}
    winner, payload, fusion = _fuse_group("bridge_drain", b"", "a.png", probs)
    assert winner == "filter"
    assert fusion["disagreed"] is True


def test_group_fusion_ignores_non_oil_scene():
    from app.api.analyze import _fuse_group
    winner, payload, fusion = _fuse_group("arrival_plate", b"", "a.png", {})
    assert winner is None
    assert fusion is None
