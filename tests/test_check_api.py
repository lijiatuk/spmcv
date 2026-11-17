"""功能验收接口的 HTTP 契约：200 = 通过，422 = 不通过，响应没有 ok。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import check as check_api
from app.main import app


def _file():
    return {"images": ("a.jpg", b"x", "image/jpeg")}


def _arrival(good=True):
    if not good:
        return [{"scene": "arrival_plate", "fields": {"plate": "晋D08906D"}}], []
    return [
        {"scene": "arrival_plate", "fields": {"plate": "晋D08906D"}},
        {"scene": "license_vin", "fields": {"vin": "HLC3J2VEXSA603850"}},
        {"scene": "frame_vin", "fields": {"vin": "HLC3J2VEXSA603850"}},
        {"scene": "odometer", "fields": {"odometer": 182}},
    ], []


def _inspection():
    return [
        {"scene": "service_front", "fields": {"vin": "HLC3J2VEXSA603850"}},
        {"scene": "service_sign", "evidence": {"boxes": [{"label": "signature"},
                                                         {"label": "stamp"}]}},
    ], []


def test_canonical_pass_has_no_ok(monkeypatch):
    async def fake(images, allowed, **kw):
        return _arrival(True)

    monkeypatch.setattr(check_api, "_collect", fake)
    r = TestClient(app).post("/v1/check", files=_file(), data={
        "scene": "arrival", "expected_plate": "晋D08906D",
        "expected_vin": "HLC3J2VEXSA603850", "expected_odometer": "180",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["scene"] == "arrival"
    assert body["data"] == {"plate": "晋D08906D", "vin": "HLC3J2VEXSA603850", "odometer": 182}
    assert "ok" not in body
    assert body["missing"] == []


def test_canonical_fail_is_422(monkeypatch):
    async def fake(images, allowed, **kw):
        return _arrival(False)

    monkeypatch.setattr(check_api, "_collect", fake)
    r = TestClient(app).post("/v1/check", files=_file(), data={"scene": "arrival"})
    assert r.status_code == 422
    body = r.json()
    assert body["scene"] == "arrival"
    assert "缺少照片" in body["message"]
    assert "行驶证VIN" in body["missing"]
    assert "ok" not in body


def test_alias_arrival(monkeypatch):
    async def fake(images, allowed, **kw):
        return _arrival(True)

    monkeypatch.setattr(check_api, "_collect", fake)
    r = TestClient(app).post("/v1/check/arrival", files=_file(), data={})
    assert r.status_code == 200
    assert r.json()["scene"] == "arrival"


def test_alias_inspection_returns_signature_stamp(monkeypatch):
    async def fake(images, allowed, **kw):
        return _inspection()

    monkeypatch.setattr(check_api, "_collect", fake)
    r = TestClient(app).post("/v1/check/inspection", files=_file(), data={"expected_vin": "HLC3J2VEXSA603850"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == {"vin": "HLC3J2VEXSA603850", "vin_partial": None,
                            "signature": True, "stamp": True}
    assert "ok" not in body


def test_bad_scene_is_422():
    r = TestClient(app).post("/v1/check", files=_file(), data={"scene": "foo"})
    assert r.status_code == 422
    body = r.json()
    assert body["scene"] == "foo"
    assert "scene" in body["message"]
    assert "ok" not in body


def test_missing_images_is_400():
    r = TestClient(app).post("/v1/check", data={"scene": "arrival"})
    assert r.status_code == 400
    assert "images" in r.json()["message"]


def test_bad_odometer_is_422():
    r = TestClient(app).post("/v1/check", files=_file(),
                             data={"scene": "arrival", "expected_odometer": "abc"})
    assert r.status_code == 422
    assert "expected_odometer" in r.json()["message"]
