"""Password exchange for a signed session token."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.security import (
    SESSION_COOKIE,
    issue_session,
    password_matches,
    require_dashboard,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 60
_attempts: dict[str, list[float]] = defaultdict(list)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() or (request.client.host if request.client else "?"))[
        :64
    ]


def _throttled(key: str) -> bool:
    now = time.monotonic()
    recent = [stamp for stamp in _attempts[key] if now - stamp < _WINDOW_SECONDS]
    _attempts[key] = recent
    return len(recent) >= _MAX_ATTEMPTS


def _record(key: str) -> None:
    _attempts[key].append(time.monotonic())


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DASHBOARD_PASSWORD is not configured on the backend.",
        )

    key = _client_key(request)
    if _throttled(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again in a minute.",
        )

    if not password_matches(settings, payload.password):
        _record(key)
        # Slow brute-force attempts without leaking timing information.
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    _attempts.pop(key, None)
    token, ttl = issue_session(settings)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path="/",
    )
    return {"token": token, "expires_in": ttl}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(_: dict = Depends(require_dashboard)) -> dict:
    settings = get_settings()
    return {
        "authenticated": True,
        "environment": settings.environment,
        "version": settings.version,
        "review_mode": settings.review_mode,
        "worker_enabled": settings.worker_enabled,
    }
