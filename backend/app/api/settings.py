"""Dashboard-editable runtime settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app import db
from app.config import get_settings as get_env_settings
from app.security import require_dashboard
from app.services.settings_service import (
    SettingsError,
    describe,
    load_overrides,
    save_overrides,
)

router = APIRouter(
    prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_dashboard)]
)


@router.get("")
async def read_settings() -> dict[str, Any]:
    settings = get_env_settings()
    if not db.is_configured():
        return describe(settings, {})
    async with db.session_scope() as session:
        overrides = await load_overrides(session)
    return describe(settings, overrides)


@router.put("")
async def update_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    settings = get_env_settings()
    try:
        async with db.session_scope() as session:
            overrides = await save_overrides(session, payload)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return describe(settings, overrides)
