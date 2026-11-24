"""12 场景识别判定（纯逻辑，模型全部 mock）。"""
from __future__ import annotations

import asyncio

import pytest

from app.services import recognizer as R


def run(coro):
    return asyncio.run(coro)


class FakeVision:
    def __init__(self, klass):
        self.klass = klass

    def scene(self, data, filename):
        return {"class": self.klass, "confidence": 0.9}


def test_oil_scene_matching(monkeypatch):
    monkeypatch.setattr(R, "VISION", FakeVision("bridge_fill"))
    out = run(R.recognize(b"x", "a.png", "bridge_fill"))
    assert out["ok"] is True
    assert out["data"] == {"action": "fill"}


def test_oil_scene_opposite_direction_is_422(monkeypatch):
    monkeypatch.setattr(R, "VISION", FakeVision("bridge_drain"))
    out = run(R.recognize(b"x", "a.png", "bridge_fill"))
    assert out["ok"] is False
    assert out["data"] == {"action": "drain"}
    assert "桥放油" in out["message"]


def test_oil_scene_unrelated_class(monkeypatch):
    monkeypatch.setattr(R, "VISION", FakeVision("filter_none"))
    out = run(R.recognize(b"x", "a.png", "bridge_drain"))
    assert out["ok"] is False
    assert out["data"] == {"action": None}
    assert "未识别到" in out["message"]


def test_filter_scene(monkeypatch):
    monkeypatch.setattr(R, "VISION", FakeVision("filter_has"))
    out = run(R.recognize(b"x", "a.png", "filter_has"))
    assert out["ok"] is True
    assert out["data"] == {"filter_state": "has"}

    out = run(R.recognize(b"x", "a.png", "filter_none"))
    assert out["ok"] is False
    assert out["data"] == {"filter_state": "has"}


def _patch_character(monkeypatch, fields):
    def fake_character(scene, data, filename, alt_data=None, deadline=None):
        return {"scene": scene, "fields": fields}
    monkeypatch.setattr(R, "_character", fake_character)


def test_plate_success_and_failure(monkeypatch):
    _patch_character(monkeypatch, {"plate": "晋D08906D"})
    out = run(R.recognize(b"x", "a.png", "arrival_plate"))
    assert out["ok"] is True and out["data"] == {"plate": "晋D08906D"}

    _patch_character(monkeypatch, {"plate": None})
    out = run(R.recognize(b"x", "a.png", "arrival_plate"))
    assert out["ok"] is False
    assert out["data"] == {"plate": None}
    assert "未识别到车牌" in out["message"]


def test_vin_success_checksum_not_required(monkeypatch):
    # 识别成功只看是否读到 VIN；校验位属于可信度问题，不在本接口做
    _patch_character(monkeypatch, {"vin": "HLC3J2VE3TA603182", "vin_checksum_ok": False})
    out = run(R.recognize(b"x", "a.png", "license_vin"))
    assert out["ok"] is True
    assert out["data"] == {"vin": "HLC3J2VE3TA603182"}


def test_vin_partial_only_is_failure(monkeypatch):
    _patch_character(monkeypatch, {"vin": None, "vin_partial": "603182"})
    out = run(R.recognize(b"x", "a.png", "frame_vin"))
    assert out["ok"] is False
    assert "尾段" in out["message"]


def test_odometer(monkeypatch):
    _patch_character(monkeypatch, {"odometer": 182})
    out = run(R.recognize(b"x", "a.png", "odometer"))
    assert out["ok"] is True and out["data"] == {"odometer": 182}

    _patch_character(monkeypatch, {"odometer": None})
    out = run(R.recognize(b"x", "a.png", "odometer"))
    assert out["ok"] is False


def test_service_front_partial_is_ok(monkeypatch):
    _patch_character(monkeypatch, {"vin": None, "vin_partial": "603182"})
    out = run(R.recognize(b"x", "a.png", "service_front"))
    assert out["ok"] is True
    assert out["data"] == {"vin": None, "vin_partial": "603182"}


def _patch_sign(monkeypatch, labels):
    boxes = [{"label": label, "score": 0.9, "box": [0, 0, 10, 10]} for label in labels]
    monkeypatch.setattr(R, "_service_sign_boxes", lambda *a, **k: (boxes, None))


def test_sign_requires_both(monkeypatch):
    _patch_sign(monkeypatch, ["signature", "stamp"])
    out = run(R.recognize(b"x", "a.png", "service_sign"))
    assert out["ok"] is True
    assert out["data"] == {"signature": True, "stamp": True}

    _patch_sign(monkeypatch, ["signature"])
    out = run(R.recognize(b"x", "a.png", "service_sign"))
    assert out["ok"] is False
    assert out["data"] == {"signature": True, "stamp": False}
    assert "服务站盖章" in out["message"]


def test_unknown_scene_is_not_in_enum():
    assert "unknown" not in R.SCENES
    assert len(R.SCENES) == 12


@pytest.mark.parametrize("scene", list(R.SCENES))
def test_every_scene_returns_four_keys(scene, monkeypatch):
    monkeypatch.setattr(R, "VISION", FakeVision(scene if scene in R.OIL_FILTER else "arrival_plate"))
    _patch_character(monkeypatch, {"plate": "x", "vin": "V", "odometer": 1,
                                   "vin_partial": None})
    _patch_sign(monkeypatch, ["signature", "stamp"])
    out = run(R.recognize(b"x", "a.png", scene))
    assert set(out) == {"ok", "scene", "message", "data"}
    assert out["scene"] == scene