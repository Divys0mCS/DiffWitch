"""Job worker.

The queue is the ``jobs`` table; there is no Redis.  Exactly one worker loop runs
per instance (the free tier runs one instance) and claims a single job at a time
with ``FOR UPDATE SKIP LOCKED`` so a second instance could never double-run it.
On boot, jobs left ``running`` by a previous (dead) instance are requeued.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from app import db
from app.config import Settings, get_settings
from app.models import Installation, Job, PullRequest, Repository, ReviewRun
from app.services.agent import run_agent
from app.services.events import EventBus, get_bus
from app.services.github import GitHubApp, GitHubError
from app.services.github_source import GitHubFileReader
from app.services.llm import build_provider
from app.services.llm.base import LLMConfigError, LLMError
from app.services.post_review import post_review
from app.services.quota import QuotaGuard
from app.services.review import ReviewRequest, review_diff
from app.services.runs import (
    finalize_run,
    next_step_seq,
    requeue_stuck_jobs,
    utcnow,
)
from app.services.settings_service import effective_settings, load_overrides
from app.services.steps import DbStepSink

logger = logging.getLogger(__name__)

_TERMINAL = {"completed", "failed", "skipped"}


# Literal statements (no interpolation) so a static analyser can prove they are
# constant. PostgreSQL uses SKIP LOCKED so a second instance can never claim the
# same job; SQLite (tests only) does not support it.
_CLAIM_PG = sa.text(
    "UPDATE jobs SET status = 'running', attempts = attempts + 1, "
    "locked_at = now(), updated_at = now() "
    "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now() "
    "ORDER BY run_after LIMIT 1 FOR UPDATE SKIP LOCKED) "
    "RETURNING id, run_id, attempts, max_attempts"
)

_CLAIM_SQLITE = sa.text(
    "UPDATE jobs SET status = 'running', attempts = attempts + 1, "
    "locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
    "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' "
    "AND run_after <= CURRENT_TIMESTAMP ORDER BY run_after LIMIT 1) "
    "RETURNING id, run_id, attempts, max_attempts"
)


def _as_uuid(value: Any) -> UUID:
    """Raw SQL returns a UUID on Postgres but hex text on SQLite."""
    if isinstance(value, UUID):
        return value
    text = str(value)
    return UUID(text) if "-" in text else UUID(hex=text)


class Worker:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        bus: EventBus | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._bus = bus or get_bus()
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.processed = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        async with db.session_scope() as session:
            await requeue_stuck_jobs(
                session, older_than_seconds=self._settings.job_stuck_after_seconds
            )
        self._task = asyncio.create_task(self._loop(), name="diffwitch-worker")
        logger.info("worker started (poll %.1fs)", self._settings.worker_poll_interval_seconds)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        logger.info("worker stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                claimed = await self.claim_one()
                if claimed is None:
                    await asyncio.sleep(self._settings.worker_poll_interval_seconds)
                    continue
                await self.process(claimed)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must never die
                logger.exception("worker loop error")
                await asyncio.sleep(min(30.0, self._settings.worker_poll_interval_seconds * 5))

    # -- queue -------------------------------------------------------------
    async def claim_one(self) -> SimpleNamespace | None:
        engine = db.get_engine()
        statement = _CLAIM_PG if engine.dialect.name == "postgresql" else _CLAIM_SQLITE
        async with db.session_scope() as session:
            row = (await session.execute(statement)).first()
        if row is None:
            return None
        return SimpleNamespace(
            id=_as_uuid(row[0]),
            run_id=_as_uuid(row[1]),
            attempts=int(row[2]),
            max_attempts=int(row[3]),
        )

    # -- execution ---------------------------------------------------------
    async def process(self, job: SimpleNamespace) -> None:
        run_id: UUID = job.run_id
        context = await self._load_context(run_id)
        if context is None:
            await self._close_job(job, ok=True)
            return

        run, repository, installation, pull_request, overrides, start_seq = context
        settings = effective_settings(self._settings, overrides)
        log = logging.LoggerAdapter(
            logger, {"run": str(run_id)[:8], "repo": repository.full_name, "pr": run.pr_number}
        )
        log.info("processing run %s (mode=%s)", run_id, run.mode)

        sink = DbStepSink(run.id, db.get_sessionmaker(), bus=self._bus, start_seq=start_seq)
        await sink.emit(
            "note",
            f"Run started for {repository.full_name}#{run.pr_number}",
            {
                "mode": run.mode,
                "head_sha": run.head_sha,
                "installation_id": installation.id,
            },
        )

        try:
            provider = build_provider(settings)
        except LLMConfigError as exc:
            await sink.emit("error", "LLM is not configured", {"message": exc.message})
            await self._complete(run.id, "failed", error=exc.message)
            await self._close_job(job, ok=False, retry=False, error=exc.message)
            return

        github = GitHubApp(settings)
        try:
            client = await github.client_for_installation(installation.id)
            await sink.emit(
                "note", "Authenticated as the GitHub App", {"installation_id": installation.id}
            )

            pull = await client.get_pull(repository.owner, repository.name, run.pr_number)
            await sink.emit(
                "tool_call",
                "get_pr_metadata",
                {"repo": repository.full_name, "number": run.pr_number},
            )
            diff_url = pull.get("diff_url") or ""
            await sink.emit(
                "tool_result",
                "PR metadata loaded",
                {
                    "title": (pull.get("title") or "")[:200],
                    "files": pull.get("changed_files"),
                    "additions": pull.get("additions"),
                    "deletions": pull.get("deletions"),
                    "diff_url": diff_url,
                },
            )

            diff = await client.get_pull_diff(repository.owner, repository.name, run.pr_number)
            await sink.emit(
                "tool_result",
                f"Downloaded diff ({len(diff)} chars)",
                {"chars": len(diff)},
            )

            request = ReviewRequest(
                repo=repository.full_name,
                pr_number=run.pr_number,
                pr_title=str(pull.get("title") or run.pr_title or ""),
                head_sha=str((pull.get("head") or {}).get("sha") or run.head_sha),
                diff=diff,
                base_ref=str((pull.get("base") or {}).get("ref") or ""),
                mode=run.mode,
            )
            reader = GitHubFileReader(
                client,
                owner=repository.owner,
                repo=repository.name,
                head_ref=request.head_sha,
                base_ref=str((pull.get("base") or {}).get("sha") or ""),
            )
            guard = QuotaGuard(settings)
            runner = review_diff if run.mode != "agent" else run_agent

            if run.mode == "agent":
                outcome = await runner(
                    request,
                    settings=settings,
                    provider=provider,
                    guard=guard,
                    sink=sink,
                    reader=reader,
                )
            else:
                outcome = await runner(
                    request,
                    settings=settings,
                    provider=provider,
                    guard=guard,
                    sink=sink,
                )

            await self._settle(
                run_id=run.id,
                job=job,
                outcome=outcome,
                client=client,
                repository=repository,
                pull=pull,
                settings=settings,
                mode=run.mode,
                log=log,
                pr_number=run.pr_number,
                head_sha=request.head_sha,
            )
        except GitHubError as exc:
            log.warning("github error: %s", exc.message)
            await sink.emit("error", "GitHub request failed", {"message": exc.message})
            await self._complete(run.id, "failed", error=exc.message)
            await self._close_job(job, ok=False, retry=exc.retryable, error=exc.message)
        except LLMError as exc:
            await sink.emit("error", "LLM call failed", {"message": exc.message})
            await self._complete(run.id, "failed", error=exc.message)
            await self._close_job(job, ok=False, retry=exc.retryable, error=exc.message)
        except Exception as exc:  # noqa: BLE001 - never leave a run running
            log.exception("run failed")
            message = f"{type(exc).__name__}: {exc}"
            await sink.emit("error", "Run failed", {"message": message[:500]})
            await self._complete(run.id, "failed", error=message)
            await self._close_job(job, ok=False, retry=True, error=message)
        finally:
            await github.aclose()
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    logger.debug("provider close failed")
            self.processed += 1

    async def _load_context(self, run_id: UUID):
        async with db.session_scope() as session:
            run = await session.get(ReviewRun, run_id)
            if run is None or run.status in _TERMINAL:
                return None
            repository = await session.get(Repository, run.repository_id)
            if repository is None:
                return None
            installation = await session.get(Installation, repository.installation_id)
            if installation is None:
                return None
            pull_request = (
                await session.get(PullRequest, run.pull_request_id) if run.pull_request_id else None
            )
            overrides = await load_overrides(session)
            start_seq = await next_step_seq(session, run.id)
            run.status = "running"
            run.started_at = utcnow()
            return run, repository, installation, pull_request, overrides, start_seq

    async def _settle(
        self,
        *,
        run_id: UUID,
        job: SimpleNamespace,
        outcome: Any,
        client: Any,
        repository: Repository,
        pull: dict[str, Any],
        settings: Settings,
        mode: str,
        log: logging.LoggerAdapter,
        pr_number: int,
        head_sha: str,
    ) -> None:
        # LITE returns a single `llm_result`; AGENT returns a list.
        results = list(getattr(outcome, "llm_results", []) or [])
        single = getattr(outcome, "llm_result", None)
        if not results and single is not None:
            results = [single]
        llm_calls = len(results)
        tokens_in = sum(result.prompt_tokens for result in results)
        tokens_out = sum(result.completion_tokens for result in results)

        if outcome.skipped_reason:
            await self._complete(
                run_id,
                "skipped",
                skip_reason=outcome.skipped_reason,
                error=outcome.error,
                llm_calls=llm_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            await self._close_job(job, ok=True)
            return

        if outcome.error or outcome.result is None:
            await self._complete(
                run_id,
                "failed",
                error=outcome.error or "review failed",
                llm_calls=llm_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            await self._close_job(job, ok=False, retry=False, error=outcome.error)
            return

        result = outcome.result
        prepared = outcome.prepared
        posted = await post_review(
            client,
            owner=repository.owner,
            repo=repository.name,
            number=pr_number,
            head_sha=head_sha,
            result=result,
            mode=mode,
            right_anchors=prepared.right_anchors if prepared else None,
            left_anchors=prepared.left_anchors if prepared else None,
            dropped=len(outcome.dropped),
        )

        if not posted.ok:
            log.warning("posting the review failed: %s", posted.error)
            await self._complete(
                run_id,
                "failed",
                error=f"Could not post the review: {posted.error}",
                risk=result.overall_risk.value,
                summary=result.summary,
                llm_calls=llm_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            await self._close_job(job, ok=False, retry=False, error=posted.error)
            return

        await self._complete(
            run_id,
            "completed",
            risk=result.overall_risk.value,
            summary=result.summary,
            review_url=posted.review_url,
            review_id=posted.review_id,
            comments_posted=posted.comments_posted,
            files_reviewed=len(prepared.files) if prepared else 0,
            llm_calls=llm_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        await self._close_job(job, ok=True)
        log.info("run completed with %s comment(s)", posted.comments_posted)

    async def _complete(self, run_id: UUID, status: str, **fields: Any) -> None:
        async with db.session_scope() as session:
            run = await session.get(ReviewRun, run_id)
            if run is None:
                return
            await finalize_run(session, run, status=status, **fields)
            payload = {
                "type": "run",
                "run_id": str(run_id),
                "status": status,
                "risk": run.risk,
                "comments": run.comments_posted,
                "final": True,
            }
        try:
            await self._bus.publish(str(run_id), payload)
        except Exception:  # noqa: BLE001
            logger.debug("could not publish run completion")

    async def _close_job(
        self,
        job: SimpleNamespace,
        *,
        ok: bool,
        retry: bool = False,
        error: str | None = None,
    ) -> None:
        async with db.session_scope() as session:
            row = await session.get(Job, job.id)
            if row is None:
                return
            row.last_error = (error or "")[:2000] or row.last_error
            if ok:
                row.status = "done"
                row.locked_at = None
            elif retry and job.attempts < job.max_attempts:
                row.status = "queued"
                row.locked_at = None
                row.run_after = utcnow() + timedelta(seconds=min(300, 15 * 2**job.attempts))
                logger.info("job %s requeued (attempt %s)", job.id, job.attempts)
            else:
                row.status = "failed"
                row.locked_at = None
