"""接口准入控制：并发上限 + 每调用方限流。

为什么需要：接口开放给多个业务方后，任何一方突发流量都会把推理服务打满，
所有人一起超时——比"部分请求快速失败"糟糕得多。

策略：
  1. 全局并发上限（`MAX_CONCURRENT`），满了就**快速返回 429**，而不是排队排到超时；
  2. 按调用方限流（有 API Key 按 Key，否则按 IP），每分钟 N 次；
  3. 429 都带 `Retry-After`，调用方按提示退避即可。

失败也要可预期：宁可 1 秒内拿到 429，不要 30 秒后拿到超时。
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings

# 这些路径需要准入控制（业务接口），静态资源和健康检查不需要
GUARDED_PREFIX = "/v1/"

_inflight = asyncio.Semaphore(max(1, settings.max_concurrent))
_wait_s = float(settings.admission_wait_s)

# 每个调用方的滑动窗口计数：client -> [window_start, count]
_buckets: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
_rate_per_min = int(settings.rate_limit_per_min)


def _client_of(request: Request) -> str:
    key = (request.headers.get("x-api-key") or "").strip()
    if key:
        return "key:" + key[:12]
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return "ip:" + fwd.split(",")[0].strip()
    return "ip:" + (request.client.host if request.client else "unknown")


def _rate_ok(client: str, now: float) -> tuple[bool, float]:
    """滑动窗口（60s）。返回 (是否放行, 建议等待秒数)。"""
    if _rate_per_min <= 0:
        return True, 0.0
    bucket = _buckets[client]
    if now - bucket[0] >= 60.0:
        bucket[0] = now
        bucket[1] = 0.0
    if bucket[1] >= _rate_per_min:
        return False, max(1.0, 60.0 - (now - bucket[0]))
    bucket[1] += 1
    return True, 0.0


async def admission_middleware(request: Request, call_next):
    if not request.url.path.startswith(GUARDED_PREFIX):
        return await call_next(request)

    now = time.monotonic()
    client = _client_of(request)
    allowed, retry_after = _rate_ok(client, now)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_LIMITED",
                     "message": f"调用频率超限（{_rate_per_min}/分钟），请稍后重试",
                     "request_id": getattr(request.state, "request_id", "-")},
            headers={"Retry-After": str(int(retry_after))},
        )

    try:
        await asyncio.wait_for(_inflight.acquire(), timeout=_wait_s)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=429,
            content={"code": "BUSY",
                     "message": f"服务繁忙（并发上限 {settings.max_concurrent}），请稍后重试",
                     "request_id": getattr(request.state, "request_id", "-")},
            headers={"Retry-After": "1"},
        )

    try:
        return await call_next(request)
    finally:
        _inflight.release()
