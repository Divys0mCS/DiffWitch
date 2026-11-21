"""Gemini implementation of :class:`LLMProvider` (REST, httpx).

Design notes for the free tier:
* the API key travels in a header, never a query string, so it cannot leak into
  logs or tracebacks;
* 429/503 are retried with exponential backoff + jitter, honouring the server's
  retry hint (``Retry-After`` or ``error.details[].retryDelay``);
* 400/403/404 are not retried - they mean a wrong key or a model id that has
  been shut down, and the dashboard must say so plainly.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import Settings
from app.services.llm.base import (
    LLMConfigError,
    LLMError,
    LLMQuotaError,
    LLMResponseError,
    LLMResult,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_BACKOFF_SECONDS = 60.0
_RETRY_DELAY_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)s")


def _retry_hint(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        delay = detail.get("retryDelay")
        if isinstance(delay, str):
            match = _RETRY_DELAY_RE.match(delay)
            if match:
                return float(match.group("value"))
    return None


def _error_message(response: httpx.Response, model: str) -> str:
    """A human, secret-free explanation of a failed call."""
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raw = str(error.get("message") or "")
        status = str(error.get("status") or "")
    except ValueError:
        raw, status = response.text[:300], ""

    lowered = raw.lower()
    if response.status_code in {401, 403}:
        return "Gemini rejected the API key (check GEMINI_API_KEY)."
    if response.status_code == 404:
        return (
            f"Model '{model}' was not found for this API key. "
            "It may have been shut down; copy the current id from AI Studio into LLM_MODEL."
        )
    if response.status_code == 400 and "api key" in lowered:
        return "Gemini rejected the API key (check GEMINI_API_KEY)."
    if response.status_code == 429:
        if "quota" in lowered or "exceeded" in lowered:
            return "Gemini quota exhausted for this model (free-tier daily or per-minute limit)."
        return "Gemini rate limit hit (429)."
    if raw:
        return f"Gemini error {response.status_code} {status}: {raw[:240]}"
    return f"Gemini returned HTTP {response.status_code}."


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not settings.gemini_api_key:
            raise LLMConfigError("GEMINI_API_KEY is not set")
        if not settings.llm_model:
            raise LLMConfigError(
                "LLM_MODEL is not set: copy the exact model id from AI Studio into LLM_MODEL"
            )
        self.model = settings.llm_model
        self._api_key = settings.gemini_api_key
        self._base = settings.gemini_api_base.rstrip("/")
        self._timeout = settings.llm_timeout_seconds
        self._max_retries = max(0, settings.llm_max_retries)
        self._default_temperature = settings.llm_temperature
        self._default_max_tokens = settings.llm_max_output_tokens
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._sleep = sleeper or asyncio.sleep

    def _url(self) -> str:
        return f"{self._base}/models/{self.model}:generateContent"

    def _body(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None,
        max_output_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        generation: dict[str, Any] = {
            "temperature": self._default_temperature if temperature is None else temperature,
            "maxOutputTokens": (
                self._default_max_tokens if max_output_tokens is None else max_output_tokens
            ),
        }
        if json_schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = json_schema
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> tuple[str, str | None]:
        candidates = payload.get("candidates") or []
        if not candidates:
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            raise LLMResponseError(
                f"Gemini returned no candidates (blockReason={blocked or 'unknown'}).",
                retryable=False,
            )
        candidate = candidates[0] or {}
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        finish = candidate.get("finishReason")
        if not text.strip():
            raise LLMResponseError(
                f"Gemini returned an empty response (finishReason={finish or 'unknown'})."
            )
        return text, finish

    @staticmethod
    def _usage(payload: dict[str, Any]) -> tuple[int, int]:
        usage = payload.get("usageMetadata") or {}
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        completion_tokens = int(usage.get("candidatesTokenCount") or 0)
        total = int(usage.get("totalTokenCount") or 0)
        if completion_tokens == 0 and total > prompt_tokens:
            completion_tokens = total - prompt_tokens
        return prompt_tokens, completion_tokens

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        body = self._body(
            system=system,
            prompt=prompt,
            json_schema=json_schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

        last_error: LLMError | None = None
        started = time.perf_counter()

        for attempt in range(1, self._max_retries + 2):
            try:
                response = await self._client.post(self._url(), json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = LLMError(
                    f"Could not reach Gemini ({type(exc).__name__}).", retryable=True
                )
                logger.warning(
                    "gemini transport error on attempt %s: %s", attempt, type(exc).__name__
                )
            else:
                if response.status_code == 200:
                    payload = response.json()
                    text, finish = self._extract_text(payload)
                    prompt_tokens, completion_tokens = self._usage(payload)
                    return LLMResult(
                        text=text,
                        model=self.model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        finish_reason=finish,
                        attempts=attempt,
                    )

                hint = _retry_hint(response)
                message = _error_message(response, self.model)
                retryable = response.status_code in _RETRYABLE_STATUS
                last_error = LLMError(
                    message,
                    retryable=retryable,
                    status_code=response.status_code,
                    retry_after=hint,
                )
                if not retryable:
                    raise last_error
                logger.warning(
                    "gemini %s on attempt %s/%s (retry_after=%s)",
                    response.status_code,
                    attempt,
                    self._max_retries + 1,
                    hint,
                )

            if attempt >= self._max_retries + 1:
                break

            backoff = min(_MAX_BACKOFF_SECONDS, 2.0 ** (attempt - 1))
            backoff *= 0.5 + random.random()  # noqa: S311 - jitter, not crypto
            if last_error is not None and last_error.retry_after is not None:
                backoff = min(_MAX_BACKOFF_SECONDS, max(backoff, last_error.retry_after))
            logger.info("retrying gemini in %.1fs (attempt %s)", backoff, attempt + 1)
            await self._sleep(backoff)

        if last_error is not None and last_error.status_code == 429:
            # Out of retries on a rate limit: this is a quota problem, not a bug.
            raise LLMQuotaError(
                last_error.message,
                status_code=429,
                retry_after=last_error.retry_after,
            )
        raise last_error or LLMError("Gemini call failed for an unknown reason.")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def close_provider(provider: Any) -> None:
    closer = getattr(provider, "aclose", None)
    if closer is not None:
        await closer()
