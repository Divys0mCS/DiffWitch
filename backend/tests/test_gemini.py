from __future__ import annotations

import json

import httpx
import pytest
from app.config import Settings
from app.services.llm.base import LLMError, LLMQuotaError
from app.services.llm.gemini import GeminiProvider

REVIEW_TEXT = json.dumps({"summary": "ok", "overall_risk": "low", "comments": []})
OK_BODY = {
    "candidates": [
        {
            "content": {"parts": [{"text": REVIEW_TEXT}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 120,
        "candidatesTokenCount": 30,
        "totalTokenCount": 150,
    },
}


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "gemini_api_key": "test-key-not-real",
        "llm_model": "gemini-test-model",
        "llm_max_retries": 1,
        "llm_temperature": 0.1,
        "llm_max_output_tokens": 1234,
        "gemini_api_base": "https://example.test/v1beta",
    }
    values.update(overrides)
    return Settings(**values)


def _provider(handler, **overrides):
    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(_settings(**overrides), client=client, sleeper=sleeper)
    return provider, slept, client


async def test_successful_generation():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key_header"] = request.headers.get("x-goog-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK_BODY)

    provider, slept, client = _provider(handler)
    result = await provider.generate(system="sys", prompt="hi", json_schema={"type": "OBJECT"})
    await client.aclose()

    assert result.prompt_tokens == 120
    assert result.completion_tokens == 30
    assert result.finish_reason == "STOP"
    assert slept == []
    assert captured["key_header"] == "test-key-not-real"
    assert "test-key-not-real" not in captured["url"]
    body = captured["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseSchema"] == {"type": "OBJECT"}
    assert body["generationConfig"]["maxOutputTokens"] == 1234


async def test_429_is_retried_with_retry_after_hint():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "3"},
                json={"error": {"message": "slow down"}},
            )
        return httpx.Response(200, json=OK_BODY)

    provider, slept, client = _provider(handler, llm_max_retries=2)
    result = await provider.generate(system="s", prompt="p")
    await client.aclose()

    assert calls["count"] == 2
    assert result.attempts == 2
    assert slept and slept[0] >= 3


async def test_503_is_retried_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=OK_BODY)

    provider, _, client = _provider(handler, llm_max_retries=2)
    result = await provider.generate(system="s", prompt="p")
    await client.aclose()
    assert calls["count"] == 2
    assert result.text


async def test_exhausted_429_becomes_quota_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "message": "Quota exceeded for quota metric",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )

    provider, slept, client = _provider(handler, llm_max_retries=1)
    with pytest.raises(LLMQuotaError) as info:
        await provider.generate(system="s", prompt="p")
    await client.aclose()
    assert "quota" in info.value.message.lower()
    assert len(slept) == 1


async def test_400_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            400,
            json={"error": {"message": "API key not valid. Please pass a valid API key."}},
        )

    provider, _, client = _provider(handler, llm_max_retries=3)
    with pytest.raises(LLMError) as info:
        await provider.generate(system="s", prompt="p")
    await client.aclose()
    assert calls["count"] == 1
    assert info.value.retryable is False
    assert "GEMINI_API_KEY" in info.value.message


async def test_404_mentions_model_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "models/old-model is not found"}})

    provider, _, client = _provider(handler)
    with pytest.raises(LLMError) as info:
        await provider.generate(system="s", prompt="p")
    await client.aclose()
    assert "LLM_MODEL" in info.value.message


async def test_empty_candidates_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
        )

    provider, _, client = _provider(handler)
    with pytest.raises(LLMError) as info:
        await provider.generate(system="s", prompt="p")
    await client.aclose()
    assert "SAFETY" in info.value.message


async def test_transport_error_is_retried_then_fails_cleanly():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("boom", request=request)

    provider, slept, client = _provider(handler, llm_max_retries=1)
    with pytest.raises(LLMError):
        await provider.generate(system="s", prompt="p")
    await client.aclose()
    assert calls["count"] == 2
    assert len(slept) == 1


async def test_missing_key_raises_config_error():
    from app.services.llm.base import LLMConfigError

    with pytest.raises(LLMConfigError):
        GeminiProvider(_settings(gemini_api_key=""))


async def test_missing_model_raises_config_error():
    from app.services.llm.base import LLMConfigError

    with pytest.raises(LLMConfigError) as info:
        GeminiProvider(_settings(llm_model=""))
    assert "LLM_MODEL" in info.value.message
