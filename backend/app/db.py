"""Async database layer.

The connection string is normalised for asyncpg and for Neon's pooled
(pgbouncer, transaction-mode) endpoint:

* ``postgres://`` and ``postgresql://`` become ``postgresql+asyncpg://``.
* libpq-only query arguments (``sslmode``, ``channel_binding``, ...) are
  stripped, because asyncpg rejects unknown kwargs; ``sslmode`` is translated
  into an ``ssl`` connect argument instead.
* server-side prepared statements are disabled by default, which pgbouncer in
  transaction mode requires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Query arguments that only make sense to libpq/psycopg, not asyncpg.
_LIBPQ_ONLY_ARGS = {
    "sslmode",
    "channel_binding",
    "connect_timeout",
    "application_name",
    "target_session_attrs",
    "options",
    "gssencmode",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "keepalives",
    "keepalives_idle",
    "keepalives_interval",
    "keepalives_count",
    "tcp_user_timeout",
    "statement_cache_size",
    "prepared_statement_cache_size",
    "pgbouncer",
    "supa",
    "schema",
    "search_path",
}

_SSL_MODES = {"require", "verify-ca", "verify-full", "prefer", "allow"}

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_lock = asyncio.Lock()


def normalize_database_url(
    raw: str,
    *,
    statement_cache_size: int = 0,
    connect_timeout: float = 10.0,
) -> tuple[str, dict[str, Any]]:
    """Return ``(async_url, connect_args)`` ready for ``create_async_engine``."""
    if not raw or not raw.strip():
        raise ValueError("DATABASE_URL is not set")

    url = raw.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        scheme = "postgresql+asyncpg"

    if scheme.startswith("sqlite"):
        # Used only by the test suite; production is always Postgres.
        return url, {}

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    sslmode = query.get("sslmode", "").lower()

    connect_args: dict[str, Any] = {
        "timeout": float(connect_timeout),
        "command_timeout": 60.0,
        "statement_cache_size": int(statement_cache_size),
    }
    host = (parts.hostname or "").lower()
    needs_ssl = sslmode in _SSL_MODES or host.endswith(".neon.tech") or "neon.tech" in host
    if needs_ssl and sslmode not in {"disable"}:
        connect_args["ssl"] = True

    # Drop every libpq-only parameter; asyncpg accepts none of them.
    for key in list(query):
        if key in _LIBPQ_ONLY_ARGS:
            query.pop(key)

    clean = urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return clean, connect_args


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    url, connect_args = normalize_database_url(
        settings.database_url,
        statement_cache_size=settings.db_statement_cache_size,
        connect_timeout=settings.db_connect_timeout_seconds,
    )

    if url.startswith("sqlite"):
        is_memory = ":memory:" in url or url.endswith("sqlite+aiosqlite://")
        return create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=StaticPool if is_memory else NullPool,
            connect_args=connect_args,
        )

    return create_async_engine(
        url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_timeout=30.0,
        connect_args=connect_args,
    )


async def init_engine(settings: Settings | None = None) -> AsyncEngine | None:
    """Create the process-wide engine. Returns ``None`` when unconfigured."""
    global _engine, _sessionmaker
    settings = settings or get_settings()
    if not settings.database_configured:
        return None
    async with _lock:
        if _engine is None:
            _engine = build_engine(settings)
            _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine is not initialised (DATABASE_URL missing?)")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database engine is not initialised (DATABASE_URL missing?)")
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session; commits on success, rolls back on error."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping() -> tuple[bool, float]:
    """Cheap liveness probe. Returns ``(ok, latency_ms)``."""
    if _engine is None:
        return False, 0.0
    started = time.perf_counter()
    try:
        async with _engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - health probes never raise
        logger.warning("database ping failed: %s", type(exc).__name__)
        return False, (time.perf_counter() - started) * 1000
    return True, (time.perf_counter() - started) * 1000


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    async with _lock:
        if _engine is not None:
            await _engine.dispose()
        _engine = None
        _sessionmaker = None
