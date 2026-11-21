"""Post one GitHub review with inline comments, safely.

* every comment is re-checked against the diff anchors (again) before sending;
* comments already present on the PR with the same path/line/message are skipped;
* if GitHub rejects the inline payload (422), the review is posted as a summary
  body so the user still gets the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.schemas import CommentSide, ReviewComment, ReviewResult
from app.services.github import GitHubClient, GitHubError

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {"critical": "🛑", "warning": "⚠️", "info": "💡"}


@dataclass(slots=True)
class PostedReview:
    ok: bool
    review_id: int | None = None
    review_url: str | None = None
    comments_posted: int = 0
    degraded_to_summary: bool = False
    error: str | None = None


def render_comment_body(comment: ReviewComment) -> str:
    icon = _SEVERITY_ICON.get(comment.severity.value, "💡")
    parts = [
        f"{icon} **{comment.severity.value.upper()}** · `{comment.category.value}`",
        "",
        comment.message,
    ]
    if comment.suggested_fix:
        parts.extend(["", "Suggested fix:", "```", comment.suggested_fix.strip(), "```"])
    return "\n".join(parts)


def render_summary_body(result: ReviewResult, *, mode: str, dropped: int = 0) -> str:
    risk = result.overall_risk.value.upper()
    lines = [
        f"### 🧙 DiffWitch · risk: {risk}",
        "",
        result.summary,
        "",
    ]
    if result.comments:
        lines.append(f"_{len(result.comments)} inline comment(s) below._")
    else:
        lines.append("_No issues worth flagging._")
    if dropped:
        lines.append("")
        lines.append(f"_{dropped} additional comment(s) could not be anchored to a diff line._")
    lines.extend(
        [
            "",
            "---",
            f"<sub>Automated review · {mode.upper()} mode · "
            "DiffWitch reviews code, not people. Verify before acting.</sub>",
        ]
    )
    return "\n".join(lines)


def _existing_keys(existing: list[dict[str, Any]]) -> set[tuple[str, int, str]]:
    keys: set[tuple[str, int, str]] = set()
    for item in existing or []:
        path = str(item.get("path") or "")
        line = item.get("line") or item.get("original_line") or 0
        body = str(item.get("body") or "")
        try:
            keys.add((path, int(line), _normalise(body)))
        except (TypeError, ValueError):
            continue
    return keys


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())[:300]


async def post_review(
    client: GitHubClient,
    *,
    owner: str,
    repo: str,
    number: int,
    head_sha: str,
    result: ReviewResult,
    mode: str,
    right_anchors: dict[str, set[int]] | None = None,
    left_anchors: dict[str, set[int]] | None = None,
    dropped: int = 0,
    commit_id: str | None = None,
) -> PostedReview:
    body = render_summary_body(result, mode=mode, dropped=dropped)

    try:
        existing = await client.list_review_comments(owner, repo, number)
    except GitHubError as exc:
        logger.warning("could not list existing review comments: %s", exc.message)
        existing = []
    seen = _existing_keys(existing)

    payload: list[dict[str, Any]] = []
    skipped_duplicates = 0
    for comment in result.comments:
        if right_anchors is not None:
            anchors = left_anchors or {} if comment.side is CommentSide.LEFT else right_anchors
            if comment.line not in anchors.get(comment.file, set()):
                continue
        rendered = render_comment_body(comment)
        key = (comment.file, comment.line, _normalise(rendered))
        if key in seen:
            skipped_duplicates += 1
            continue
        seen.add(key)
        payload.append(
            {
                "path": comment.file,
                "line": comment.line,
                "side": comment.side.value,
                "body": rendered,
            }
        )

    if skipped_duplicates:
        logger.info("skipped %s duplicate review comment(s)", skipped_duplicates)

    if not payload:
        try:
            created = await client.create_review(
                owner, repo, number, body=body, comments=[], commit_id=commit_id or head_sha
            )
        except GitHubError as exc:
            return PostedReview(ok=False, error=exc.message)
        return PostedReview(
            ok=True,
            review_id=_as_int(created.get("id")),
            review_url=created.get("html_url"),
            comments_posted=0,
        )

    try:
        created = await client.create_review(
            owner, repo, number, body=body, comments=payload, commit_id=commit_id or head_sha
        )
        return PostedReview(
            ok=True,
            review_id=_as_int(created.get("id")),
            review_url=created.get("html_url"),
            comments_posted=len(payload),
        )
    except GitHubError as exc:
        if exc.status_code != 422:
            return PostedReview(ok=False, error=exc.message)
        logger.warning("inline review rejected (422); retrying as a summary")
        fallback_body = (
            body + "\n\n> Inline comments could not be attached to this diff; "
            "the findings are summarised here instead.\n"
        )
        for comment in result.comments[:10]:
            fallback_body += f"\n- `{comment.file}:{comment.line}` {comment.message[:300]}"
        try:
            created = await client.create_review(
                owner,
                repo,
                number,
                body=fallback_body,
                comments=[],
                commit_id=commit_id or head_sha,
            )
        except GitHubError as fallback_exc:
            return PostedReview(ok=False, error=fallback_exc.message)
        return PostedReview(
            ok=True,
            review_id=_as_int(created.get("id")),
            review_url=created.get("html_url"),
            comments_posted=0,
            degraded_to_summary=True,
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
