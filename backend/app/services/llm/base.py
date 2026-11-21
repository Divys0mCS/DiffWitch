"""Provider-agnostic LLM interface.

Swapping providers means implementing :class:`LLMProvider` and returning it from
``app.services.llm.build_provider``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str | None = None
    attempts: int = 1


class LLMError(RuntimeError):
    """Base class for provider failures with a dashboard-safe message."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after


class LLMConfigError(LLMError):
    """Missing or invalid configuration (no key, unknown model)."""


class LLMQuotaError(LLMError):
    """The provider says we are out of quota; do not retry."""


class LLMResponseError(LLMError):
    """The provider answered, but the payload was unusable."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...

    async def aclose(self) -> None: ...
