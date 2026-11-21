from __future__ import annotations

import httpx
import pytest
from app.config import get_settings
from app.main import create_app
from tests.fixtures import seed_run

AUTH = {"authorization": "Bearer test-pw"}


@pytest.fixture
async def client(monkeypatch, sqlite_engine):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pw")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "whsec")
    get_settings.cache_clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    get_settings.cache_clear()


async def test_dashboard_requires_auth(client):
    response = await client.get("/api/runs")
    assert response.status_code == 401


async def test_wrong_bearer_is_rejected(client):
    response = await client.get("/api/runs", headers={"authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_list_runs(client):
    run_id, _ = await seed_run()
    response = await client.get("/api/runs", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    run = body["runs"][0]
    assert run["id"] == str(run_id)
    assert run["repository"] == "acme/app"
    assert run["status"] == "completed"
    assert run["risk"] == "low"


async def test_list_runs_filters_by_status(client):
    await seed_run(status="completed")
    await seed_run(status="failed", with_steps=False, number=6)
    completed = await client.get("/api/runs?status=completed", headers=AUTH)
    assert completed.json()["total"] == 1
    failed = await client.get("/api/runs?status=failed", headers=AUTH)
    assert failed.json()["total"] == 1


async def test_run_detail_includes_ordered_steps(client):
    run_id, _ = await seed_run()
    response = await client.get(f"/api/runs/{run_id}", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["id"] == str(run_id)
    assert [step["seq"] for step in body["steps"]] == [1, 2, 3]
    assert body["steps"][1]["tokens"] == 120


async def test_unknown_run_is_404(client):
    response = await client.get("/api/runs/00000000-0000-0000-0000-000000000000", headers=AUTH)
    assert response.status_code == 404


async def test_sse_stream_sends_snapshot_for_finished_run(client):
    run_id, _ = await seed_run(status="completed")
    async with client.stream("GET", f"/api/runs/{run_id}/events", headers=AUTH) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = [chunk async for chunk in response.aiter_text()]
    body = "".join(chunks)
    assert '"type": "snapshot"' in body
    assert '"type": "run"' in body
    assert str(run_id) in body


async def test_sse_accepts_token_query_param(client):
    run_id, _ = await seed_run(status="completed")
    async with client.stream("GET", f"/api/runs/{run_id}/events?token=test-pw") as response:
        assert response.status_code == 200
        chunks = [chunk async for chunk in response.aiter_text()]
    assert '"type": "snapshot"' in "".join(chunks)


async def test_rerun_creates_a_new_job(client):
    run_id, _ = await seed_run(status="completed")
    response = await client.post(f"/api/runs/{run_id}/rerun", headers=AUTH)
    assert response.status_code == 202
    new_id = response.json()["run_id"]
    assert new_id != str(run_id)

    detail = await client.get(f"/api/runs/{new_id}", headers=AUTH)
    assert detail.json()["run"]["status"] == "queued"

    job = await client.get(f"/api/runs/{new_id}/job", headers=AUTH)
    assert job.json()["job"]["status"] == "queued"


async def test_settings_round_trip(client):
    current = await client.get("/api/settings", headers=AUTH)
    assert current.status_code == 200
    assert current.json()["review_mode"] == "lite"

    updated = await client.put(
        "/api/settings",
        headers=AUTH,
        json={"review_mode": "agent", "max_agent_steps": 4, "daily_llm_budget": 9},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["review_mode"] == "agent"
    assert body["max_agent_steps"] == 4
    assert body["daily_llm_budget"] == 9


async def test_settings_validation(client):
    response = await client.put("/api/settings", headers=AUTH, json={"review_mode": "turbo"})
    assert response.status_code == 422

    response = await client.put("/api/settings", headers=AUTH, json={"max_agent_steps": 999})
    assert response.status_code == 422


async def test_usage_reports_budget(client):
    await seed_run()
    response = await client.get("/api/usage", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["today"]["budget"] is not None
    assert body["limits"]["provider"] in {"gemini", "fake"}
    assert body["totals"]["runs"] == 1


async def test_login_flow(client):
    wrong = await client.post("/api/auth/login", json={"password": "bad"})
    assert wrong.status_code == 401

    ok = await client.post("/api/auth/login", json={"password": "test-pw"})
    assert ok.status_code == 200
    token = ok.json()["token"]
    assert token

    me = await client.get("/api/auth/me", headers={"authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
