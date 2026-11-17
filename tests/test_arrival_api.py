"""接车接口的语义回归：单张照片不应被“四件套不齐”误判。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import categories
from app.main import app


def _photo(scene: str, fields: dict) -> dict:
    return {
        "scene": scene,
        "scene_group": scene,
        "fields": fields,
        "evidence": {},
        "need_review": False,
        "review_reasons": [],
        "_brief": {"index": 0, "filename": "plate.jpg", "scene": scene,
                   "scene_label": scene, "confidence": 0.99,
                   "model_version": "v5.1.0", "need_review": False,
                   "review_reasons": []},
    }


def test_arrival_endpoint_single_photo(monkeypatch):
    async def fake_collect(images, allowed, **kwargs):
        return [_photo("arrival_plate", {"plate": "晋D08906D"})], []

    monkeypatch.setattr(categories, "_collect", fake_collect)
    client = TestClient(app)
    r = client.post("/v1/arrival", files={"images": ("plate.jpg", b"x", "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "partial"
    assert body["reasons"] == []
    assert body["codes"] == []
    assert "license_vin" in body["missing_scenes"]


def test_arrival_endpoint_strict_requires_four(monkeypatch):
    async def fake_collect(images, allowed, **kwargs):
        return [_photo("arrival_plate", {"plate": "晋D08906D"})], []

    monkeypatch.setattr(categories, "_collect", fake_collect)
    client = TestClient(app)
    r = client.post("/v1/arrival", data={"strict": "true"},
                    files={"images": ("plate.jpg", b"x", "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "review"
    assert any("未提交" in reason for reason in body["reasons"])
    assert "MISSING_ODOMETER" not in body["codes"]
