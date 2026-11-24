"""运维端点。

`/healthz` 与 `/readyz` 必须分开：

* `/healthz`（liveness）——只证明进程活着，**不碰任何下游**。
  容器编排用它判断"要不要重启"，不应受下游抖动影响。
  之前这里同步调 vision + kev（各 5s 超时），最坏 10s，
  超过 compose 的 5s healthcheck timeout，容器会被反复重启。
* `/readyz`（readiness）——能否接流量。探测下游但用短超时（默认 2s），
  失败返回 503，让负载均衡把流量摘走，而不是重启容器。
"""
from __future__ import annotations

import hashlib
import json

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import PlainTextResponse

from app.config import BASE_DIR, settings
from app.metrics import metrics
from app.services.kev_client import KevJudge

router = APIRouter(tags=["ops"])
KEV = KevJudge()


def _probe(url: str, path: str = "/healthz") -> dict:
    try:
        r = httpx.get(f"{url.rstrip('/')}{path}", timeout=settings.readiness_timeout)
        result = {"ok": r.status_code == 200, "status": r.status_code}
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        for key in ("model_version", "auxiliary_enabled"):
            if key in payload:
                result[key] = payload[key]
        return result
    except Exception as exc:  # noqa: BLE001 - 探针只关心可用性
        return {"ok": False, "error": type(exc).__name__}


@router.get("/healthz")
def healthz():
    """liveness：不查下游，永远快速返回。"""
    return {"ok": True, "version": settings.version}


@router.get("/readyz")
def readyz(response: Response):
    """readiness：下游不可达时返回 503。"""
    vision = _probe(settings.vision_url)
    ocr = _probe(settings.ocr_service_url)
    kev = KEV.health()
    ready = vision["ok"] and ocr["ok"]
    if not ready:
        response.status_code = 503
    return {"ok": ready, "vision": vision, "ocr": ocr, "kev": kev}


@router.get("/v1/meta")
def meta():
    manifest_path = BASE_DIR / "models/current/manifest.json"
    try:
        model_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        model_manifest = {}
    return {
        "name": settings.app_name,
        "version": settings.version,
        "components": ["vision", "ocr", "rules"],
        "contract": {
            "api_version": "v1",
            "taxonomy_version": "v1",
            # 对外只有单张识别：HTTP 200 = 识别成功，422 = 不成功
            "single_image": True,
            "scene_count": 12,
            "response_fields": ["scene", "message", "data"],
            "auxiliary_fusion_enabled": settings.enable_fusion,
        },
        "model": {
            "configured_version": model_manifest.get("model_version"),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if manifest_path.is_file() else None,
            "configured_roles": sorted((model_manifest.get("roles") or {}).keys()),
        },
        "limits": {
            "max_concurrent": settings.max_concurrent,
            "max_image_mb": settings.max_image_bytes // (1024 * 1024),
            "request_deadline_s": settings.request_deadline_s,
            "admission_wait_s": settings.admission_wait_s,
        },
        "use_vlx": settings.use_vlx,
        "kev_mode": settings.kev_mode,
    }


@router.get("/metrics")
def prometheus_metrics():
    """Prometheus 文本格式。

    直接 `return str` 会被 FastAPI 当成 JSON 字符串序列化，
    结果是带引号、`\n` 转义的 `application/json`，Prometheus 抓取会解析失败。
    """
    return PlainTextResponse(
        metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
