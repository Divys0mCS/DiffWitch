"""GitHub webhook receiver.

Verifies the HMAC signature in constant time, deduplicates on the delivery id,
persists what it needs and enqueues a job. It never talks to GitHub or the LLM,
so it answers in well under GitHub's 10-second timeout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app import db
from app.config import get_settings
from app.services.runs import (
    create_run,
    enqueue_job,
    find_cached_run,
    has_active_run,
    record_delivery,
    upsert_installation,
    upsert_pull_request,
    upsert_repository,
)
from app.services.settings_service import effective_settings, load_overrides

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

REVIEW_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


@router.post("/webhooks/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    settings = get_settings()
    body = await request.body()

    if not settings.github_webhook_secret:
        logger.error("webhook received but GITHUB_WEBHOOK_SECRET is not set")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret is not configured",
        )

    signature = request.headers.get("x-hub-signature-256")
    if not verify_signature(settings.github_webhook_secret, body, signature):
        logger.warning("rejected webhook with a bad signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")

    event = request.headers.get("x-github-event", "")
    delivery = request.headers.get("x-github-delivery", "")

    if event == "ping":
        return {"ok": True, "zen": "DiffWitch is listening"}

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    action = payload.get("action")

    if event == "installation":
        return await _handle_installation(payload, action)
    if event != "pull_request":
        return {"ignored": True, "reason": f"event '{event}' is not handled"}
    if action not in REVIEW_ACTIONS:
        return {"ignored": True, "reason": f"action '{action}' is not reviewed"}

    repository_data = payload.get("repository") or {}
    installation_data = payload.get("installation") or {}
    pr_data = payload.get("pull_request") or {}
    head_sha = str((pr_data.get("head") or {}).get("sha") or "")
    if not repository_data.get("id") or not installation_data.get("id") or not head_sha:
        return {"ignored": True, "reason": "incomplete payload"}

    if not delivery:
        delivery = hashlib.sha256(body).hexdigest()[:64]

    async with db.session_scope() as session:
        if not await record_delivery(session, delivery, event, action):
            logger.info("duplicate delivery %s ignored", delivery)
            return {"duplicate": True}

        installation = await upsert_installation(session, installation_data)
        repository = await upsert_repository(session, installation.id, repository_data)
        pull = await upsert_pull_request(session, repository.id, pr_data)
        overrides = await load_overrides(session)
        mode = effective_settings(settings, overrides).review_mode

        cached = await find_cached_run(session, repository.id, pull.number, head_sha)
        if cached is not None:
            run = await create_run(
                session,
                repository_id=repository.id,
                pull_request_id=pull.id,
                pr_number=pull.number,
                pr_title=pull.title,
                head_sha=head_sha,
                base_sha=pull.base_sha,
                mode=mode,
                delivery_id=delivery,
                status="skipped",
                skip_reason="cached",
            )
            logger.info("SHA %s already reviewed; recorded cached skip", head_sha[:8])
            return {"status": "cached", "run_id": str(run.id)}

        active = await has_active_run(session, repository.id, pull.number, head_sha)
        if active is not None:
            return {"status": "already_queued", "run_id": str(active.id)}

        run = await create_run(
            session,
            repository_id=repository.id,
            pull_request_id=pull.id,
            pr_number=pull.number,
            pr_title=pull.title,
            head_sha=head_sha,
            base_sha=pull.base_sha,
            mode=mode,
            delivery_id=delivery,
        )
        await enqueue_job(session, run, max_attempts=settings.job_max_attempts)
        run_id = str(run.id)

    logger.info(
        "queued run %s for %s#%s", run_id, repository_data.get("full_name"), pr_data.get("number")
    )
    return {"status": "queued", "run_id": run_id}


async def _handle_installation(payload: dict[str, Any], action: str | None) -> dict[str, Any]:
    installation_data = payload.get("installation") or {}
    if not installation_data.get("id"):
        return {"ignored": True}
    async with db.session_scope() as session:
        installation = await upsert_installation(session, installation_data)
        if action == "deleted":
            installation.suspended = True
    return {"status": "ok", "action": action}
