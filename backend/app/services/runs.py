"""Persistence helpers for installations, repositories, PRs, runs and jobs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Installation,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    RunStep,
    WebhookDelivery,
)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")


def utcnow() -> datetime:
    return datetime.now(UTC)


async def upsert_installation(session: AsyncSession, data: dict[str, Any]) -> Installation:
    installation_id = int(data["id"])
    installation = await session.get(Installation, installation_id)
    account = data.get("account") or {}
    if installation is None:
        installation = Installation(
            id=installation_id,
            account_login=str(account.get("login") or "unknown"),
            account_type=str(account.get("type") or "User"),
            suspended=bool(data.get("suspended_at")),
        )
        session.add(installation)
    else:
        installation.account_login = str(account.get("login") or installation.account_login)
        installation.account_type = str(account.get("type") or installation.account_type)
        installation.suspended = bool(data.get("suspended_at"))
    await session.flush()
    return installation


async def upsert_repository(
    session: AsyncSession, installation_id: int, data: dict[str, Any]
) -> Repository:
    repo_id = int(data["id"])
    repository = await session.get(Repository, repo_id)
    full_name = str(data.get("full_name") or "")
    owner, _, name = full_name.partition("/")
    if repository is None:
        repository = Repository(
            id=repo_id,
            installation_id=installation_id,
            full_name=full_name,
            owner=owner,
            name=name or full_name,
            private=bool(data.get("private")),
            default_branch=str(data.get("default_branch") or "main"),
        )
        session.add(repository)
    else:
        repository.installation_id = installation_id
        repository.full_name = full_name or repository.full_name
        repository.owner = owner or repository.owner
        repository.name = name or repository.name
        repository.private = bool(data.get("private", repository.private))
        repository.default_branch = str(data.get("default_branch") or repository.default_branch)
    await session.flush()
    return repository


async def upsert_pull_request(
    session: AsyncSession, repository_id: int, data: dict[str, Any]
) -> PullRequest:
    number = int(data["number"])
    existing = (
        await session.execute(
            sa.select(PullRequest).where(
                PullRequest.repository_id == repository_id, PullRequest.number == number
            )
        )
    ).scalar_one_or_none()

    head = data.get("head") or {}
    base = data.get("base") or {}
    values = {
        "title": (data.get("title") or "")[:512] or None,
        "author": ((data.get("user") or {}).get("login") or "")[:255] or None,
        "state": str(data.get("state") or "open"),
        "url": (data.get("html_url") or "")[:1024] or None,
        "head_sha": (head.get("sha") or "")[:64] or None,
        "base_sha": (base.get("sha") or "")[:64] or None,
    }
    if existing is None:
        existing = PullRequest(repository_id=repository_id, number=number, **values)
        session.add(existing)
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    await session.flush()
    return existing


async def find_cached_run(
    session: AsyncSession, repository_id: int, pr_number: int, head_sha: str
) -> ReviewRun | None:
    """A completed review for this exact SHA means we never pay for it twice."""
    result = await session.execute(
        sa.select(ReviewRun)
        .where(
            ReviewRun.repository_id == repository_id,
            ReviewRun.pr_number == pr_number,
            ReviewRun.head_sha == head_sha,
            ReviewRun.status == "completed",
        )
        .order_by(ReviewRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def has_active_run(
    session: AsyncSession, repository_id: int, pr_number: int, head_sha: str
) -> ReviewRun | None:
    result = await session.execute(
        sa.select(ReviewRun)
        .where(
            ReviewRun.repository_id == repository_id,
            ReviewRun.pr_number == pr_number,
            ReviewRun.head_sha == head_sha,
            ReviewRun.status.in_(ACTIVE_STATUSES),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_run(
    session: AsyncSession,
    *,
    repository_id: int,
    pull_request_id: UUID | None,
    pr_number: int,
    pr_title: str | None,
    head_sha: str,
    base_sha: str | None,
    mode: str,
    forced: bool = False,
    delivery_id: str | None = None,
    status: str = "queued",
    skip_reason: str | None = None,
) -> ReviewRun:
    run = ReviewRun(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        pr_number=pr_number,
        pr_title=(pr_title or "")[:512] or None,
        head_sha=head_sha,
        base_sha=base_sha,
        mode=mode,
        forced=forced,
        delivery_id=delivery_id,
        status=status,
        skip_reason=skip_reason,
        finished_at=utcnow() if status in {"skipped", "failed"} else None,
    )
    session.add(run)
    await session.flush()
    return run


async def enqueue_job(session: AsyncSession, run: ReviewRun, *, max_attempts: int = 3) -> Job:
    job = Job(run_id=run.id, kind="review", status="queued", max_attempts=max_attempts)
    session.add(job)
    await session.flush()
    return job


async def record_delivery(
    session: AsyncSession, delivery_id: str, event: str, action: str | None
) -> bool:
    """Insert the delivery id. Returns False when it was already processed."""
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    dialect = getattr(getattr(bind, "dialect", None), "name", "postgresql")
    if dialect == "postgresql":
        statement = (
            pg_insert(WebhookDelivery)
            .values(delivery_id=delivery_id, event=event, action=action)
            .on_conflict_do_nothing(index_elements=["delivery_id"])
        )
        result = await session.execute(statement)
        return result.rowcount > 0

    statement = sa.text(
        "INSERT INTO webhook_deliveries (delivery_id, event, action) "
        "VALUES (:delivery_id, :event, :action) ON CONFLICT (delivery_id) DO NOTHING"
    )
    result = await session.execute(
        statement, {"delivery_id": delivery_id, "event": event, "action": action}
    )
    return bool(result.rowcount)


async def next_step_seq(session: AsyncSession, run_id: UUID) -> int:
    result = await session.execute(
        sa.select(sa.func.coalesce(sa.func.max(RunStep.seq), 0)).where(RunStep.run_id == run_id)
    )
    return int(result.scalar_one() or 0)


async def finalize_run(
    session: AsyncSession,
    run: ReviewRun,
    *,
    status: str,
    risk: str | None = None,
    summary: str | None = None,
    error: str | None = None,
    skip_reason: str | None = None,
    review_url: str | None = None,
    review_id: int | None = None,
    comments_posted: int = 0,
    files_reviewed: int = 0,
    llm_calls: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    run.status = status
    run.risk = risk
    run.summary = (summary or "")[:8000] or None
    run.error = (error or "")[:2000] or None
    run.skip_reason = skip_reason
    run.review_url = review_url
    run.review_id = review_id
    run.comments_posted = comments_posted
    run.files_reviewed = files_reviewed
    run.llm_calls = llm_calls
    run.tokens_in = tokens_in
    run.tokens_out = tokens_out
    run.finished_at = utcnow()
    await session.flush()


async def requeue_stuck_jobs(session: AsyncSession, *, older_than_seconds: int) -> int:
    """Recover jobs from a previous instance that died mid-run."""
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    result = await session.execute(
        sa.update(Job)
        .where(Job.status == "running", Job.locked_at.is_not(None), Job.locked_at < cutoff)
        .values(status="queued", run_after=utcnow(), locked_at=None)
    )
    stuck_runs = await session.execute(
        sa.update(ReviewRun)
        .where(ReviewRun.status == "running", ReviewRun.started_at < cutoff)
        .values(status="queued")
    )
    logger.info(
        "requeued %s stuck job(s) and reset %s run(s)",
        result.rowcount,
        stuck_runs.rowcount,
    )
    return int(result.rowcount or 0)
