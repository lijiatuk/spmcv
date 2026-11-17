"""上传校验 / 健康检查 / 指标语义的回归测试。

这三个是 code review 里 H2/H3/H5 的修复点，必须锁住行为。
"""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.limits import read_image, sniff_image
from app.metrics import Metrics

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_upload(data: bytes, name: str = "a.jpg") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_sniff_rejects_non_image():
    with pytest.raises(HTTPException) as exc:
        sniff_image(b"not an image at all")
    assert exc.value.status_code == 400


def test_sniff_uses_magic_bytes_not_extension():
    """产品数据里 643 个 .png 实际是 JPEG，判断必须看内容。"""
    assert sniff_image(JPEG) == "jpeg"
    assert sniff_image(PNG) == "png"


@pytest.mark.asyncio
async def test_read_image_accepts_valid():
    data = await read_image(make_upload(JPEG))
    assert data == JPEG


@pytest.mark.asyncio
async def test_read_image_rejects_oversize():
    from app.config import settings

    big = JPEG + b"\x00" * (settings.max_image_bytes + 1)
    with pytest.raises(HTTPException) as exc:
        await read_image(make_upload(big))
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_read_image_rejects_empty():
    with pytest.raises(HTTPException) as exc:
        await read_image(make_upload(b""))
    assert exc.value.status_code == 400


def test_metrics_kinds_are_separate():
    m = Metrics()
    m.start("image")
    m.end(100, kind="image")
    m.start("workorder")
    m.end(500, kind="workorder")
    assert m._stats["image"]["requests"] == 1
    assert m._stats["workorder"]["requests"] == 1
    assert m._stats["image"]["in_flight"] == 0
    assert m._stats["workorder"]["in_flight"] == 0


def test_metrics_in_flight_never_negative():
    m = Metrics()
    m.end(10, kind="workorder")          # 没有对应的 start
    assert m._stats["workorder"]["in_flight"] == 0


def test_healthz_does_not_touch_downstream():
    """liveness 必须快：不查 vision/ocr/kev。"""
    from app.main import create_app

    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "vision" not in body          # 不暴露/不探测下游
    assert "kev" not in body


def test_readyz_reports_503_when_downstream_down(monkeypatch):
    """必须把下游指到不可达端口，否则本机起了服务时这个测试会假绿/假红。"""
    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "vision_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "ocr_service_url", "http://127.0.0.1:9")
    client = TestClient(create_app())
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_metrics_endpoint_is_prometheus_text():
    """Prometheus 抓取格式：不能返回 JSON 字符串（带引号和 \\n 转义）。"""
    from app.main import create_app

    client = TestClient(create_app())
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert body.startswith("# TYPE spm_image_requests_total counter\n")
    assert '"' not in body.split("\n")[0]
    assert "# TYPE spm_workorder_latency_ms_p95 gauge" in body
