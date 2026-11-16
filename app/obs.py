"""可观测性与准入：请求 ID、访问日志、统一错误结构、API Key。

产品化最基本的要求——出了问题能定位到某一次请求，外部调用能被管住。

开启 API Key 后：
    API_KEYS=k1,k2 python -m uvicorn ...
    curl -H "X-API-Key: k1" ...

不配置 `API_KEYS` 时不校验（本地开发和内网演示）。**对外提供服务时必须配。**
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

log = logging.getLogger("spm.access")

# 错误码：给调用方判断用，不依赖 HTTP 状态码语义
CODE_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    413: "PAYLOAD_TOO_LARGE",
    422: "INVALID_PARAMS",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

# 这些路径不需要 API Key：健康检查、静态资源、文档
OPEN_PREFIXES = ("/healthz", "/readyz", "/metrics", "/ui/", "/viz", "/sso/", "/docs", "/openapi.json")
OPEN_EXACT = {"/", "/favicon.ico"}


def _keys() -> set[str]:
    raw = getattr(settings, "api_keys", "") or ""
    return {k.strip() for k in raw.split(",") if k.strip()}


def needs_key(path: str) -> bool:
    if path in OPEN_EXACT or any(path.startswith(p) for p in OPEN_PREFIXES):
        return False
    return path.startswith("/v1/")


async def observability_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    started = time.perf_counter()

    # ---- API Key 准入 ----
    # 浏览器同源请求（内部控制台）放行：浏览器会带 Sec-Fetch-Site，
    # 服务端/脚本调用不会带。不追求防住有意伪造，只用它区分「人点的页面」和「机器调用」。
    same_origin = request.headers.get("sec-fetch-site") in ("same-origin", "same-site")
    keys = _keys()
    if keys and needs_key(request.url.path) and not same_origin:
        got = (request.headers.get("x-api-key") or "").strip()
        if not got:
            return _err(401, "缺少 X-API-Key", rid, request)
        if got not in keys:
            return _err(401, "X-API-Key 无效", rid, request)

    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 - 兜底，保证返回统一结构
        log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        return _err(500, "服务内部错误", rid, request)

    elapsed = (time.perf_counter() - started) * 1000
    response.headers["X-Request-Id"] = rid
    log.info("%s %s %s %.1fms rid=%s",
             request.method, request.url.path, response.status_code, elapsed, rid)
    return response


def _err(status: int, message: str, rid: str, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": CODE_BY_STATUS.get(status, "ERROR"), "message": message,
                 "request_id": rid, "path": request.url.path},
        headers={"X-Request-Id": rid},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    rid = getattr(request.state, "request_id", "-")
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code") or CODE_BY_STATUS.get(exc.status_code, "ERROR")
        message = detail.get("message") or str(detail)
    else:
        code = CODE_BY_STATUS.get(exc.status_code, "ERROR")
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "message": message, "request_id": rid,
                 "path": request.url.path},
        headers={"X-Request-Id": rid},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    rid = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=422,
        content={"code": "INVALID_PARAMS", "message": "参数校验失败",
                 "errors": exc.errors()[:5], "request_id": rid,
                 "path": request.url.path},
        headers={"X-Request-Id": rid},
    )
