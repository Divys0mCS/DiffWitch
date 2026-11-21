from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from app import db
from app.config import Settings
from app.models import Job, ReviewRun, RunStep
from app.services import worker as worker_module
from app.services.github import GitHubError
from app.services.llm.fake import FakeProvider
from app.services.worker import Worker
from tests.fixtures import CLEAN_REVIEW, TWO_FILE_DIFF, seed_run


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "llm_provider": "fake",
        "daily_llm_budget": 5,
        "llm_rpm": 0,
        "max_inline_comments": 5,
        "worker_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


class FakeClient:
    def __init__(self, *, fail: Exception | None = None, diff: str = TWO_FILE_DIFF) -> None:
        self.fail = fail
        self.diff = diff
        self.reviews: list[dict[str, Any]] = []
        self.reads: list[tuple[str, str]] = []

    async def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        return {
            "number": number,
            "title": "Bound the loop",
            "changed_files": 1,
            "additions": 3,
            "deletions": 1,
            "diff_url": "https://github.com/acme/app/pull/5.diff",
            "head": {"sha": "a" * 40},
            "base": {"ref": "main", "sha": "b" * 40},
        }

    async def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        return self.diff

    async def list_review_comments(self, owner, repo, number) -> list[dict[str, Any]]:
        return []

    async def create_review(self, owner, repo, number, *, body, comments, commit_id=None):
        self.reviews.append({"body": body, "comments": comments, "commit_id": commit_id})
        return {"id": 4242, "html_url": "https://github.com/acme/app/pull/5#review-4242"}

    async def read_file(self, owner, repo, path, ref, **kwargs) -> str:
        self.reads.append((path, ref))
        return "line1\nline2\nline3"


class FakeGitHubApp:
    client: FakeClient | None = None

    def __init__(self, settings, **kwargs) -> None:
        self.settings = settings

    async def client_for_installation(self, installation_id: int) -> FakeClient:
        assert installation_id == 1
        assert FakeGitHubApp.client is not None
        return FakeGitHubApp.client

    async def aclose(self) -> None:
        return None


@pytest.fixture
def patched(monkeypatch, sqlite_engine):
    client = FakeClient()
    FakeGitHubApp.client = client
    monkeypatch.setattr(worker_module, "GitHubApp", FakeGitHubApp)
    monkeypatch.setattr(
        worker_module,
        "build_provider",
        lambda settings, **kwargs: FakeProvider(result_text=json.dumps(CLEAN_REVIEW)),
    )
    return client


async def _queued_run(**kwargs):
    run_id, job_id = await seed_run(status="queued", with_steps=False, **kwargs)
    async with db.session_scope() as session:
        job = await session.get(Job, job_id)
        job.status = "running"
        job.attempts = 1
        job.locked_at = sa.func.now()
    return run_id, job_id


async def test_claim_one_marks_the_job_running(sqlite_engine):
    _, job_id = await seed_run(status="queued", with_steps=False)
    worker = Worker(_settings())
    claimed = await worker.claim_one()
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.attempts == 1

    async with db.session_scope() as session:
        job = await session.get(Job, job_id)
        assert job.status == "running"
        assert job.locked_at is not None


async def test_claim_one_returns_none_when_empty(sqlite_engine):
    worker = Worker(_settings())
    assert await worker.claim_one() is None


async def test_requeue_stuck_jobs(sqlite_engine):
    from app.services.runs import requeue_stuck_jobs

    run_id, job_id = await seed_run(status="running", with_steps=False)
    async with db.session_scope() as session:
        job = await session.get(Job, job_id)
        job.status = "running"
        job.locked_at = sa.func.now() - timedelta(hours=2)
        run = await session.get(ReviewRun, run_id)
        run.status = "running"
        run.started_at = sa.func.now() - timedelta(hours=2)

    async with db.session_scope() as session:
        count = await requeue_stuck_jobs(session, older_than_seconds=900)
    assert count == 1

    async with db.session_scope() as session:
        job = await session.get(Job, job_id)
        assert job.status == "queued"
        assert job.locked_at is None


