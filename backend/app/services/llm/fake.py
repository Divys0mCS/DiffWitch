"""Deterministic provider used by the test suite, the CLI ``--provider fake``
mode, and local development without a Gemini key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.services.llm.base import LLMResult

Responder = Callable[[str, str], str]


def default_review_json(comment: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {
        "summary": "Fake provider review: the change looks reasonable overall.",
        "overall_risk": "low",
        "comments": [comment] if comment else [],
    }
    return json.dumps(payload)


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(
        self,
        responder: Responder | None = None,
        *,
        result_text: str | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._responder = responder
        self._result_text = result_text
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "prompt": prompt,
                "json_schema": json_schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            }
        )
        if self._raise is not None:
            raise self._raise
        if self._responder is not None:
            text = self._responder(system, prompt)
        elif self._result_text is not None:
            text = self._result_text
        else:
            text = default_review_json()
        return LLMResult(
            text=text,
            model=self.model,
            prompt_tokens=max(1, len(prompt) // 4),
            completion_tokens=max(1, len(text) // 4),
            latency_ms=1,
            finish_reason="STOP",
        )

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None
