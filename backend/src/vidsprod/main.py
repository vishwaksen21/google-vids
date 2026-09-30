"""FastAPI application: lifespan wiring, security middleware, routers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vidsprod.api import errors
from vidsprod.api.routes import campaigns, jobs, system
from vidsprod.config import get_settings
from vidsprod.container import Container
from vidsprod.logging import configure_logging, get_logger

log = get_logger(__name__)
_UPLOAD_PATHS = ("/api/references",)
_UPLOAD_LIMIT = 11 * 1024 * 1024


def create_app(container: Container | None = None) -> FastAPI:
    settings = container.settings if container else get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container or await Container.build(settings)
        app.state.container = c
        if settings.api_token is None:
            log.warning("API_TOKEN is not set: the API is unauthenticated (development only)")
        if settings.worker_embedded and container is None:
            c.start_worker()
        log.info("vidsprod started", mode=settings.google_vids_mode.value,
                 provider=settings.ai_provider.value, simulation=settings.simulation)
        yield
        if container is None:
            await c.close()

    app = FastAPI(title="Vids Production", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH"],
                       allow_headers=["Authorization", "Content-Type", "X-Actor", "Idempotency-Key"])

    @app.middleware("http")
    async def limits_and_headers(request: Request, call_next):
        length = request.headers.get("content-length")
        limit = _UPLOAD_LIMIT if request.url.path in _UPLOAD_PATHS else settings.max_request_bytes
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse({"error": {"code": "TOO_LARGE", "message": "request body too large"}},
                                status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    errors.install(app)
    app.include_router(system.public)
    app.include_router(system.router)
    app.include_router(campaigns.router)
    app.include_router(jobs.router)
    return app


app = create_app()
