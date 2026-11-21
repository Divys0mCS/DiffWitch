"""Validated review output.

The LLM's JSON is untrusted input: every field is coerced and bounded before it
can reach GitHub or the dashboard.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_MESSAGE_CHARS = 2000
MAX_SUMMARY_CHARS = 4000
MAX_SUGGESTED_FIX_CHARS = 4000


class CommentSide(StrEnum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Category(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_SEVERITY_ALIASES = {
    "info": Severity.INFO,
    "informational": Severity.INFO,
    "note": Severity.INFO,
    "nit": Severity.INFO,
    "minor": Severity.INFO,
    "low": Severity.INFO,
    "warning": Severity.WARNING,
    "warn": Severity.WARNING,
    "medium": Severity.WARNING,
    "moderate": Severity.WARNING,
    "major": Severity.WARNING,
    "critical": Severity.CRITICAL,
    "error": Severity.CRITICAL,
    "blocker": Severity.CRITICAL,
    "high": Severity.CRITICAL,
    "security": Severity.CRITICAL,
}

_CATEGORY_ALIASES = {
    "bug": Category.BUG,
    "correctness": Category.BUG,
    "logic": Category.BUG,
    "error": Category.BUG,
    "security": Category.SECURITY,
    "vulnerability": Category.SECURITY,
    "performance": Category.PERFORMANCE,
    "perf": Category.PERFORMANCE,
    "efficiency": Category.PERFORMANCE,
    "style": Category.STYLE,
    "readability": Category.STYLE,
    "maintainability": Category.STYLE,
    "naming": Category.STYLE,
    "docs": Category.STYLE,
}

_RISK_ALIASES = {
    "low": RiskLevel.LOW,
    "none": RiskLevel.LOW,
    "minor": RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "moderate": RiskLevel.MEDIUM,
    "med": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
    "critical": RiskLevel.HIGH,
    "severe": RiskLevel.HIGH,
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_text(value: str, limit: int) -> str:
    text = _ANSI_RE.sub("", str(value)).replace("\r\n", "\n").replace("\x00", "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


class ReviewComment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str = Field(min_length=1, max_length=1024)
    line: int = Field(ge=1, le=1_000_000)
    side: CommentSide = CommentSide.RIGHT
    severity: Severity = Severity.INFO
    category: Category = Category.BUG
    message: str = Field(min_length=1)
    suggested_fix: str | None = None

    @field_validator("file", mode="before")
    @classmethod
    def _normalise_path(cls, value: Any) -> str:
        path = str(value or "").strip().replace("\\", "/")
        # Models like to echo `b/app/x.py` or a leading slash from the diff.
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        return path.lstrip("/")

    @field_validator("side", mode="before")
    @classmethod
    def _normalise_side(cls, value: Any) -> str:
        raw = str(value or "RIGHT").strip().upper()
        return "LEFT" if raw.startswith("L") else "RIGHT"

    @field_validator("severity", mode="before")
    @classmethod
    def _normalise_severity(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return _SEVERITY_ALIASES.get(raw, Severity.INFO).value

    @field_validator("category", mode="before")
    @classmethod
    def _normalise_category(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return _CATEGORY_ALIASES.get(raw, Category.BUG).value

    @field_validator("line", mode="before")
    @classmethod
    def _normalise_line(cls, value: Any) -> int:
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            return int(match.group()) if match else 1
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    @field_validator("message", mode="before")
    @classmethod
    def _normalise_message(cls, value: Any) -> str:
        text = _clean_text(value if isinstance(value, str) else str(value), MAX_MESSAGE_CHARS)
        return text or "No detail provided."

    @field_validator("suggested_fix", mode="before")
    @classmethod
    def _normalise_fix(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = _clean_text(str(value), MAX_SUGGESTED_FIX_CHARS)
        return text or None


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1)
    overall_risk: RiskLevel = RiskLevel.LOW
    comments: list[ReviewComment] = Field(default_factory=list)

    @field_validator("overall_risk", mode="before")
    @classmethod
    def _normalise_risk(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return _RISK_ALIASES.get(raw, RiskLevel.LOW).value

    @field_validator("summary", mode="before")
    @classmethod
    def _normalise_summary(cls, value: Any) -> str:
        text = _clean_text(value if isinstance(value, str) else str(value), MAX_SUMMARY_CHARS)
        return text or "The reviewer did not return a summary."

    @field_validator("comments", mode="before")
    @classmethod
    def _normalise_comments(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @model_validator(mode="after")
    def _deduplicate(self) -> ReviewResult:
        """Drop duplicate (file, line, side, message) comments, keeping order."""
        seen: set[tuple[str, int, str, str]] = set()
        unique: list[ReviewComment] = []
        for comment in self.comments:
            key = (comment.file, comment.line, comment.side.value, comment.message.lower()[:200])
            if key in seen:
                continue
            seen.add(key)
            unique.append(comment)
        self.comments = unique
        return self

    def clamp_to_diff(
        self,
        allowed_right: dict[str, set[int]],
        allowed_left: dict[str, set[int]],
        *,
        max_comments: int,
    ) -> tuple[ReviewResult, list[ReviewComment]]:
        """Keep only comments anchored to lines that were actually shown.

        Returns the filtered result plus the dropped comments so the caller can
        report "N comments could not be anchored" instead of silently losing
        them.
        """
        kept: list[ReviewComment] = []
        dropped: list[ReviewComment] = []
        for comment in self.comments:
            anchors = allowed_left if comment.side is CommentSide.LEFT else allowed_right
            if comment.line in anchors.get(comment.file, set()):
                kept.append(comment)
            else:
                dropped.append(comment)
        self.comments = kept[:max_comments]
        if len(kept) > max_comments:
            dropped.extend(kept[max_comments:])
        return self, dropped


def parse_review_payload(payload: Any) -> ReviewResult:
    """Coerce arbitrary model output into a ReviewResult.

    Tolerates markdown fences and leading prose around the JSON object.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("```")
            ).strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("review payload must be a JSON object")
    return ReviewResult.model_validate(payload)


# Gemini responseSchema (OpenAPI subset; no $refs).
REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "overall_risk": {"type": "STRING", "enum": ["low", "medium", "high"]},
        "comments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "file": {"type": "STRING"},
                    "line": {"type": "INTEGER"},
                    "side": {"type": "STRING", "enum": ["LEFT", "RIGHT"]},
                    "severity": {"type": "STRING", "enum": ["info", "warning", "critical"]},
                    "category": {
                        "type": "STRING",
                        "enum": ["bug", "security", "performance", "style"],
                    },
                    "message": {"type": "STRING"},
                    "suggested_fix": {"type": "STRING"},
                },
                "required": ["file", "line", "side", "severity", "category", "message"],
            },
        },
    },
    "required": ["summary", "overall_risk", "comments"],
}
