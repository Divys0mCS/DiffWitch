"""Typed application settings.

Every secret is read from the environment (or a local ``.env`` file that is
git-ignored).  Nothing in this module contains a real credential and nothing
here is ever logged verbatim.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "production", "test"]
ReviewMode = Literal["lite", "agent"]
LLMProviderName = Literal["gemini", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- app ----
    app_name: str = "DiffWitch"
    version: str = "0.1.0"
    environment: Environment = "development"
    log_level: str = "INFO"

    # ---- database (Neon Postgres) ----
    # Pooled connection string, e.g.
    # postgresql+asyncpg://user:pass@ep-xxx-pooler.us-east-2.aws.neon.tech/db?sslmode=require
    database_url: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 2
    db_pool_recycle_seconds: int = 280
    db_connect_timeout_seconds: float = 10.0
    # Neon's pooled endpoint runs pgbouncer in transaction mode, which cannot
    # use server-side prepared statements.  0 is the safe default; raise it
    # only when connecting to a direct (non-pooled) endpoint.
    db_statement_cache_size: int = 0

    # ---- http / cors ----
    # Comma separated list. Only these origins may call the dashboard API.
    frontend_origin: str = "http://localhost:3000"

    # ---- dashboard auth ----
    dashboard_password: str = ""
    session_secret: str = ""
    session_ttl_hours: int = 168

    # ---- llm ----
    llm_provider: LLMProviderName = "gemini"
    # Never hardcoded: the exact model id is supplied by the operator.
    llm_model: str = ""
    gemini_api_key: str = ""
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_rpm: int = 5
    llm_rpd: int = 20
    daily_llm_budget: int = 18
    llm_max_output_tokens: int = 4096
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 4

    # ---- review behaviour ----
    review_mode: ReviewMode = "lite"
    max_agent_steps: int = 3
    max_prompt_chars: int = 60_000
    max_file_diff_chars: int = 12_000
    max_files_per_review: int = 40
    max_inline_comments: int = 25
    # Comma separated globs, matched case-insensitively against the file path.
    ignore_globs: str = (
        "package-lock.json,pnpm-lock.yaml,yarn.lock,poetry.lock,Pipfile.lock,"
        "*.min.js,*.min.css,*.map,*.png,*.jpg,*.jpeg,*.gif,*.svg,*.ico,*.webp,"
        "*.pdf,*.zip,*.woff,*.woff2,*.ttf,*.eot,*.so,*.dll,*.dylib,*.pyc,"
        "node_modules/**,vendor/**,dist/**,build/**,out/**,coverage/**,"
        "*.lock,*.snap,*.generated.*,*.g.dart,*.designer.cs"
    )

    # ---- github app ----
    github_app_id: str = ""
    # Base64 of the .pem contents (single line, safe for env vars).
    github_private_key_b64: str = ""
    github_webhook_secret: str = ""
    github_api_base: str = "https://api.github.com"
    github_user_agent: str = "DiffWitch"

    # ---- worker ----
    worker_enabled: bool = True
    worker_concurrency: int = 1
    worker_poll_interval_seconds: float = 2.0
    job_max_attempts: int = 3
    # A job left "running" for longer than this is considered abandoned
    # (the free Render instance was restarted) and is requeued on boot.
    job_stuck_after_seconds: int = 900

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("environment", mode="before")
    @classmethod
    def _lower_environment(cls, value: str) -> str:
        return str(value).lower()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def frontend_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.frontend_origin.split(",")
            if origin.strip()
        ]

    @property
    def ignore_glob_list(self) -> list[str]:
        return [glob.strip() for glob in self.ignore_globs.split(",") if glob.strip()]

    @property
    def github_private_key(self) -> str | None:
        """Return the PEM private key, decoded from base64 when needed."""
        raw = self.github_private_key_b64.strip()
        if not raw:
            return None
        if raw.startswith("-----BEGIN"):
            # Tolerate a raw PEM pasted with literal \n escapes.
            return raw.replace("\\n", "\n")
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        return decoded.replace("\\n", "\n")

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
