"""Runtime-tunable settings stored in Postgres.

Env vars are the defaults; the dashboard may override a small, validated subset
(review mode, comment cap, agent steps, daily budget, filters).
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Setting

logger = logging.getLogger(__name__)

SETTINGS_KEY = "review"

EDITABLE_DEFAULTS: dict[str, Any] = {
    "review_mode": "lite",
    "max_inline_comments": 25,
    "max_agent_steps": 3,
    "daily_llm_budget": 18,
    "ignore_globs": "",
    "max_files_per_review": 40,
}

_INT_FIELDS = {
    "max_inline_comments": (1, 100),
    "max_agent_steps": (1, 10),
    "daily_llm_budget": (0, 10_000),
    "max_files_per_review": (1, 200),
}


class SettingsError(ValueError):
    pass


def validate_overrides(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if key not in EDITABLE_DEFAULTS:
            continue
        if key == "review_mode":
            mode = str(value).lower()
            if mode not in {"lite", "agent"}:
                raise SettingsError("review_mode must be 'lite' or 'agent'")
            clean[key] = mode
        elif key in _INT_FIELDS:
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"{key} must be an integer") from exc
            low, high = _INT_FIELDS[key]
            if not low <= number <= high:
                raise SettingsError(f"{key} must be between {low} and {high}")
            clean[key] = number
        elif key == "ignore_globs":
            if isinstance(value, list):
                clean[key] = ",".join(str(item).strip() for item in value if str(item).strip())
            else:
                clean[key] = str(value)
    return clean


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(Setting, SETTINGS_KEY)
    if row is None or not isinstance(row.value, dict):
        return {}
    return validate_overrides(row.value)


async def save_overrides(session: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    current = await load_overrides(session)
    merged = {**current, **validate_overrides(data)}
    row = await session.get(Setting, SETTINGS_KEY)
    if row is None:
        session.add(Setting(key=SETTINGS_KEY, value=merged))
    else:
        row.value = merged
        row.updated_at = sa.func.now()
    await session.flush()
    return merged


def effective_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    """Copy of the env settings with dashboard overrides applied."""
    updates: dict[str, Any] = {}
    for key, value in overrides.items():
        if key == "ignore_globs" and not value:
            continue
        if (
            key in {"review_mode", "max_inline_comments", "max_agent_steps", "daily_llm_budget"}
            or key == "max_files_per_review"
            or key == "ignore_globs"
        ):
            updates[key] = value
    if not updates:
        return base
    try:
        return base.model_copy(update=updates)
    except Exception:  # noqa: BLE001 - never fail a run over a bad override
        logger.warning("ignoring invalid runtime overrides")
        return base


def describe(base: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    effective = effective_settings(base, overrides)
    return {
        "review_mode": effective.review_mode,
        "max_inline_comments": effective.max_inline_comments,
        "max_agent_steps": effective.max_agent_steps,
        "daily_llm_budget": effective.daily_llm_budget,
        "max_files_per_review": effective.max_files_per_review,
        "ignore_globs": effective.ignore_glob_list,
        "defaults": {
            "review_mode": base.review_mode,
            "max_inline_comments": base.max_inline_comments,
            "max_agent_steps": base.max_agent_steps,
            "daily_llm_budget": base.daily_llm_budget,
            "max_files_per_review": base.max_files_per_review,
            "ignore_globs": base.ignore_glob_list,
        },
    }
