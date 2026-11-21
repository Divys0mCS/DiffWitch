"""Step emission.

A "step" is one observable beat of the agent's reasoning.  Phase 2 renders them
to the console/test list; Phase 3 persists them and Phase 4 streams them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Step:
    kind: str  # llm_call | tool_call | tool_result | note | verdict | error
    title: str
    detail: dict[str, Any] | None = None
    tokens: int | None = None
    duration_ms: int | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


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
    """Discards steps (useful in unit tests)."""

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
        step = Step(kind=kind, title=title, detail=detail, tokens=tokens, duration_ms=duration_ms)
        self.steps.append(step)
        return step
