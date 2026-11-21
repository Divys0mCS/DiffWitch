from __future__ import annotations

import base64

import pytest
from app.config import Settings, get_settings
from app.db import normalize_database_url


def test_settings_defaults_are_free_tier_safe():
    settings = Settings(_env_file=None)
    assert settings.review_mode == "lite"
    assert settings.max_agent_steps == 3
    assert settings.llm_rpm == 5
    assert settings.daily_llm_budget == 18
    assert settings.worker_concurrency == 1


def test_frontend_origins_are_parsed_and_trimmed():
    settings = Settings(
        _env_file=None, frontend_origin="https://a.vercel.app/, http://localhost:3000/"
    )
    assert settings.frontend_origins == ["https://a.vercel.app", "http://localhost:3000"]


def test_ignore_globs_parsed():
    settings = Settings(_env_file=None, ignore_globs="*.lock, vendor/**,*.min.js")
    assert settings.ignore_glob_list == ["*.lock", "vendor/**", "*.min.js"]


def test_github_private_key_decodes_base64():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    encoded = base64.b64encode(pem.encode()).decode()
    settings = Settings(_env_file=None, github_private_key_b64=encoded)
    assert settings.github_private_key == pem


def test_github_private_key_accepts_raw_pem():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    settings = Settings(_env_file=None, github_private_key_b64=pem)
    assert settings.github_private_key == pem


def test_github_private_key_invalid_base64_returns_none():
    settings = Settings(_env_file=None, github_private_key_b64="not base64 !!!")
    assert settings.github_private_key is None


def test_environment_is_lowercased():
    settings = Settings(_env_file=None, environment="PRODUCTION")
    assert settings.environment == "production"
    assert settings.is_production is True


def test_normalize_postgres_scheme_and_ssl():
    url, connect_args = normalize_database_url(
        "postgres://user:pw@ep-cool-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
    )
    assert url == "postgresql+asyncpg://user:pw@ep-cool-pooler.us-east-2.aws.neon.tech/neondb"
    assert connect_args["ssl"] is True
    assert connect_args["statement_cache_size"] == 0


def test_normalize_strips_libpq_only_query_args():
    url, connect_args = normalize_database_url(
        "postgresql://u:p@localhost:5432/db?sslmode=disable&channel_binding=prefer&pgbouncer=true"
    )
    assert "sslmode" not in url
    assert "channel_binding" not in url
    assert "pgbouncer" not in url
    assert "ssl" not in connect_args


def test_normalize_forces_ssl_for_neon_host():
    _, connect_args = normalize_database_url("postgresql://u:p@ep-x.neon.tech/db")
    assert connect_args["ssl"] is True


def test_normalize_keeps_unknown_query_args():
    url, _ = normalize_database_url(
        "postgresql://u:p@localhost/db?application_name=diffwitch&foo=bar"
    )
    assert "foo=bar" in url
    assert "application_name" not in url


def test_normalize_sqlite_passthrough():
    url, connect_args = normalize_database_url("sqlite+aiosqlite:///:memory:")
    assert url == "sqlite+aiosqlite:///:memory:"
    assert connect_args == {}


def test_normalize_rejects_empty_url():
    with pytest.raises(ValueError):
        normalize_database_url("")


def test_settings_cached_singleton():
    assert get_settings() is get_settings()
