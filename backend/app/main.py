"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.api import health
from app.config import get_settings
from app.logs import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting %s v%s (env=%s, mode=%s)",
        settings.app_name,
        settings.version,
        settings.environment,
        settings.review_mode,
    )

    if settings.database_configured:
        await db.init_engine(settings)
        ok, latency = await db.ping()
        if ok:
            logger.info("database reachable (%.1f ms)", latency)
        else:
            # Do not crash: Render should keep the service up and the pinger
            # should still get a response while Neon wakes up.
            logger.error("database unreachable at startup; check DATABASE_URL")
    else:
        logger.warning("DATABASE_URL is not set - database features are disabled")

    try:
        yield
    finally:
        await db.dispose_engine()
        logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )

    app.include_router(health.router)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": settings.app_name,
                "version": settings.version,
                "docs": "/docs" if not settings.is_production else None,
                "health": "/health",
            }
        )

    return app


app = create_app()
