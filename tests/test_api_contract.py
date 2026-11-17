from fastapi.testclient import TestClient

from app.api import categories, health
from app.main import create_app


def test_meta_exposes_versioned_contract_and_limits():
    client = TestClient(create_app())
    response = client.get("/v1/meta", headers={"X-Request-Id": "contract-check"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "contract-check"
    payload = response.json()
    assert payload["contract"]["api_version"] == "v1"
    assert payload["contract"]["taxonomy_version"] == "v1"
    assert payload["contract"]["decision_values"] == ["pass", "review"]
    assert payload["limits"]["max_photos"] > 0
    assert payload["model"]["configured_version"]
    assert payload["model"]["manifest_sha256"]
    assert "scene" in payload["model"]["configured_roles"]


def test_openapi_exposes_upstream_scan_and_workorder_routes():
    client = TestClient(create_app())
    spec = client.get("/openapi.json").json()
    for path in ("/v1/scan", "/v1/arrival", "/v1/repair", "/v1/inspection", "/v1/workorder"):
        assert "post" in spec["paths"][path]
        assert "multipart/form-data" in spec["paths"][path]["post"]["requestBody"]["content"]


def test_labeling_guide_is_available_to_the_console():
    client = TestClient(create_app())
    response = client.get("/viz/api/labeling_guide")
    assert response.status_code == 200
    assert "bridge_drain" in response.text
    assert "无法判断" in response.text


def test_scan_invalid_category_has_stable_machine_error():
    client = TestClient(create_app())
    response = client.post(
        "/v1/scan",
        headers={"X-Request-Id": "bad-category-check"},
        files={"images": ("bad.jpg", b"not-an-image", "image/jpeg")},
        data={"category": "unknown"},
    )
    assert response.status_code == 422
    assert response.headers["X-Request-Id"] == "bad-category-check"
    assert response.json()["code"] == "INVALID_PARAMS"


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


def test_ocr_timeout_is_exposed_as_stable_photo_and_workflow_code():
    assert categories.ocr_error_code("ReadTimeout: upstream timed out") == "OCR_TIMEOUT"
    assert categories.ocr_error_code("ConnectError: refused") == "OCR_UNAVAILABLE"
    payload = {"ok": True}
    categories._include_ocr_errors(payload, [{"_brief": {"ocr_error_code": "OCR_TIMEOUT"}}])
    assert payload["ok"] is False
    assert payload["need_review"] is True
    assert "OCR_TIMEOUT" in payload["codes"]
