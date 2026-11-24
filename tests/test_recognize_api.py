"""HTTP 契约：200 = 识别成功，422 = 不成功，响应只有 scene/message/data。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import recognize as api
from app.main import create_app

# 1x1 PNG：read_image 会校验 magic bytes
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
       b"\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82")


def _client() -> TestClient:
    return TestClient(create_app())


def _fake(ok: bool, scene: str = "arrival_plate", data: dict | None = None, message: str = ""):
    async def _recognize(payload, filename, scene_arg, budget=None):
        return {
            "ok": ok,
            "scene": scene_arg,
            "message": message or ("识别成功" if ok else "未识别到"),
            "data": data if data is not None else (
                {"plate": "晋D08906D"} if ok else {"plate": None}),
        }
    return _recognize


def test_success_is_200(monkeypatch):
    monkeypatch.setattr(api, "recognize", _fake(True))
    r = _client().post("/v1/recognize",
                       files={"image": ("a.png", PNG, "image/png")},
                       data={"scene": "arrival_plate"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"scene": "arrival_plate", "message": "识别成功",
                    "data": {"plate": "晋D08906D"}}


def test_failure_is_422(monkeypatch):
    monkeypatch.setattr(api, "recognize", _fake(False, message="未识别到车牌，请重拍"))
    r = _client().post("/v1/recognize",
                       files={"image": ("a.png", PNG, "image/png")},
                       data={"scene": "arrival_plate"})
    assert r.status_code == 422
    body = r.json()
    assert body["scene"] == "arrival_plate"
    assert body["message"] == "未识别到车牌，请重拍"
    assert body["data"] == {"plate": None}


def test_bad_image_bytes_are_400(monkeypatch):
    monkeypatch.setattr(api, "recognize", _fake(True))
    r = _client().post("/v1/recognize",
                       files={"image": ("a.txt", b"not an image", "text/plain")},
                       data={"scene": "odometer"})
    assert r.status_code == 400
    assert "scene" in r.json()


def test_image_url_failure_is_400(monkeypatch):
    async def boom(url):
        from fastapi import HTTPException
        raise HTTPException(400, "图片下载失败：HTTP 404")

    monkeypatch.setattr(api, "fetch_image", boom)
    r = _client().post("/v1/recognize", data={"scene": "odometer",
                                              "image_url": "https://x/404.jpg"})
    assert r.status_code == 400
    assert "下载失败" in r.json()["message"]


def test_empty_data_on_timeout(monkeypatch):
    import asyncio

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
        return {"ok": True, "scene": "odometer", "message": "ok", "data": {}}

    monkeypatch.setattr(api, "recognize", slow)
    from app.config import settings
    monkeypatch.setattr(settings, "request_deadline_s", 0.05)
    r = _client().post("/v1/recognize",
                       files={"image": ("a.png", PNG, "image/png")},
                       data={"scene": "odometer"})
    assert r.status_code == 422
    assert "超时" in r.json()["message"]
    assert r.json()["data"] == {"odometer": None}