"""SSO 接入的单元测试：Cookie 名、Token 提取、权限映射、中间件行为。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import sso
from app.config import settings


def test_token_key_follows_host_not_env():
    """Cookie 名由 host 子串决定，不是由环境变量决定（契约里明确写过）。"""
    assert sso.token_key("tis-dev01.deepway-inc.com") == "dw-open-token-dev"
    assert sso.token_key("tis-test02.deepway-inc.com") == "dw-open-token-test"
    assert sso.token_key("spm.deepway-inc.com") == "dw-open-token"
    assert sso.token_key("localhost:8017") == "dw-open-token"


def test_menus_of_only_takes_resource_type_3():
    user = {"resources": [
        {"resourceType": 3, "resourceCodeName": "SPM_OVERVIEW"},
        {"resourceType": 4, "resourceCodeName": "SPM_OVERVIEW_EXPORT"},
        {"resourceType": "3", "resourceCodeName": "SPM_INFER"},
        {"resourceType": 3},
    ]}
    assert sso.menus_of(user) == ["SPM_OVERVIEW", "SPM_INFER"]


def test_menus_of_handles_bad_payload():
    assert sso.menus_of({}) == []
    assert sso.menus_of({"resources": [{"resourceType": "x", "resourceCodeName": "A"}]}) == []


def _client(mode: str) -> TestClient:
    app = FastAPI()
    app.middleware("http")(sso.sso_middleware)
    app.include_router(sso.router)

    @app.get("/v1/protected")
    def protected():
        return {"ok": True}

    return TestClient(app)


def test_middleware_off_mode_lets_everything_through(monkeypatch):
    monkeypatch.setattr(settings, "sso_mode", "off")
    c = _client("off")
    assert c.get("/v1/protected").status_code == 200


def test_middleware_mock_requires_token(monkeypatch):
    monkeypatch.setattr(settings, "sso_mode", "mock")
    c = _client("mock")
    r = c.get("/v1/protected")
    assert r.status_code == 401
    assert r.json()["code"] == "A0220"
    r2 = c.get("/v1/protected", headers={"authorization": "t"})
    assert r2.status_code == 200


def test_health_is_public_even_when_sso_on(monkeypatch):
    monkeypatch.setattr(settings, "sso_mode", "mock")
    app = FastAPI()
    app.middleware("http")(sso.sso_middleware)

    @app.get("/healthz")
    def h():
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/healthz").status_code == 200


def test_me_maps_views_to_resource_codes(monkeypatch):
    monkeypatch.setattr(settings, "sso_mode", "mock")
    c = _client("mock")
    d = c.get("/sso/me", headers={"authorization": "t"}).json()
    assert d["enabled"] is True
    assert d["views"]["mistakes"] == "SPM_MISTAKES"


def test_abs_url_normalises_protocol_relative():
    """`//host` 是前端写法，服务端 httpx 用不了，必须补 https。"""
    assert sso.abs_url("//iov-gw-dev01.api.deepway-inc.com") == "https://iov-gw-dev01.api.deepway-inc.com"
    assert sso.abs_url("https://x.com/") == "https://x.com"
    assert sso.abs_url("x.com") == "https://x.com"
    assert sso.abs_url("") == ""


def test_extract_resources_finds_nested_list():
    payload = {"code": "00000", "data": {"user": {"name": "a"},
                                         "resourceList": [
                                             {"resourceType": 3, "resourceCodeName": "SPM_INFER"},
                                         ]}}
    got = sso._extract_resources(payload)
    assert got and got[0]["resourceCodeName"] == "SPM_INFER"


def test_extract_resources_returns_empty_when_absent():
    assert sso._extract_resources({"data": {"x": 1}}) == []
