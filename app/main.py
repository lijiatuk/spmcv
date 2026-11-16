from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import analyze, categories, health, viz, workorder
from app import obs, sso
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
    app.middleware("http")(sso.sso_middleware)
    app.add_exception_handler(StarletteHTTPException, obs.http_exception_handler)
    app.add_exception_handler(RequestValidationError, obs.validation_exception_handler)
    app.include_router(health.router)
    app.include_router(sso.router)
    app.include_router(viz.router)
    # 三类场景各一个接口，调用方按素材目录直接选
    app.include_router(categories.router, prefix=settings.api_prefix)
    app.include_router(analyze.router, prefix=settings.api_prefix)
    app.include_router(workorder.router, prefix=settings.api_prefix)
    app.mount("/ui", StaticFiles(directory=str(settings.web_dir), html=True), name="ui")

    @app.get("/viz")
    def viz_page():
        return RedirectResponse(url="/ui/viz.html")

    @app.get("/")
    def root():
        return RedirectResponse(url="/ui/")

    return app


app = create_app()
