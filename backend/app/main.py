"""FastAPI application entrypoint.

One process serves the HTTP API, the queue worker and the MCP endpoint, because
the free tier allows exactly one backend service.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from app import db
from app.api import auth, health, runs, usage, webhooks
from app.api import settings as settings_api
from app.config import Settings, get_settings
from app.logs import configure_logging
from app.mcp.server import BearerGuard, build_public_mcp_server
from app.services.worker import Worker

logger = logging.getLogger(__name__)

MCP_MOUNT_PATH = "/mcp"


class McpPathMiddleware:
    """Serve ``/mcp`` directly instead of redirecting to ``/mcp/``."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == MCP_MOUNT_PATH:
            scope = dict(scope)
            scope["path"] = MCP_MOUNT_PATH + "/"
            scope["raw_path"] = (MCP_MOUNT_PATH + "/").encode()
        await self.app(scope, receive, send)


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    hosts = [item.strip() for item in settings.mcp_allowed_hosts.split(",") if item.strip()]
    origins = [item.strip() for item in settings.mcp_allowed_origins.split(",") if item.strip()]
    wildcard = "*" in hosts or "*" in origins
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=not wildcard,
        allowed_hosts=[] if wildcard else hosts,
        allowed_origins=[] if wildcard else origins,
    )


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
            logger.error("database unreachable at startup; check DATABASE_URL")
    else:
        logger.warning("DATABASE_URL is not set - database features are disabled")

    worker: Worker | None = None
    async with AsyncExitStack() as stack:
        session_manager = getattr(app.state, "mcp_session_manager", None)
        if session_manager is not None:
            try:
                await stack.enter_async_context(session_manager.run())
                logger.info("MCP endpoint ready at %s", MCP_MOUNT_PATH)
            except Exception:  # noqa: BLE001 - never block startup on MCP
                logger.exception("could not start the MCP session manager")

        if settings.database_configured and settings.worker_enabled:
            worker = Worker(settings)
            try:
                await worker.start()
            except Exception:  # noqa: BLE001
                logger.exception("worker failed to start")
                worker = None
        else:
            logger.info("worker disabled (WORKER_ENABLED=false or no database)")

        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
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
    app.add_middleware(McpPathMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(runs.router)
    app.include_router(usage.router)
    app.include_router(settings_api.router)
    app.include_router(webhooks.router)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": settings.app_name,
                "version": settings.version,
                "docs": "/docs" if not settings.is_production else None,
                "health": "/health",
                "mcp": MCP_MOUNT_PATH,
            }
        )

    # Read-only MCP surface in this same process, bearer-protected.
    mcp_server = build_public_mcp_server()
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/",
        transport_security=_transport_security(settings),
    )
    app.state.mcp_session_manager = mcp_server.session_manager
    app.mount(MCP_MOUNT_PATH, BearerGuard(mcp_app, settings.dashboard_password))

    return app


app = create_app()
