"""Smoke test: the Alembic migration applies cleanly from scratch."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "installations",
    "repositories",
    "pull_requests",
    "review_runs",
    "run_steps",
    "jobs",
    "usage_daily",
    "settings",
    "webhook_deliveries",
    "alembic_version",
}


def test_migration_upgrade_and_downgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "migration.db"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert EXPECTED_TABLES.issubset(tables)

    with sqlite3.connect(db_path) as conn:
        indexes = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "ix_review_runs_repo_pr_sha" in indexes
    assert "ix_jobs_status_run_after" in indexes

    command.downgrade(config, "base")

    with sqlite3.connect(db_path) as conn:
        remaining = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        }
    assert remaining <= {"alembic_version"}
