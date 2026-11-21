"""Runs API: list, detail, live event stream, and re-review."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app import db
from app.config import get_settings
from app.models import Job, Repository, ReviewRun, RunStep
from app.security import require_dashboard
from app.services.events import get_bus
from app.services.runs import create_run, enqueue_job
from app.services.settings_service import effective_settings, load_overrides

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"], dependencies=[Depends(require_dashboard)])

_TERMINAL = {"completed", "failed", "skipped"}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _run_dict(run: ReviewRun, repository: str) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "mode": run.mode,
        "risk": run.risk,
        "pr_number": run.pr_number,
        "pr_title": run.pr_title,
        "head_sha": run.head_sha,
        "repository": repository,
        "summary": run.summary,
        "error": run.error,
        "skip_reason": run.skip_reason,
        "review_url": run.review_url,
        "comments_posted": run.comments_posted,
        "files_reviewed": run.files_reviewed,
        "llm_calls": run.llm_calls,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


def _step_dict(step: RunStep) -> dict[str, Any]:
    return {
        "seq": step.seq,
        "kind": step.kind,
        "title": step.title,
        "detail": step.detail,
        "tokens": step.tokens,
        "duration_ms": step.duration_ms,
        "at": _iso(step.created_at),
    }


def _joined() -> Any:
    return sa.select(ReviewRun, Repository.full_name).join(
        Repository, Repository.id == ReviewRun.repository_id
    )


@router.get("")
async def list_runs(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    repository: str | None = None,
) -> dict[str, Any]:
    filters = []
    count_filters = []
    if status_filter:
        filters.append(ReviewRun.status == status_filter)
        count_filters.append(ReviewRun.status == status_filter)
    if repository:
        filters.append(Repository.full_name == repository)
        count_filters.append(Repository.full_name == repository)

    statement = (
        _joined().where(*filters).order_by(ReviewRun.created_at.desc()).limit(limit).offset(offset)
    )
    count_statement = (
        sa.select(sa.func.count())
        .select_from(ReviewRun)
        .join(Repository, Repository.id == ReviewRun.repository_id)
        .where(*count_filters)
    )

    async with db.session_scope() as session:
        rows = (await session.execute(statement)).all()
        total = (await session.execute(count_statement)).scalar_one()

    return {
        "runs": [_run_dict(run, name) for run, name in rows],
        "total": int(total or 0),
    }


@router.get("/{run_id}")
async def get_run(run_id: UUID) -> dict[str, Any]:
    async with db.session_scope() as session:
        row = (await session.execute(_joined().where(ReviewRun.id == run_id))).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        steps = (
            (
                await session.execute(
                    sa.select(RunStep)
                    .where(RunStep.run_id == run_id)
                    .order_by(RunStep.seq.asc())
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )
    return {
        "run": _run_dict(row[0], row[1]),
        "steps": [_step_dict(step) for step in steps],
    }


@router.get("/{run_id}/events")
async def run_events(run_id: UUID, request: Request) -> StreamingResponse:
    """SSE stream: a snapshot first, then live steps until the run finishes."""
    bus = get_bus()
    queue = await bus.subscribe(str(run_id))

    async def stream():
        try:
            async with db.session_scope() as session:
                row = (await session.execute(_joined().where(ReviewRun.id == run_id))).first()
                steps = (
                    (
                        await session.execute(
                            sa.select(RunStep)
                            .where(RunStep.run_id == run_id)
                            .order_by(RunStep.seq.asc())
                            .limit(1000)
                        )
                    )
                    .scalars()
                    .all()
                )

            if row is None:
                yield _sse({"type": "error", "message": "run not found"})
                return

            run = _run_dict(row[0], row[1])
            yield _sse({"type": "snapshot", "run": run, "steps": [_step_dict(s) for s in steps]})
            if run["status"] in _TERMINAL:
                yield _sse({"type": "run", "status": run["status"], "final": True})
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield _sse(event)
                if event.get("type") == "run" and event.get("final"):
                    break
        finally:
            await bus.unsubscribe(str(run_id), queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.post("/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def rerun(run_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    async with db.session_scope() as session:
        original = await session.get(ReviewRun, run_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Run not found")
        repository = await session.get(Repository, original.repository_id)
        if repository is None:
            raise HTTPException(status_code=409, detail="Repository record is missing")

        overrides = await load_overrides(session)
        mode = effective_settings(settings, overrides).review_mode

        run = await create_run(
            session,
            repository_id=original.repository_id,
            pull_request_id=original.pull_request_id,
            pr_number=original.pr_number,
            pr_title=original.pr_title,
            head_sha=original.head_sha,
            base_sha=original.base_sha,
            mode=mode,
            forced=True,
        )
        await enqueue_job(session, run, max_attempts=settings.job_max_attempts)
        new_id = str(run.id)

    logger.info("re-review queued for run %s as %s", run_id, new_id)
    return {"status": "queued", "run_id": new_id}


@router.get("/{run_id}/steps")
async def run_steps(run_id: UUID) -> dict[str, Any]:
    async with db.session_scope() as session:
        steps = (
            (
                await session.execute(
                    sa.select(RunStep)
                    .where(RunStep.run_id == run_id)
                    .order_by(RunStep.seq.asc())
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )
    return {"steps": [_step_dict(step) for step in steps]}


@router.get("/{run_id}/job")
async def run_job(run_id: UUID) -> dict[str, Any]:
    async with db.session_scope() as session:
        job = (
            await session.execute(
                sa.select(Job).where(Job.run_id == run_id).order_by(Job.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if job is None:
            return {"job": None}
        return {
            "job": {
                "status": job.status,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "last_error": job.last_error,
            }
        }
