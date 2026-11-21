"""Provider factory."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.llm.base import (
    LLMConfigError,
    LLMError,
    LLMProvider,
    LLMQuotaError,
    LLMResponseError,
    LLMResult,
)
from app.services.llm.fake import FakeProvider
from app.services.llm.gemini import GeminiProvider

__all__ = [
    "FakeProvider",
    "GeminiProvider",
    "LLMConfigError",
    "LLMError",
    "LLMProvider",
    "LLMQuotaError",
    "LLMResponseError",
    "LLMResult",
    "build_provider",
]


def build_provider(settings: Settings, **kwargs: Any) -> LLMProvider:
    """Instantiate the configured provider.

    Raises ``LLMConfigError`` with an actionable message when configuration is
    incomplete, so the dashboard can show the reason instead of a stack trace.
    """
    if settings.llm_provider == "fake":
        return FakeProvider()
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings, **kwargs)
    raise LLMConfigError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'")
