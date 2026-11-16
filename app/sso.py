"""DEEPWAY 统一 SSO 接入（后端侧）。

契约来自 dw-sso-integration skill：
  - 登录态是根域 Cookie `dw-open-token[-dev|-test]`
  - 请求头三件套 `authorization` / `appCode` / `poolCode`
  - 登录失效码 `A0202/A0203/A0220/A0223/A0230`，无权限 `A0400/A0301`
  - 权限资源 `resourceType=3` 是菜单、`4` 是按钮，`resourceCodeName` 必须与前端视图名一致

**前端只做体验层**（隐藏菜单/跳 403），真正的鉴权在这里：
每个 `/v1/*`、`/viz/api/*` 请求都会校验 Token；拿不到合法身份就 401。

三种模式（`SSO_MODE`）：
  off    默认。不校验，保持现有演示可用。
  mock   本地联调用。任意 Token 都通过，返回固定用户与全部权限。
  live   生产。向用户中心 `/idaasweb/user/getOne` 校验 Token。
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import BASE_DIR

router = APIRouter(tags=["sso"])

# 前端视图名 == resourceCodeName。菜单权限就按这张表做交集。
VIEW_RESOURCES = {
    "overview": "SPM_OVERVIEW",
    "infer": "SPM_INFER",
    "dataset": "SPM_DATASET",
    "eval": "SPM_EVAL",
    "mistakes": "SPM_MISTAKES",
    "train": "SPM_TRAIN",
}

LOGIN_ERROR_CODES = {"A0202", "A0203", "A0220", "A0223", "A0230"}
DENY_ERROR_CODES = {"A0400", "A0301"}

# 不校验的路径：健康检查、SSO 自身、静态资源
PUBLIC_PREFIXES = (
    "/healthz", "/readyz", "/metrics", "/sso/", "/ui/", "/docs", "/openapi.json",
)
PUBLIC_EXACT = {"/", "/viz"}

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60.0


def abs_url(url: str) -> str:
    """把 `//host` 这种协议相对地址补成 https。

    前端可以用协议相对地址，服务端不行——httpx 会抛 UnsupportedProtocol，
    而异常被 fail-closed 分支吞掉后表现为「所有人都 401」，很难查。
    """
    url = (url or "").strip().rstrip("/")
    if url.startswith("//"):
        return "https:" + url
    if url and not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _settings():
    """延迟读取，避免 import 顺序问题。"""
    from app.config import settings as s
    return s


def token_key(host: str) -> str:
    """Cookie 名由 host 决定，不是由环境变量决定。"""
    h = (host or "").lower()
    if "dev" in h:
        return "dw-open-token-dev"
    if "test" in h:
        return "dw-open-token-test"
    return "dw-open-token"


def extract_token(request: Request) -> str:
    """优先取请求头，其次取根域 Cookie。"""
    auth = request.headers.get("authorization") or ""
    if auth:
        return auth.strip()
    cookie = request.cookies.get(token_key(request.headers.get("host", "")))
    return (cookie or "").strip()


def _extract_resources(node, depth: int = 0) -> list[dict]:
    """从任意嵌套结构里找出权限资源列表。"""
    if depth > 6:
        return []
    if isinstance(node, list):
        if node and all(isinstance(i, dict) and "resourceCodeName" in i for i in node):
            return node
        for item in node:
            found = _extract_resources(item, depth + 1)
            if found:
                return found
    elif isinstance(node, dict):
        for key in ("resources", "resourceList", "menus", "list", "data"):
            if key in node:
                found = _extract_resources(node[key], depth + 1)
                if found:
                    return found
        for value in node.values():
            found = _extract_resources(value, depth + 1)
            if found:
                return found
    return []


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _mock_user() -> dict:
    return {
        "userName": "本地调试",
        "account": "dev",
        "resources": [
            {"resourceType": 3, "resourceCodeName": v} for v in VIEW_RESOURCES.values()
        ] + [{"resourceType": 4, "resourceCodeName": f"{v}_EXPORT"} for v in VIEW_RESOURCES.values()],
    }


async def _fetch_user(token: str) -> dict | None:
    """向用户中心校验 Token，返回用户信息（含 resources）。失败返回 None。"""
    s = _settings()
    headers = {
        "authorization": token,
        "appCode": s.sso_app_code,
        "poolCode": s.sso_pool_code,
    }
    base = abs_url(s.sso_user_base)
    if not base:
        return None
    url = f"{base}/idaasweb/user/getOne"
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if str(data.get("code")) != "00000" and data.get("status") != "success":
        return None
    payload = data.get("data") or {}
    # 权限资源单独拉一次；接口名来自契约文档
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # 注意：skill 文档写的是 authorizeAndAuthority，实际接口是 authorizeAndPermission
            # （从 ihome 登录页的 umi bundle 里挖出来的）
            r2 = await client.get(
                f"{abs_url(s.sso_user_base)}/idaasweb/user/login/authorizeAndPermission",
                headers=headers)
            d2 = r2.json()
        if str(d2.get("code")) == "00000" or d2.get("status") == "success":
            # 权限接口的确切结构没有文档，用「递归找带 resourceCodeName 的列表」兜底，
            # 避免字段名变化导致所有人菜单为空。
            payload["resources"] = _extract_resources(d2)
    except Exception:  # noqa: BLE001 - 权限接口失败时按无权限处理
        payload.setdefault("resources", [])
    return payload


async def resolve_user(request: Request) -> dict | None:
    """带缓存的用户解析。"""
    s = _settings()
    token = extract_token(request)
    if not token:
        return None
    if s.sso_mode == "mock":
        return _mock_user()

    key = _cache_key(token)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        user = await _fetch_user(token)
    except Exception:  # noqa: BLE001 - 用户中心不可达 → 视为未认证（fail closed）
        return None
    if user is None:
        return None
    _CACHE[key] = (now, user)
    return user


def menus_of(user: dict) -> list[str]:
    """resourceType=3 的菜单编码。"""
    out = []
    for r in user.get("resources") or []:
        try:
            if int(r.get("resourceType") or 0) == 3:
                code = r.get("resourceCodeName")
                if code:
                    out.append(code)
        except (TypeError, ValueError):
            continue
    return out


@router.get("/sso/config")
def sso_config(request: Request):
    """前端启动时读这里，避免把 appCode/poolCode 写死在前端代码里。"""
    s = _settings()
    return {
        "enabled": s.sso_mode != "off",
        "mode": s.sso_mode,
        "openLoginDomain": s.sso_open_login_domain,
        "appCode": s.sso_app_code,
        "poolCode": s.sso_pool_code,
        "tokenKey": token_key(request.headers.get("host", "")),
        "views": VIEW_RESOURCES,
    }


@router.get("/sso/me")
async def sso_me(request: Request):
    """当前用户 + 可用菜单。前端据此隐藏导航。"""
    s = _settings()
    if s.sso_mode == "off":
        return {"enabled": False, "user": None, "menus": list(VIEW_RESOURCES.values())}
    user = await resolve_user(request)
    if user is None:
        raise HTTPException(401, detail={"code": "A0220", "message": "登录已失效"})
    menus = menus_of(user)
    return {
        "enabled": True,
        "user": {k: user.get(k) for k in ("userName", "account", "email", "phone")},
        "menus": menus,
        "views": VIEW_RESOURCES,
    }


@router.post("/sso/logout")
def sso_logout():
    """后端不持有会话，登出由前端清 Cookie 后跳 SSO。这里只做占位，便于前端统一调用。"""
    return {"ok": True}


async def sso_middleware(request: Request, call_next):
    """统一鉴权：所有业务接口都必须带合法 Token。"""
    s = _settings()
    if s.sso_mode == "off":
        return await call_next(request)

    path = request.url.path
    if path in PUBLIC_EXACT or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)

    user = await resolve_user(request)
    if user is None:
        # 登录失效：返回契约里的错误码，前端据此统一登出
        return JSONResponse(status_code=401,
                            content={"code": "A0220", "message": "登录已失效"})

    request.state.user = user
    request.state.menus = menus_of(user)
    return await call_next(request)
