"""LITE review pipeline: diff -> filtered prompt -> one LLM call -> validated JSON.

AGENT mode (Phase 3) reuses everything here; only the "obtain the review"
step differs.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.schemas import (
    REVIEW_RESPONSE_SCHEMA,
    ReviewComment,
    ReviewResult,
    parse_review_payload,
)
from app.services.diff import PreparedDiff, estimate_tokens, prepare_diff
from app.services.llm.base import LLMError, LLMProvider, LLMQuotaError, LLMResult
from app.services.prompts import SYSTEM_PROMPT, build_lite_prompt
from app.services.quota import QuotaExceeded, QuotaGuard
from app.services.steps import NullStepSink, StepSink

logger = logging.getLogger(__name__)

_MAX_COMMENTS_PLACEHOLDER = "%%MAX_COMMENTS%%"


def system_prompt(max_comments: int) -> str:
    return SYSTEM_PROMPT.replace(_MAX_COMMENTS_PLACEHOLDER, str(max_comments))


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response (fences tolerated)."""
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = [line for line in candidate.splitlines() if not line.strip().startswith("```")]
        candidate = "\n".join(lines).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in the model response")
    payload = json.loads(candidate[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model returned JSON that is not an object")
    return payload


@dataclass(slots=True)
class ReviewRequest:
    repo: str
    pr_number: int
    pr_title: str
    head_sha: str
    diff: str
    base_ref: str = ""
    mode: str = "lite"


@dataclass(slots=True)
class ReviewOutcome:
    result: ReviewResult | None
    prepared: PreparedDiff | None = None
    dropped: list[ReviewComment] = field(default_factory=list)
    skipped_reason: str | None = None
    error: str | None = None
    llm_result: LLMResult | None = None
    prompt_tokens_estimate: int = 0
    files_reviewed: int = 0

    @property
    def ok(self) -> bool:
        return self.result is not None and self.error is None


async def review_diff(
    request: ReviewRequest,
    *,
    settings: Settings,
    provider: LLMProvider,
    guard: QuotaGuard,
    sink: StepSink | None = None,
) -> ReviewOutcome:
    """Run one LITE review. Never raises for expected failures."""
    sink = sink or NullStepSink()
    max_comments = max(1, settings.max_inline_comments)

    prepared = prepare_diff(
        request.diff,
        ignore_globs=settings.ignore_glob_list,
        max_file_chars=settings.max_file_diff_chars,
        max_total_chars=settings.max_prompt_chars,
        max_files=settings.max_files_per_review,
    )

    await sink.emit(
        "note",
        f"Filtered diff: {len(prepared.files)} file(s) kept, {len(prepared.skipped)} filtered",
        {
            "kept": prepared.included_paths,
            "filtered": [
                {"path": path, "reason": reason} for path, reason in prepared.skipped[:20]
            ],
            "omitted": prepared.omitted[:20],
            "total_changed_files": prepared.total_files_seen,
        },
    )

    if not prepared.files:
        await sink.emit(
            "verdict", "Nothing reviewable in this diff", {"risk": "low", "comments": 0}
        )
        return ReviewOutcome(
            result=ReviewResult(
                summary=(
                    "DiffWitch found no reviewable changes: every changed file was a "
                    "lockfile, generated/binary asset or vendored code."
                ),
                overall_risk="low",
                comments=[],
            ),
            prepared=prepared,
            skipped_reason="no_reviewable_files",
        )

    prompt = build_lite_prompt(
        prepared,
        repo=request.repo,
        pr_number=request.pr_number,
        pr_title=request.pr_title,
        base_ref=request.base_ref,
        head_sha=request.head_sha,
        max_comments=max_comments,
    )
    system = system_prompt(max_comments)
    estimate = estimate_tokens(system) + estimate_tokens(prompt)

    await sink.emit(
        "llm_call",
        f"Reviewing {len(prepared.files)} file(s) in one call ({provider.name})",
        {
            "provider": provider.name,
            "model": provider.model,
            "mode": "lite",
            "files": prepared.included_paths,
            "prompt_chars": len(prompt),
            "estimated_tokens": estimate,
        },
        tokens=estimate,
    )

    try:
        await guard.reserve()
    except QuotaExceeded as exc:
        await sink.emit("note", "Skipped: LLM budget", {"reason": exc.reason, "detail": exc.detail})
        return ReviewOutcome(
            result=None,
            prepared=prepared,
            skipped_reason=f"quota: {exc.reason}",
            error=exc.detail,
        )

    started = time.perf_counter()
    try:
        llm_result = await provider.generate(
            system=system,
            prompt=prompt,
            json_schema=REVIEW_RESPONSE_SCHEMA,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    except LLMQuotaError as exc:
        await sink.emit("error", "LLM quota exhausted", {"message": exc.message})
        return ReviewOutcome(
            result=None,
            prepared=prepared,
            skipped_reason="quota: provider",
            error=exc.message,
        )
    except LLMError as exc:
        await sink.emit(
            "error",
            "LLM call failed",
            {"message": exc.message, "status_code": exc.status_code, "retryable": exc.retryable},
        )
        return ReviewOutcome(result=None, prepared=prepared, error=exc.message)
    except Exception as exc:  # noqa: BLE001 - never crash the worker
        logger.exception("unexpected provider failure")
        await sink.emit("error", "LLM call failed", {"message": type(exc).__name__})
        return ReviewOutcome(
            result=None, prepared=prepared, error=f"Unexpected provider error: {type(exc).__name__}"
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await guard.record_tokens(
        tokens_in=llm_result.prompt_tokens,
        tokens_out=llm_result.completion_tokens,
        runs=1,
    )
    await sink.emit(
        "llm_call",
        f"Response received ({llm_result.completion_tokens} tokens out)",
        {
            "model": llm_result.model,
            "prompt_tokens": llm_result.prompt_tokens,
            "completion_tokens": llm_result.completion_tokens,
            "finish_reason": llm_result.finish_reason,
            "attempts": llm_result.attempts,
        },
        tokens=llm_result.completion_tokens,
        duration_ms=elapsed_ms,
    )

    try:
        payload = extract_json(llm_result.text)
        result = parse_review_payload(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        await sink.emit("error", "Model returned unusable JSON", {"message": str(exc)})
        return ReviewOutcome(
            result=None,
            prepared=prepared,
            error=f"The model returned invalid JSON: {exc}",
            llm_result=llm_result,
        )

    result, dropped = result.clamp_to_diff(
        prepared.right_anchors, prepared.left_anchors, max_comments=settings.max_inline_comments
    )

    if dropped:
        await sink.emit(
            "note",
            f"Dropped {len(dropped)} comment(s) that did not anchor to a shown line",
            {"dropped": [f"{c.file}:{c.line}" for c in dropped[:20]]},
        )

    await sink.emit(
        "verdict",
        f"Verdict: {result.overall_risk.value} risk, {len(result.comments)} comment(s)",
        {
            "risk": result.overall_risk.value,
            "comments": len(result.comments),
            "dropped": len(dropped),
            "summary": result.summary,
        },
    )

    return ReviewOutcome(
        result=result,
        prepared=prepared,
        dropped=dropped,
        llm_result=llm_result,
        prompt_tokens_estimate=estimate,
        files_reviewed=len(prepared.files),
    )
