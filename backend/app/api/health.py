"""Health endpoints.

``/health`` is the liveness probe used by Render and by the uptime pinger
(it answers 200 even while the DB is briefly unreachable, so a pinger keeps
the free instance awake).  ``/health/ready`` is strict and returns 503 unless
the database answers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response, status

from app import db
from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    db_state = "not_configured"
    db_latency_ms: float | None = None

    if settings.database_configured:
        ok, latency = await db.ping()
        db_state = "up" if ok else "down"
        db_latency_ms = round(latency, 1)

    return {
        "status": "ok" if db_state in {"up", "not_configured"} else "degraded",
        "service": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "db": db_state,
        "db_latency_ms": db_latency_ms,
        "worker_enabled": settings.worker_enabled,
        "time": datetime.now(UTC).isoformat(),
    }


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    settings = get_settings()
    if not settings.database_configured:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": False, "reason": "database_not_configured"}

    ok, latency = await db.ping()
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": False, "reason": "database_unreachable"}

    return {"ready": True, "db_latency_ms": round(latency, 1)}
