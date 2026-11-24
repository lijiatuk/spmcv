"""按 URL 拉取图片。

业务系统把照片先放到对象存储，再传 URL。只做最基本的限额与协议校验，
不做域名白名单（调用方都是内部系统，给的都是可访问地址）。

限制：
- 只接受 http / https；
- 连接 3s、读取 6s（外层还有 5s 总预算，下载上限 3s）；
- 最多 15MB，边下边限，超限 413；
- 最多 3 次重定向；
- 按 magic bytes 判格式（不信任后缀 / content-type）。
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from app.config import settings
from app.limits import sniff_image

_MAX_REDIRECTS = 3
_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 6.0

# 复用同一个 AsyncClient：每次新建都会重建 SSL 上下文，实测在部分机器上
# 首次要 3–4s，正好把 3s 的下载预算吃光（表现成「第一次 URL 请求一定超时」）。
_client: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        timeout = httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT,
                                write=_READ_TIMEOUT, pool=_READ_TIMEOUT)
        # trust_env=False：内网对象存储不需要走系统代理；走了反而会在
        # 代理不可达时表现成「下载超时」（本地联调已复现）。
        _client = httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                    max_redirects=_MAX_REDIRECTS, trust_env=False)
    return _client


async def warmup() -> None:
    """服务启动时预热：把 AsyncClient / SSL 上下文的一次性开销提前吃掉。"""
    try:
        _shared_client()
    except Exception:  # noqa: BLE001 - 预热失败不影响启动
        pass


async def fetch_image(url: str) -> tuple[bytes, str]:
    """下载 URL 指向的图片，返回 (bytes, filename)。"""
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "image_url 为空")
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "image_url 只支持 http / https")

    limit = settings.max_image_bytes
    buf = bytearray()
    filename = url.rsplit("/", 1)[-1].split("?")[0] or "image"

    try:
        client = _shared_client()
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise HTTPException(400, f"图片下载失败：HTTP {resp.status_code}")
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
            # 部分对象存储返回 octet-stream，交给 magic bytes 判定
            if ctype and not (ctype.startswith("image/")
                              or ctype == "application/octet-stream"):
                raise HTTPException(400, f"URL 不是图片：{ctype}")
            async for chunk in resp.aiter_bytes(64 * 1024):
                buf.extend(chunk)
                if len(buf) > limit:
                    mb = limit // (1024 * 1024)
                    raise HTTPException(413, f"图片超过 {mb}MB")
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(400, "图片下载超时")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"图片下载失败：{type(exc).__name__}")

    data = bytes(buf)
    if not data:
        raise HTTPException(400, "图片内容为空")
    sniff_image(data)  # 不信任 content-type / 后缀
    return data, filename