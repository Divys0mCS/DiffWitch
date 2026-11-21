"""Shared pytest configuration.

Environment is pinned before any app module is imported so settings never pick
up a developer's real ``.env``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = ""
os.environ["WORKER_ENABLED"] = "false"
os.environ["DASHBOARD_PASSWORD"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GITHUB_WEBHOOK_SECRET"] = ""

import pytest  # noqa: E402
from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def sqlite_engine():
    """In-memory SQLite engine with all tables created."""
    from app.models import Base

    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    get_settings.cache_clear()
    engine = await db.init_engine()
    assert engine is not None
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await db.dispose_engine()
        os.environ["DATABASE_URL"] = ""
        get_settings.cache_clear()
