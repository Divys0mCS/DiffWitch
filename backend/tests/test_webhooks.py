from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from app import db
from app.config import get_settings
from app.main import create_app
from app.models import Job, ReviewRun

SECRET = "whsec_test_only"


def _payload(action: str = "opened", *, sha: str = "a" * 40, number: int = 5) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {
            "id": 1,
            "account": {"login": "acme", "type": "Organization"},
        },
        "repository": {
            "id": 10,
            "full_name": "acme/app",
            "private": False,
            "default_branch": "main",
        },
        "pull_request": {
            "number": number,
            "title": "Bound the loop",
            "state": "open",
            "html_url": f"https://github.com/acme/app/pull/{number}",
            "user": {"login": "dev"},
            "head": {"sha": sha},
            "base": {"sha": "b" * 40, "ref": "main"},
        },
    }


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def client(monkeypatch, sqlite_engine):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    get_settings.cache_clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    get_settings.cache_clear()


async def _post(client, payload, *, delivery="d-1", event="pull_request", signature=None):
    body = json.dumps(payload).encode()
    headers = {
        "content-type": "application/json",
        "x-github-event": event,
        "x-github-delivery": delivery,
        "x-hub-signature-256": signature if signature is not None else _sign(body),
    }
    return await client.post("/webhooks/github", content=body, headers=headers)


async def test_valid_webhook_queues_a_run(client):
    response = await _post(client, _payload())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    run_id = UUID(body["run_id"])

    async with db.session_scope() as session:
        run = await session.get(ReviewRun, run_id)
        assert run is not None
        assert run.status == "queued"
        assert run.head_sha == "a" * 40
        jobs = (await session.execute(sa.select(Job).where(Job.run_id == run.id))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == "queued"


async def test_bad_signature_is_rejected(client):
    response = await _post(client, _payload(), signature="sha256=" + "0" * 64)
    assert response.status_code == 401


async def test_missing_signature_is_rejected(client):
    response = await _post(client, _payload(), signature="")
    assert response.status_code == 401


async def test_duplicate_delivery_is_ignored(client):
    first = await _post(client, _payload(), delivery="d-dup")
    second = await _post(client, _payload(), delivery="d-dup")
    assert first.json()["status"] == "queued"
    assert second.json() == {"duplicate": True}

    async with db.session_scope() as session:
        count = (await session.execute(sa.select(sa.func.count()).select_from(ReviewRun))).scalar()
    assert count == 1


async def test_cached_sha_is_not_reviewed_again(client):
    await _post(client, _payload(sha="c" * 40, number=6), delivery="d-cache-0")
    async with db.session_scope() as session:
        run = (
            await session.execute(sa.select(ReviewRun).where(ReviewRun.head_sha == "c" * 40))
        ).scalar_one()
        run.status = "completed"

    response = await _post(client, _payload(sha="c" * 40, number=6), delivery="d-cache-1")
    body = response.json()
    assert body["status"] == "cached"

    async with db.session_scope() as session:
        runs = (
            (await session.execute(sa.select(ReviewRun).where(ReviewRun.head_sha == "c" * 40)))
            .scalars()
            .all()
        )
        assert len(runs) == 2
        assert any(run.status == "skipped" and run.skip_reason == "cached" for run in runs)


async def test_unhandled_action_is_ignored(client):
    response = await _post(client, _payload(action="closed"), delivery="d-closed")
    assert response.json()["ignored"] is True


async def test_unhandled_event_is_ignored(client):
    response = await _post(client, {"action": "x"}, event="issues", delivery="d-events")
    assert response.json()["ignored"] is True


async def test_ping_is_acknowledged(client):
    response = await _post(client, {"zen": "hi"}, event="ping", delivery="d-ping")
    assert response.json()["ok"] is True


async def test_missing_secret_returns_503(monkeypatch, sqlite_engine):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        body = json.dumps(_payload()).encode()
        response = await c.post(
            "/webhooks/github",
            content=body,
            headers={
                "content-type": "application/json",
                "x-github-event": "pull_request",
                "x-github-delivery": "d-nosecret",
                "x-hub-signature-256": "sha256=deadbeef",
            },
        )
    assert response.status_code == 503
    get_settings.cache_clear()


async def test_synchronize_queues_a_new_run(client):
    first = await _post(client, _payload(sha="d" * 40, number=7), delivery="d-sync-1")
    second = await _post(
        client,
        _payload(action="synchronize", sha="e" * 40, number=7),
        delivery="d-sync-2",
    )
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "queued"
    assert first.json()["run_id"] != second.json()["run_id"]
