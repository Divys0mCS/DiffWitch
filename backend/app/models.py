"""SQLAlchemy 2.0 ORM models.

Types are kept portable so the test suite can run on SQLite while production
runs on Neon Postgres (JSON becomes JSONB on Postgres, UUID becomes native
``uuid``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres, JSON everywhere else (tests).
JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class Installation(Base):
    """A GitHub App installation (``id`` is GitHub's installation id)."""

    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
    account_login: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="User")
    suspended: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Repository(Base):
    """A repository the app is installed on (``id`` is GitHub's repo id)."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
    installation_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    private: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    default_branch: Mapped[str] = mapped_column(sa.String(255), nullable=False, default="main")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        sa.UniqueConstraint("repository_id", "number", name="uq_pull_requests_repo_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String(512))
    author: Mapped[str | None] = mapped_column(sa.String(255))
    state: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="open")
    url: Mapped[str | None] = mapped_column(sa.String(1024))
    head_sha: Mapped[str | None] = mapped_column(sa.String(64))
    base_sha: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ReviewRun(Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        sa.Index("ix_review_runs_repo_pr_sha", "repository_id", "pr_number", "head_sha"),
        sa.Index("ix_review_runs_status", "status"),
        sa.Index("ix_review_runs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pull_request_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True
    )
    pr_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    pr_title: Mapped[str | None] = mapped_column(sa.String(512))
    head_sha: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    base_sha: Mapped[str | None] = mapped_column(sa.String(64))
    mode: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="lite")
    # queued | running | completed | failed | skipped
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="queued")
    skip_reason: Mapped[str | None] = mapped_column(sa.String(64))
    risk: Mapped[str | None] = mapped_column(sa.String(16))
    summary: Mapped[str | None] = mapped_column(sa.Text)
    review_url: Mapped[str | None] = mapped_column(sa.String(1024))
    review_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    forced: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    llm_calls: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    files_reviewed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    comments_posted: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    delivery_id: Mapped[str | None] = mapped_column(sa.String(128))
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class RunStep(Base):
    """One observable step of the agent's reasoning, streamed to the dashboard."""

    __tablename__ = "run_steps"
    __table_args__ = (sa.UniqueConstraint("run_id", "seq", name="uq_run_steps_run_seq"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # llm_call | tool_call | tool_result | note | verdict | error
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String(256))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    tokens: Mapped[int | None] = mapped_column(sa.Integer)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = _created_at()


class Job(Base):
    """Postgres-backed queue (no Redis on the free tier)."""

    __tablename__ = "jobs"
    __table_args__ = (sa.Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="review")
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class UsageDaily(Base):
    __tablename__ = "usage_daily"

    day: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    llm_calls: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    runs: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = _updated_at()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    updated_at: Mapped[datetime] = _updated_at()


class WebhookDelivery(Base):
    """Delivery-id ledger so redelivered webhooks are ignored."""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    event: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(sa.String(64))
    received_at: Mapped[datetime] = _created_at()
