from __future__ import annotations

import json

from app.config import Settings
from app.services.agent import agent_schema, agent_system_prompt, run_agent
from app.services.llm.fake import FakeProvider
from app.services.quota import QuotaGuard
from app.services.review import ReviewRequest
from app.services.steps import ListStepSink
from tests.fixtures import CLEAN_REVIEW, TWO_FILE_DIFF


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "daily_llm_budget": 10,
        "llm_rpm": 0,
        "max_agent_steps": 3,
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
        mode="agent",
    )


def test_agent_schema_requires_thought_and_tool():
    schema = agent_schema()
    assert schema["required"] == ["thought", "tool"]
    assert "post_review" in schema["properties"]["tool"]["enum"]
    assert schema["properties"]["review"]["required"] == [
        "summary",
        "overall_risk",
        "comments",
    ]


def test_agent_system_prompt_lists_tools():
    text = agent_system_prompt(4)
    assert "get_pr_metadata" in text
    assert "read_file" in text
    assert "4 comments" in text


async def test_agent_calls_a_tool_then_posts_review():
    def responder(system: str, prompt: str) -> str:
        if "TOOL RESULT" in prompt:
            return json.dumps(
                {"thought": "I have context now", "tool": "post_review", "review": CLEAN_REVIEW}
            )
        return json.dumps(
            {
                "thought": "I need the surrounding file",
                "tool": "read_file",
                "args": {"path": "app/main.py", "start_line": 1, "end_line": 20},
            }
        )

    settings = _settings()
    provider = FakeProvider(responder=responder)
    sink = ListStepSink()

    outcome = await run_agent(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings), sink=sink
    )

    assert outcome.ok is True
    assert outcome.steps_used == 2
    assert outcome.result is not None
    assert outcome.result.comments[0].line == 5
    assert [call["tool"] for call in outcome.tool_calls] == ["read_file"]
    assert len(provider.calls) == 2
    assert provider.calls[1]["json_schema"] is not None

    kinds = [step.kind for step in sink.steps]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "verdict"


async def test_agent_without_reader_still_finishes():
    def responder(system: str, prompt: str) -> str:
        if "TOOL RESULT" in prompt:
            return json.dumps({"thought": "ok", "tool": "post_review", "review": CLEAN_REVIEW})
        return json.dumps({"thought": "check", "tool": "get_pr_metadata", "args": {}})

    settings = _settings()
    outcome = await run_agent(
        _request(),
        settings=settings,
        provider=FakeProvider(responder=responder),
        guard=QuotaGuard(settings),
    )
    assert outcome.ok is True
    assert outcome.tool_calls[0]["tool"] == "get_pr_metadata"


async def test_agent_reports_exhausted_steps():
    def responder(system: str, prompt: str) -> str:
        return json.dumps({"thought": "more", "tool": "list_changed_files", "args": {}})

    settings = _settings(max_agent_steps=2)
    provider = FakeProvider(responder=responder)
    outcome = await run_agent(
        _request(), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )

    assert outcome.result is None
    assert outcome.steps_used == 2
    assert outcome.error is not None
    assert "without posting a review" in outcome.error
    assert len(provider.calls) == 2


async def test_single_step_agent_must_finish_immediately():
    def responder(system: str, prompt: str) -> str:
        return json.dumps({"thought": "x", "tool": "get_diff", "args": {}})

    settings = _settings(max_agent_steps=1)
    outcome = await run_agent(
        _request(),
        settings=settings,
        provider=FakeProvider(responder=responder),
        guard=QuotaGuard(settings),
    )
    assert outcome.result is None
    assert outcome.error is not None


async def test_agent_stops_when_budget_is_gone():
    settings = _settings(daily_llm_budget=1)
    guard = QuotaGuard(settings)
    await guard.reserve()

    def responder(system: str, prompt: str) -> str:
        return json.dumps({"thought": "x", "tool": "get_pr_metadata", "args": {}})

    outcome = await run_agent(
        _request(), settings=settings, provider=FakeProvider(responder=responder), guard=guard
    )
    assert outcome.skipped_reason == "quota: daily_budget"
    assert outcome.result is None


async def test_agent_rejects_invalid_final_review():
    def responder(system: str, prompt: str) -> str:
        return json.dumps({"thought": "done", "tool": "post_review", "review": "nope"})

    settings = _settings(max_agent_steps=1)
    outcome = await run_agent(
        _request(),
        settings=settings,
        provider=FakeProvider(responder=responder),
        guard=QuotaGuard(settings),
    )
    assert outcome.result is None
    assert outcome.error is not None
    assert "invalid" in outcome.error.lower()


async def test_agent_drops_unanchored_comments():
    review = {
        "summary": "mixed",
        "overall_risk": "high",
        "comments": [
            {"file": "app/main.py", "line": 6, "message": "real", "side": "RIGHT"},
            {"file": "app/main.py", "line": 999, "message": "invented", "side": "RIGHT"},
        ],
    }

    def responder(system: str, prompt: str) -> str:
        return json.dumps({"thought": "done", "tool": "post_review", "review": review})

    settings = _settings(max_agent_steps=1)
    outcome = await run_agent(
        _request(),
        settings=settings,
        provider=FakeProvider(responder=responder),
        guard=QuotaGuard(settings),
    )
    assert outcome.result is not None
    assert [comment.line for comment in outcome.result.comments] == [6]
    assert len(outcome.dropped) == 1


async def test_agent_skips_when_nothing_reviewable():
    diff = "\n".join(
        [
            "diff --git a/yarn.lock b/yarn.lock",
            "--- a/yarn.lock",
            "+++ b/yarn.lock",
            "@@ -1,1 +1,1 @@",
            "-a",
            "+b",
        ]
    )
    settings = _settings()
    provider = FakeProvider()
    outcome = await run_agent(
        _request(diff), settings=settings, provider=provider, guard=QuotaGuard(settings)
    )
    assert outcome.skipped_reason == "no_reviewable_files"
    assert provider.calls == []


async def test_agent_surfaces_provider_failure():
    from app.services.llm.base import LLMError

    settings = _settings()
    outcome = await run_agent(
        _request(),
        settings=settings,
        provider=FakeProvider(raise_exc=LLMError("boom")),
        guard=QuotaGuard(settings),
    )
    assert outcome.error == "boom"
    assert outcome.result is None
