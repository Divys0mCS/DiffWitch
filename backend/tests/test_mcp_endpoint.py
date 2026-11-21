from __future__ import annotations

import httpx
import pytest
from app.config import get_settings
from app.main import create_app

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    },
}

MCP_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


@pytest.fixture
def mcp_app(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    app = create_app()
    try:
        yield app
    finally:
        get_settings.cache_clear()


async def test_mcp_requires_bearer_token(mcp_app):
    transport = httpx.ASGITransport(app=mcp_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 401


async def test_mcp_rejects_wrong_token(mcp_app):
    transport = httpx.ASGITransport(app=mcp_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json=INITIALIZE,
            headers={**MCP_HEADERS, "authorization": "Bearer wrong"},
        )
        assert response.status_code == 401


async def test_mcp_initialize_handshake(mcp_app):
    transport = httpx.ASGITransport(app=mcp_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with mcp_app.router.lifespan_context(mcp_app):
            response = await client.post(
                "/mcp",
                json=INITIALIZE,
                headers={**MCP_HEADERS, "authorization": "Bearer test-password"},
            )
        assert response.status_code == 200
        assert "result" in response.text
        assert "diffwitch" in response.text


async def test_mcp_disabled_without_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    get_settings.cache_clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 503
    get_settings.cache_clear()


async def test_mounted_mcp_does_not_shadow_health(mcp_app):
    transport = httpx.ASGITransport(app=mcp_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "DiffWitch"
