"""Daily budget + per-minute rate guard.

Free-tier Gemini is extremely tight (often 5 requests/minute, ~20/day), so the
counter lives in Postgres and is incremented atomically *before* the call.  If
the reservation fails the run is marked ``skipped: quota`` instead of burning
the remaining allowance.

Without a database (CLI, tests) the guard falls back to in-process counters.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings

logger = logging.getLogger(__name__)

_RESERVE_SQL = sa.text(
    """
    INSERT INTO usage_daily (day, llm_calls, tokens_in, tokens_out, runs, updated_at)
    VALUES (:day, 1, 0, 0, 0, CURRENT_TIMESTAMP)
    ON CONFLICT (day) DO UPDATE
    SET llm_calls = usage_daily.llm_calls + 1,
        updated_at = CURRENT_TIMESTAMP
    WHERE usage_daily.llm_calls < :budget
    RETURNING llm_calls
    """
)

_RECORD_SQL = sa.text(
    """
    INSERT INTO usage_daily (day, llm_calls, tokens_in, tokens_out, runs, updated_at)
    VALUES (:day, 0, :tokens_in, :tokens_out, :runs, CURRENT_TIMESTAMP)
    ON CONFLICT (day) DO UPDATE
    SET tokens_in = usage_daily.tokens_in + :tokens_in,
        tokens_out = usage_daily.tokens_out + :tokens_out,
        runs = usage_daily.runs + :runs,
        updated_at = CURRENT_TIMESTAMP
    """
)

_SNAPSHOT_SQL = sa.text(
    """
    SELECT day, llm_calls, tokens_in, tokens_out, runs
    FROM usage_daily
    WHERE day = :day
    """
)


class QuotaExceeded(RuntimeError):
    """Raised when a call must not be made."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class QuotaGuard:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._rpm = max(0, settings.llm_rpm)
        self._budget = settings.daily_llm_budget
        self._factory = session_factory
        self._sleep = sleeper or asyncio.sleep
        self._today = today or (lambda: datetime.now(UTC).date())
        self._recent: deque[float] = deque()
        self._memory_calls = 0
        self._memory_tokens_in = 0
        self._memory_tokens_out = 0
        self._memory_runs = 0
        self._memory_day: date = self._today()

    # -- internals ---------------------------------------------------------
    @property
    def effective_budget(self) -> int:
        """``<= 0`` means unlimited (documented)."""
        return self._budget if self._budget > 0 else 2_147_483_647

    def _roll_memory_day(self) -> None:
        today = self._today()
        if today != self._memory_day:
            self._memory_day = today
            self._memory_calls = 0
            self._memory_tokens_in = 0
            self._memory_tokens_out = 0
            self._memory_runs = 0

    async def _session_factory(self) -> async_sessionmaker[AsyncSession] | None:
        if self._factory is not None:
            return self._factory
        try:
            from app import db

            return db.get_sessionmaker()
        except RuntimeError:
            return None

    async def _wait_for_rpm(self) -> None:
        if self._rpm <= 0:
            return
        waited = 0.0
        while True:
            now = time.monotonic()
            while self._recent and now - self._recent[0] >= 60.0:
                self._recent.popleft()
            if len(self._recent) < self._rpm:
                self._recent.append(now)
                return
            delay = 60.0 - (now - self._recent[0]) + 0.1
            if waited + delay > 90.0:
                raise QuotaExceeded(
                    "rate_limit",
                    f"Local rate limit of {self._rpm} requests/minute would need a "
                    f"{delay:.0f}s wait; giving up for this run.",
                )
            logger.info("waiting %.1fs for the local rate limiter", delay)
            await self._sleep(delay)
            waited += delay
            # The window has moved forward; dropping the oldest slot guarantees
            # progress even with an injected (non-advancing) clock in tests.
            if self._recent:
                self._recent.popleft()

    # -- public API --------------------------------------------------------
    async def reserve(self) -> int:
        """Reserve one LLM call. Returns the number used today including this one."""
        await self._wait_for_rpm()
        day = self._today()
        factory = await self._session_factory()

        if factory is None:
            self._roll_memory_day()
            if self._memory_calls >= self.effective_budget:
                raise QuotaExceeded(
                    "daily_budget",
                    f"Daily LLM budget of {self.effective_budget} calls is used up.",
                )
            self._memory_calls += 1
            return self._memory_calls

        async with factory() as session:
            result = await session.execute(
                _RESERVE_SQL, {"day": day, "budget": self.effective_budget}
            )
            row = result.first()
            await session.commit()

        if row is None:
            raise QuotaExceeded(
                "daily_budget",
                f"Daily LLM budget of {self.effective_budget} calls is used up.",
            )
        return int(row[0])

    async def record_tokens(self, *, tokens_in: int, tokens_out: int, runs: int = 0) -> None:
        day = self._today()
        factory = await self._session_factory()
        if factory is None:
            self._roll_memory_day()
            self._memory_tokens_in += max(0, tokens_in)
            self._memory_tokens_out += max(0, tokens_out)
            self._memory_runs += max(0, runs)
            return
        async with factory() as session:
            await session.execute(
                _RECORD_SQL,
                {
                    "day": day,
                    "tokens_in": max(0, tokens_in),
                    "tokens_out": max(0, tokens_out),
                    "runs": max(0, runs),
                },
            )
            await session.commit()

    async def snapshot(self) -> dict[str, Any]:
        day = self._today()
        factory = await self._session_factory()
        used = tokens_in = tokens_out = runs = 0
        if factory is None:
            self._roll_memory_day()
            used = self._memory_calls
            tokens_in = self._memory_tokens_in
            tokens_out = self._memory_tokens_out
            runs = self._memory_runs
        else:
            async with factory() as session:
                row = (await session.execute(_SNAPSHOT_SQL, {"day": day})).first()
            if row is not None:
                used = int(row[1])
                tokens_in, tokens_out, runs = int(row[2]), int(row[3]), int(row[4])

        budget = self.effective_budget
        return {
            "day": day.isoformat(),
            "llm_calls": used,
            "budget": None if self._budget <= 0 else budget,
            "remaining": None if self._budget <= 0 else max(0, budget - used),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "runs": runs,
            "rpm_limit": self._rpm,
        }
