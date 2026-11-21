from __future__ import annotations

import json

from app.config import Settings
from app.services.llm.base import LLMError, LLMQuotaError
from app.services.llm.fake import FakeProvider
from app.services.quota import QuotaGuard
from app.services.review import ReviewRequest, extract_json, review_diff
from app.services.steps import ListStepSink
from tests.fixtures import CLEAN_REVIEW, TWO_FILE_DIFF


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "daily_llm_budget": 10,
        "llm_rpm": 0,
        "max_inline_comments": 5,
        "llm_provider": "fake",
    }
    values.update(overrides)
    return Settings(**values)


def _request(diff: str = TWO_FILE_DIFF) -> ReviewRequest:
    return ReviewRequest(
        repo="Divys0mCS/DiffWitch",
        pr_number=42,
        pr_title="Add sys.exit",
        head_sha="a" * 40,
        diff=diff,
        base_ref="main",
    )


async def test_lite_review_happy_path():
    settings = _settings()
    provider = FakeProvider(result_text=json.dumps(CLEAN_REVIEW))
    sink = ListStepSink()

    outcome = await review_diff(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings), sink=sink
    )

    assert outcome.ok is True
    assert outcome.result is not None
    assert outcome.result.overall_risk.value == "low"
    assert len(outcome.result.comments) == 1
    assert outcome.result.comments[0].line == 5
    assert outcome.files_reviewed == 1
    assert len(provider.calls) == 1

    call = provider.calls[0]
    assert call["json_schema"] is not None
    assert "untrusted" in call["system"].lower()
    assert "app/main.py" in call["prompt"]
    assert "package-lock.json" in call["prompt"]  # mentioned only as filtered out
    assert "FILTERED OUT" in call["prompt"]

    kinds = [step.kind for step in sink.steps]
    assert "note" in kinds
    assert kinds.count("llm_call") == 2
    assert kinds[-1] == "verdict"


async def test_only_one_llm_call_in_lite_mode():
    settings = _settings()
    provider = FakeProvider(result_text=json.dumps(CLEAN_REVIEW))
    await review_diff(_request(), settings=settings, provider=provider, guard=QuotaGuard(settings))
    assert len(provider.calls) == 1


async def test_comment_on_missing_line_is_dropped():
    payload = {
        "summary": "mixed",
        "overall_risk": "medium",
        "comments": [
            {"file": "app/main.py", "line": 6, "message": "real", "side": "RIGHT"},
            {"file": "app/main.py", "line": 99, "message": "fake", "side": "RIGHT"},
        ],
    }
    settings = _settings()
    outcome = await review_diff(
        _request(),
        settings=settings,
        provider=FakeProvider(result_text=json.dumps(payload)),
        guard=QuotaGuard(settings),
    )
    assert outcome.result is not None
    assert [c.line for c in outcome.result.comments] == [6]
    assert len(outcome.dropped) == 1


async def test_no_reviewable_files_skips_the_llm():
    diff = "\n".join(
        [
            "diff --git a/package-lock.json b/package-lock.json",
            "--- a/package-lock.json",
            "+++ b/package-lock.json",
            "@@ -1,1 +1,1 @@",
            "-a",
            "+b",
        ]
    )
    settings = _settings()
    provider = FakeProvider(result_text=json.dumps(CLEAN_REVIEW))
    outcome = await review_diff(
        _request(diff), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )
    assert outcome.skipped_reason == "no_reviewable_files"
    assert outcome.result is not None
    assert outcome.result.comments == []
    assert provider.calls == []


async def test_budget_exhausted_skips_without_calling_llm():
    settings = _settings(daily_llm_budget=1)
    guard = QuotaGuard(settings)
    await guard.reserve()
    provider = FakeProvider(result_text=json.dumps(CLEAN_REVIEW))

    outcome = await review_diff(_request(), settings=settings, provider=provider, guard=guard)

    assert outcome.result is None
    assert outcome.skipped_reason == "quota: daily_budget"
    assert provider.calls == []


async def test_provider_quota_error_marks_skipped():
    settings = _settings()
    provider = FakeProvider(raise_exc=LLMQuotaError("daily quota gone"))
    outcome = await review_diff(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )
    assert outcome.skipped_reason == "quota: provider"
    assert outcome.error == "daily quota gone"


async def test_provider_error_is_reported_not_raised():
    settings = _settings()
    provider = FakeProvider(raise_exc=LLMError("503 unavailable"))
    outcome = await review_diff(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )
    assert outcome.result is None
    assert outcome.error == "503 unavailable"


async def test_invalid_json_is_reported():
    settings = _settings()
    provider = FakeProvider(result_text="Looks good to me!")
    outcome = await review_diff(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )
    assert outcome.result is None
    assert outcome.error is not None
    assert "invalid JSON" in outcome.error


async def test_unexpected_provider_exception_is_contained():
    settings = _settings()
    provider = FakeProvider(raise_exc=RuntimeError("kaboom"))
    outcome = await review_diff(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )
    assert outcome.result is None
    assert outcome.error is not None
    assert "RuntimeError" in outcome.error


async def test_extract_json_from_fenced_response():
    payload = '```json\n{"summary": "s", "overall_risk": "low", "comments": []}\n```'
    assert extract_json(payload)["summary"] == "s"


async def test_extract_json_with_leading_prose():
    payload = 'Here you go:\n{"summary": "s", "overall_risk": "high", "comments": []}\nDone.'
    assert extract_json(payload)["overall_risk"] == "high"


async def test_prompt_respects_max_comments_setting():
    settings = _settings(max_inline_comments=7)
    provider = FakeProvider(result_text=json.dumps(CLEAN_REVIEW))
    await review_diff(_request(), settings=settings, provider=provider, guard=QuotaGuard(settings))
    assert "7 comments" in provider.calls[0]["system"]
