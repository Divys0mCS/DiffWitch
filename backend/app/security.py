"""Dashboard authentication.

One shared password (`DASHBOARD_PASSWORD`) exchanged for a signed, expiring
session token.  Tokens are HMAC-SHA256 signed with `SESSION_SECRET`; the raw
password is also accepted as a bearer token so scripts work without a login
round trip.  Every comparison is constant time.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "diffwitch_session"


def _signing_key(settings: Settings) -> bytes:
    if settings.session_secret:
        return settings.session_secret.encode()
    # Derive a stable key so dev sessions survive a reload without a secret.
    derived = hashlib.sha256(f"diffwitch-session:{settings.dashboard_password}".encode()).digest()
    return derived


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def password_matches(settings: Settings, password: str) -> bool:
    if not settings.dashboard_password:
        return False
    return hmac.compare_digest(password.encode(), settings.dashboard_password.encode())


def issue_session(settings: Settings, *, now: float | None = None) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``."""
    ttl = max(60, settings.session_ttl_hours * 3600)
    issued = int(now if now is not None else time.time())
    payload = {"sub": "dashboard", "iat": issued, "exp": issued + ttl}
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_signing_key(settings), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}", ttl


def verify_session(settings: Settings, token: str, *, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    body, _, signature = token.rpartition(".")
    expected = hmac.new(_signing_key(settings), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        payload = json.loads(_b64decode(body))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    expires = payload.get("exp")
    current = now if now is not None else time.time()
    return isinstance(expires, int) and expires > current


def _token_from_request(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    # EventSource cannot set headers, so SSE accepts ?token=.
    return request.query_params.get("token", "")


async def require_dashboard(request: Request) -> dict[str, Any]:
    """FastAPI dependency guarding every dashboard route."""
    settings = get_settings()
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard auth is not configured: set DASHBOARD_PASSWORD.",
        )
    token = _token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if verify_session(settings, token) or password_matches(settings, token):
        return {"sub": "dashboard"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired session",
        headers={"WWW-Authenticate": "Bearer"},
    )
