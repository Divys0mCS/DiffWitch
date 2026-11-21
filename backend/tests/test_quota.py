from __future__ import annotations

from datetime import date

import pytest
from app import db
from app.config import Settings
from app.services.quota import QuotaExceeded, QuotaGuard


def _settings(**overrides) -> Settings:
    values = {"_env_file": None, "daily_llm_budget": 3, "llm_rpm": 0}
    values.update(overrides)
    return Settings(**values)


async def test_memory_budget_stops_after_limit():
    guard = QuotaGuard(_settings(daily_llm_budget=2))
    assert await guard.reserve() == 1
    assert await guard.reserve() == 2
    with pytest.raises(QuotaExceeded) as info:
        await guard.reserve()
    assert info.value.reason == "daily_budget"
    snapshot = await guard.snapshot()
    assert snapshot["llm_calls"] == 2
    assert snapshot["remaining"] == 0


async def test_zero_budget_means_unlimited():
    guard = QuotaGuard(_settings(daily_llm_budget=0))
    for _ in range(5):
        await guard.reserve()
    snapshot = await guard.snapshot()
    assert snapshot["budget"] is None
    assert snapshot["remaining"] is None


async def test_rpm_limiter_waits_instead_of_failing():
    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)

    guard = QuotaGuard(_settings(llm_rpm=2, daily_llm_budget=10), sleeper=sleeper)
    await guard.reserve()
    await guard.reserve()
    assert slept == []
    await guard.reserve()  # third call must wait for the window to slide
    assert len(slept) == 1
    assert slept[0] >= 55


async def test_memory_day_rolls_over(monkeypatch):
    current = {"day": date(2026, 1, 1)}
    guard = QuotaGuard(_settings(daily_llm_budget=1), today=lambda: current["day"])
    await guard.reserve()
    with pytest.raises(QuotaExceeded):
        await guard.reserve()
    current["day"] = date(2026, 1, 2)
    assert await guard.reserve() == 1


async def test_db_budget_is_atomic(sqlite_engine):
    guard = QuotaGuard(_settings(daily_llm_budget=2), db.get_sessionmaker())
    assert await guard.reserve() == 1
    assert await guard.reserve() == 2
    with pytest.raises(QuotaExceeded) as info:
        await guard.reserve()
    assert info.value.reason == "daily_budget"

    snapshot = await guard.snapshot()
    assert snapshot["llm_calls"] == 2
    assert snapshot["budget"] == 2
    assert snapshot["remaining"] == 0


async def test_db_records_tokens_and_runs(sqlite_engine):
    guard = QuotaGuard(_settings(daily_llm_budget=5), db.get_sessionmaker())
    await guard.reserve()
    await guard.record_tokens(tokens_in=100, tokens_out=40, runs=1)
    await guard.record_tokens(tokens_in=10, tokens_out=5, runs=1)
    snapshot = await guard.snapshot()
    assert snapshot["tokens_in"] == 110
    assert snapshot["tokens_out"] == 45
    assert snapshot["runs"] == 2


async def test_db_never_exceeds_budget_after_exhaustion(sqlite_engine):
    """The conditional upsert is the single source of truth for the budget."""
    guard = QuotaGuard(_settings(daily_llm_budget=3), db.get_sessionmaker())
    allowed = 0
    for _ in range(6):
        try:
            await guard.reserve()
            allowed += 1
        except QuotaExceeded:
            pass
    assert allowed == 3
    snapshot = await guard.snapshot()
    assert snapshot["llm_calls"] == 3
