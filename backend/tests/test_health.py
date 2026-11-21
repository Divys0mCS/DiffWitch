from __future__ import annotations

import httpx
import pytest
from app import db
from app.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def test_health_ok_without_database(client: httpx.AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "not_configured"
    assert body["service"] == "DiffWitch"
    assert "version" in body


async def test_ready_is_503_without_database(client: httpx.AsyncClient):
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["ready"] is False


async def test_health_reports_database_up(client: httpx.AsyncClient, sqlite_engine):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "up"
    assert body["status"] == "ok"
    assert body["db_latency_ms"] is not None


async def test_ready_is_200_with_database(client: httpx.AsyncClient, sqlite_engine):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


async def test_health_degraded_when_ping_fails(
    client: httpx.AsyncClient, monkeypatch, sqlite_engine
):
    async def failing_ping():
        return False, 12.0

    monkeypatch.setattr(db, "ping", failing_ping)
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["db"] == "down"

    ready = await client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["reason"] == "database_unreachable"


async def test_root_endpoint(client: httpx.AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["health"] == "/health"


async def test_cors_allows_only_configured_origin(client: httpx.AsyncClient):
    allowed = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"

    blocked = await client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers
