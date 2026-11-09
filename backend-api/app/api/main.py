from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.errors import register_error_handlers
from app.api.middleware import SecurityHeadersMiddleware
from app.api.routes import chat, documents, family, financial, ops, vault
from app.config import Settings, get_settings
from app.database import close_pool, init_pool
from app.observability import RequestIDMiddleware, configure_logging
from app.ratelimit import limiter

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await init_pool(
            settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            max_attempts=settings.db_connect_max_attempts,
            base_delay_s=settings.db_connect_base_delay_s,
        )
        logger.info("api started", extra={"env": settings.env, "run_mode": settings.run_mode})
        try:
            yield
        finally:
            await close_pool()

    app = FastAPI(
        title="FamGuard AI API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.env == "prod" else "/docs",
        redoc_url=None,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.env == "prod")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(RequestIDMiddleware)
    app.state.limiter = limiter
    register_error_handlers(app)
    app.include_router(ops.router)
    app.include_router(family.router)
    app.include_router(vault.router)
    app.include_router(financial.router)
    app.include_router(documents.router)
    app.include_router(chat.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
