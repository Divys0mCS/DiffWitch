from __future__ import annotations

import time

from app.config import Settings
from app.security import (
    issue_session,
    password_matches,
    verify_session,
)


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "dashboard_password": "hunter2-correct-horse",
        "session_secret": "s" * 40,
        "session_ttl_hours": 1,
    }
    values.update(overrides)
    return Settings(**values)


def test_password_comparison():
    settings = _settings()
    assert password_matches(settings, "hunter2-correct-horse") is True
    assert password_matches(settings, "wrong") is False
    assert password_matches(_settings(dashboard_password=""), "") is False


def test_session_round_trip():
    settings = _settings()
    token, ttl = issue_session(settings)
    assert ttl == 3600
    assert verify_session(settings, token) is True


def test_tampered_session_is_rejected():
    settings = _settings()
    token, _ = issue_session(settings)
    body, _, signature = token.rpartition(".")
    assert verify_session(settings, f"{body}.{'0' * len(signature)}") is False


def test_session_signed_with_another_secret_is_rejected():
    token, _ = issue_session(_settings())
    assert verify_session(_settings(session_secret="different-secret-value"), token) is False


def test_expired_session_is_rejected():
    settings = _settings()
    token, _ = issue_session(settings, now=time.time() - 7200)
    assert verify_session(settings, token) is False


def test_garbage_tokens_are_rejected():
    settings = _settings()
    for token in ("", "no-dot", "a.b", "...", "eyJ.zzz"):
        assert verify_session(settings, token) is False


def test_derived_secret_when_session_secret_missing():
    settings = _settings(session_secret="")
    token, _ = issue_session(settings)
    assert verify_session(settings, token) is True
    assert verify_session(_settings(session_secret="", dashboard_password="other"), token) is False
