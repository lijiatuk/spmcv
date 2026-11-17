"""对外结论字段契约：所有业务接口只暴露一个 result（pass / partial / review）。

上层不需要理解 ok / status / decision，也不需要自己组合多个布尔值。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import categories, workorder
from app.main import app


async def _empty_collect(images, allowed, **kwargs):
    return [], []


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(categories, "_collect", _empty_collect)
    return TestClient(app)


@pytest.mark.parametrize(
    "ok,status,expected",
    [(True, "pass", "pass"), (True, "partial", "partial"), (False, "review", "review")],
)
def test_arrival_maps_to_single_result(client, monkeypatch, ok, status, expected):
    monkeypatch.setattr(categories, "summarize_arrival", lambda *a, **k: {
        "category": "arrival", "ok": ok, "status": status, "complete": status == "pass",
        "plate": None, "vin": {"license": None, "frame": None, "match": False},
        "odometer": None, "present": [], "missing_scenes": [], "missing_reasons": [],
        "reasons": [] if ok else ["VIN 不一致"], "hints": [],
    })
    body = client.post("/v1/arrival", files={"images": ("a.jpg", b"x", "image/jpeg")}).json()
    assert body["result"] == expected
    assert "ok" not in body and "status" not in body


@pytest.mark.parametrize("ok,expected", [(True, "pass"), (False, "review")])
def test_repair_and_inspection_expose_result(client, monkeypatch, ok, expected):
    monkeypatch.setattr(categories, "summarize_repair", lambda *a, **k: {
        "category": "repair", "ok": ok, "oil": {}, "filter": {},
        "present": [], "reasons": [] if ok else ["桥的加油/放油未判定"],
    })
    monkeypatch.setattr(categories, "summarize_inspection", lambda *a, **k: {
        "category": "inspection", "ok": ok, "service_front_vin": None,
        "service_front_vin_partial": None, "signature": ok, "stamp": ok,
        "present": [], "reasons": [] if ok else ["缺少服务单末页"],
    })
    for path in ("/v1/repair", "/v1/inspection"):
        body = client.post(path, files={"images": ("a.jpg", b"x", "image/jpeg")}).json()
        assert body["result"] == expected, path
        assert "ok" not in body and "status" not in body, path


def test_scan_without_category_is_review(client):
    body = client.post("/v1/scan", files={"images": ("a.jpg", b"x", "image/jpeg")}).json()
    assert body["result"] == "review"
    assert body["codes"] == ["CATEGORY_REQUIRED"]
    assert "decision" not in body


def test_workorder_exposes_result_not_auto_approved(client, monkeypatch):
    async def fake_read_images(uploads):
        return [b"x" for _ in uploads]

    async def fake_analyze(raw, filename, **kwargs):
        return {"scene": "filter_has", "scene_confidence": 1.0, "fields": {},
                "review_reasons": [], "latency_ms": 1}

    monkeypatch.setattr(workorder, "read_images", fake_read_images)
    monkeypatch.setattr(workorder, "analyze_bytes", fake_analyze)
    monkeypatch.setattr(workorder, "summarize", lambda *a, **k: {
        "auto_approved": True, "need_review": False, "review_reasons": [],
        "vin": {}, "mileage": {}, "oil": {}, "filter": {}, "service_sign": {},
        "plate": None, "scenes": [], "missing_scenes": [],
    })
    body = client.post("/v1/workorder", files={"images": ("a.jpg", b"x", "image/jpeg")}).json()
    assert body["result"] == "pass"
    assert "auto_approved" not in body