async def test_process_completes_a_lite_review(sqlite_engine, patched):
    run_id, job_id = await _queued_run()
    worker = Worker(_settings())
    await worker.process(SimpleNamespace(id=job_id, run_id=run_id, attempts=1, max_attempts=3))

    async with db.session_scope() as session:
        run = await session.get(ReviewRun, run_id)
        assert run.status == "completed"
        assert run.risk == "low"
        assert run.comments_posted == 1
        assert run.review_id == 4242
        assert run.review_url.endswith("#review-4242")
        assert run.llm_calls == 1
        assert run.finished_at is not None

        steps = (
            (
                await session.execute(
                    sa.select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)
                )
            )
            .scalars()
            .all()
        )
        assert [step.seq for step in steps] == list(range(1, len(steps) + 1))
        assert "verdict" in [step.kind for step in steps]

        job = await session.get(Job, job_id)
        assert job.status == "done"

    assert len(patched.reviews) == 1
    assert patched.reviews[0]["comments"][0]["path"] == "app/main.py"
    assert patched.reviews[0]["commit_id"] == "a" * 40


async def test_process_agent_mode_works(sqlite_engine, monkeypatch, patched):
    # In agent mode the fake must answer with a tool call + review, not LITE JSON.
    monkeypatch.setattr(worker_module, "build_provider", lambda settings, **kwargs: FakeProvider())
    run_id, job_id = await _queued_run(mode="agent")
    worker = Worker(_settings(max_agent_steps=2))
    await worker.process(SimpleNamespace(id=job_id, run_id=run_id, attempts=1, max_attempts=3))
    async with db.session_scope() as session:
        run = await session.get(ReviewRun, run_id)
        assert run.status == "completed"
        assert run.llm_calls == 1


async def test_process_marks_failed_when_github_fails(sqlite_engine, monkeypatch):
    FakeGitHubApp.client = FakeClient(
        fail=GitHubError("GitHub exploded", status_code=500, retryable=True)
    )
    monkeypatch.setattr(worker_module, "GitHubApp", FakeGitHubApp)
    monkeypatch.setattr(
        worker_module,
        "build_provider",
        lambda settings, **kwargs: FakeProvider(result_text=json.dumps(CLEAN_REVIEW)),
    )
    run_id, job_id = await _queued_run()
    worker = Worker(_settings())
    await worker.process(SimpleNamespace(id=job_id, run_id=run_id, attempts=1, max_attempts=3))

    async with db.session_scope() as session:
        run = await session.get(ReviewRun, run_id)
        assert run.status == "failed"
        assert "exploded" in (run.error or "")
        job = await session.get(Job, job_id)
        assert job.status == "queued"  # retryable, attempts remain
        assert "exploded" in (job.last_error or "")


async def test_process_fails_without_credentials(sqlite_engine, monkeypatch):
    def boom(settings, **kwargs):
        from app.services.llm.base import LLMConfigError

        raise LLMConfigError("GEMINI_API_KEY is not set")

    monkeypatch.setattr(worker_module, "build_provider", boom)
    monkeypatch.setattr(worker_module, "GitHubApp", FakeGitHubApp)
    FakeGitHubApp.client = FakeClient()
    run_id, job_id = await _queued_run()
    worker = Worker(_settings())
    await worker.process(SimpleNamespace(id=job_id, run_id=run_id, attempts=1, max_attempts=3))

    async with db.session_scope() as session:
        run = await session.get(ReviewRun, run_id)
        assert run.status == "failed"
        assert "GEMINI_API_KEY" in (run.error or "")
        job = await session.get(Job, job_id)
        assert job.status == "failed"


async def test_process_ignores_an_already_finished_run(sqlite_engine, patched):
    run_id, job_id = await seed_run(status="completed")
    worker = Worker(_settings())
    await worker.process(SimpleNamespace(id=job_id, run_id=run_id, attempts=1, max_attempts=3))
    assert patched.reviews == []
