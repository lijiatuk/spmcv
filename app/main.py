from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import analyze, categories, health, train, viz, workorder
from app import admission, obs, sso
from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(obs.observability_middleware)
    app.middleware("http")(admission.admission_middleware)
    app.middleware("http")(sso.sso_middleware)
    app.add_exception_handler(StarletteHTTPException, obs.http_exception_handler)
    app.add_exception_handler(RequestValidationError, obs.validation_exception_handler)
    app.include_router(health.router)
    app.include_router(sso.router)
    app.include_router(viz.router)
    app.include_router(train.router)
    # 三类场景各一个接口，调用方按素材目录直接选
    app.include_router(categories.router, prefix=settings.api_prefix)
    app.include_router(analyze.router, prefix=settings.api_prefix)
    app.include_router(workorder.router, prefix=settings.api_prefix)
    @app.middleware("http")
    async def _no_cache_ui(request, call_next):
        """前端资源一律不缓存。

        踩过的坑：修了画框偏移，用户刷新页面仍然看到旧效果——因为浏览器
        用了 ETag 启发式缓存。控制台是内部工具，资源就几十 KB，
        设成 no-store 让每次刷新都拿到最新版本，比让用户手动清缓存靠谱。
        """
        response = await call_next(request)
        if request.url.path.startswith("/ui/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    app.mount("/ui", StaticFiles(directory=str(settings.web_dir), html=True), name="ui")

    @app.get("/viz")
    def viz_page():
        return RedirectResponse(url="/ui/viz.html")

    @app.get("/")
    def root():
        """根路径直接进控制台：访问 http://ip:8020/ 即可，不用记 /ui/console.html。"""
        return RedirectResponse(url="/ui/console.html")

    return app


app = create_app()
