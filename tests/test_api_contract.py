"""对外契约：只剩单张 /v1/recognize；多张接口必须不存在。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import health
from app.main import create_app


def test_meta_exposes_versioned_contract_and_limits():
    client = TestClient(create_app())
    response = client.get("/v1/meta", headers={"X-Request-Id": "contract-check"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "contract-check"
    payload = response.json()
    assert payload["contract"]["api_version"] == "v1"
    assert payload["contract"]["taxonomy_version"] == "v1"
    assert payload["limits"]["max_image_mb"] > 0
    assert payload["model"]["configured_version"]
    assert payload["model"]["manifest_sha256"]


def test_openapi_exposes_only_single_image_business_route():
    client = TestClient(create_app())
    spec = client.get("/openapi.json").json()
    assert "post" in spec["paths"]["/v1/recognize"]
    body = spec["paths"]["/v1/recognize"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in body

    # 多张 / 阶段接口必须已下线
    for retired in ("/v1/scan", "/v1/arrival", "/v1/repair", "/v1/inspection",
                    "/v1/workorder", "/v1/check", "/v1/check/arrival"):
        assert retired not in spec["paths"], retired


def test_scene_enum_endpoint():
    client = TestClient(create_app())
    payload = client.get("/v1/scenes").json()
    scenes = {row["scene"] for row in payload["scenes"]}
    assert len(scenes) == 12
    assert {"arrival_plate", "license_vin", "frame_vin", "odometer",
            "bridge_drain", "bridge_fill", "tire_drain", "tire_fill",
            "filter_none", "filter_has", "service_front", "service_sign"} == scenes


def test_recognize_rejects_bad_scene():
    client = TestClient(create_app())
    r = client.post("/v1/recognize", files={"image": ("a.jpg", b"x", "image/jpeg")},
                    data={"scene": "not_a_scene"})
    assert r.status_code == 400
    assert "scene" in r.json()["message"]


def test_recognize_requires_exactly_one_image_source():
    client = TestClient(create_app())
    r = client.post("/v1/recognize", data={"scene": "arrival_plate"})
    assert r.status_code == 400
    assert "image" in r.json()["message"]

    r = client.post("/v1/recognize",
                    files={"image": ("a.jpg", b"x", "image/jpeg")},
                    data={"scene": "arrival_plate", "image_url": "https://x/y.jpg"})
    assert r.status_code == 400
    assert "二选一" in r.json()["message"]


def test_labeling_guide_is_available_to_the_console():
    client = TestClient(create_app())
    response = client.get("/viz/api/labeling_guide")
    assert response.status_code == 200
    assert "bridge_drain" in response.text


def test_readiness_exposes_loaded_vision_model_version(monkeypatch):
    class UpstreamResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"model_version": "v5.1.0", "auxiliary_enabled": False}

    monkeypatch.setattr(health.httpx, "get", lambda *_args, **_kwargs: UpstreamResponse())
    result = health._probe("http://vision")
    assert result == {
        "ok": True, "status": 200,
        "model_version": "v5.1.0", "auxiliary_enabled": False,
    }