"""Step emission.

A "step" is one observable beat of the agent's reasoning.  ``DbStepSink``
persists it to ``run_steps`` and publishes it to the live bus so the dashboard
timeline can render it while the run is still going.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import RunStep
from app.services.events import EventBus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Step:
    kind: str  # llm_call | tool_call | tool_result | note | verdict | error
    title: str
    detail: dict[str, Any] | None = None
    tokens: int | None = None
    duration_ms: int | None = None
    seq: int | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_event(self, run_id: str) -> dict[str, Any]:
        return {
            "type": "step",
            "run_id": run_id,
            "seq": self.seq,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "tokens": self.tokens,
            "duration_ms": self.duration_ms,
            "at": self.at.isoformat(),
        }


@runtime_checkable
class StepSink(Protocol):
    async def emit(
        self,
        kind: str,
        title: str,
        detail: dict[str, Any] | None = None,
        *,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> Step: ...


class NullStepSink:
    async def emit(
        self,
        kind: str,
        title: str,
        detail: dict[str, Any] | None = None,
        *,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> Step:
        return Step(kind=kind, title=title, detail=detail, tokens=tokens, duration_ms=duration_ms)


class ListStepSink:
    """Keeps steps in memory, for tests and the CLI."""

    def __init__(self) -> None:
        self.steps: list[Step] = []

    async def emit(
        self,
        kind: str,
        title: str,
        detail: dict[str, Any] | None = None,
        *,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> Step:
        step = Step(
            kind=kind,
            title=title,
            detail=detail,
            tokens=tokens,
            duration_ms=duration_ms,
            seq=len(self.steps) + 1,
        )
        self.steps.append(step)
        return step


class DbStepSink:
    """Persist to ``run_steps`` and fan out to SSE subscribers.

    A database failure here must never abort a review, so errors are logged and
    swallowed; the review itself is the valuable output.
    """

    def __init__(
        self,
        run_id: UUID,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        bus: EventBus | None = None,
        start_seq: int = 0,
        on_step: Callable[[Step], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self._session_factory = session_factory
        self._bus = bus
        self._seq = start_seq
        self._on_step = on_step

    async def emit(
        self,
        kind: str,
        title: str,
        detail: dict[str, Any] | None = None,
        *,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> Step:
        self._seq += 1
        step = Step(
            kind=kind,
            title=title[:256],
            detail=detail,
            tokens=tokens,
            duration_ms=duration_ms,
            seq=self._seq,
        )
        try:
            async with self._session_factory() as session:
                session.add(
                    RunStep(
                        run_id=self.run_id,
                        seq=step.seq,
                        kind=step.kind,
                        title=step.title,
                        detail=step.detail,
                        tokens=step.tokens,
                        duration_ms=step.duration_ms,
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - never fail a review over telemetry
            logger.exception("could not persist run step %s", step.seq)

        if self._bus is not None:
            try:
                await self._bus.publish(str(self.run_id), step.as_event(str(self.run_id)))
            except Exception:  # noqa: BLE001
                logger.debug("could not publish step %s", step.seq)
        if self._on_step is not None:
            with contextlib.suppress(Exception):
                self._on_step(step)
        return step
