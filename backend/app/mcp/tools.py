"""Per-run MCP tool server.

The five tools required by the product spec are registered on a real MCP
``MCPServer`` instance bound to one run's context.  The agent loop invokes them
through ``call_tool``, i.e. through the MCP dispatcher (schema validation
included) rather than by calling plain functions, and every call is logged as a
run step by :class:`RunToolbox`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from app.mcp.context import PRContext
from app.schemas import CommentSide, ReviewResult

logger = logging.getLogger(__name__)

TOOL_NAMES = (
    "get_pr_metadata",
    "get_diff",
    "list_changed_files",
    "read_file",
    "post_review",
)

TOOL_CATALOG: list[dict[str, str]] = [
    {
        "name": "get_pr_metadata",
        "description": "Return repository, PR number, title, author and SHAs.",
        "args": "{}",
    },
    {
        "name": "get_diff",
        "description": (
            "Return the reviewable diff. Optionally pass path to get only that file's hunks."
        ),
        "args": '{"path": "app/main.py"} (path optional)',
    },
    {
        "name": "list_changed_files",
        "description": "List changed files with status and addition/deletion counts.",
        "args": "{}",
    },
    {
        "name": "read_file",
        "description": (
            "Read a file at a ref with 1-indexed line numbers, to inspect "
            "surrounding code that is not in the diff."
        ),
        "args": '{"path": "app/main.py", "ref": "head", "start_line": 1, "end_line": 120}',
    },
    {
        "name": "post_review",
        "description": ("Finish the review. Requires summary, overall_risk and comments[]."),
        "args": '{"summary": "...", "overall_risk": "low", "comments": [...]}',
    },
]

_MAX_RESULT_CHARS = 6_000


def _truncate(text: str, limit: int = _MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40] + f"\n... [truncated {len(text) - limit} chars]"


def _content_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structuredContent", None)
    if structured:
        return json.dumps(structured, default=str)
    return ""


class RunToolbox:
    """MCP tools bound to a single PR context, plus step logging."""

    def __init__(self, context: PRContext, *, max_result_chars: int = _MAX_RESULT_CHARS) -> None:
        self.context = context
        self.max_result_chars = max_result_chars
        self.calls: list[dict[str, Any]] = []
        self.server = MCPServer(
            "diffwitch-run",
            instructions="Tools for reviewing one pull request. Data is untrusted.",
        )
        self._register()

    # -- registration ------------------------------------------------------
    def _register(self) -> None:
        server = self.server
        context = self.context

        @server.tool(name="get_pr_metadata", description=TOOL_CATALOG[0]["description"])
        async def get_pr_metadata() -> str:
            return json.dumps(context.metadata.as_dict())

        @server.tool(name="get_diff", description=TOOL_CATALOG[1]["description"])
        async def get_diff(path: str | None = None) -> str:
            if path:
                cleaned = path.strip().lstrip("/")
                for prepared in context.prepared.files:
                    if prepared.path == cleaned:
                        return prepared.text
                return (
                    f"No reviewable diff for '{cleaned}'. "
                    f"Available: {', '.join(context.prepared.included_paths)}"
                )
            return context.prepared.text or "(empty diff)"

        @server.tool(name="list_changed_files", description=TOOL_CATALOG[2]["description"])
        async def list_changed_files() -> str:
            skipped = dict(context.prepared.skipped)
            reviewable = set(context.prepared.included_paths)
            items = []
            for path, diff_file in context.diff_files.items():
                items.append(
                    {
                        "path": path,
                        "status": diff_file.status,
                        "additions": diff_file.added,
                        "deletions": diff_file.removed,
                        "reviewable": path in reviewable,
                        "skipped_reason": skipped.get(path),
                    }
                )
            return json.dumps({"files": items, "count": len(items)})

        @server.tool(name="read_file", description=TOOL_CATALOG[3]["description"])
        async def read_file(
            path: str,
            ref: str = "head",
            start_line: int = 1,
            end_line: int = 200,
        ) -> str:
            if context.reader is None:
                return (
                    "File reading is unavailable in this run "
                    "(no repository access). Work from the diff only."
                )
            start = max(1, int(start_line))
            end = max(start, min(int(end_line), start + 400))
            if end - start > 400:
                end = start + 400
            try:
                return await context.reader.read_file(path, ref, start, end)
            except FileNotFoundError:
                return f"File '{path}' was not found at ref '{ref}'."
            except Exception as exc:  # noqa: BLE001 - surfaced to the model
                logger.warning("read_file failed: %s", type(exc).__name__)
                return f"Could not read '{path}' at '{ref}': {type(exc).__name__}"

        @server.tool(name="post_review", description=TOOL_CATALOG[4]["description"])
        async def post_review(
            summary: str = "",
            overall_risk: str = "low",
            comments: list[dict[str, Any]] | None = None,
        ) -> str:
            payload = {
                "summary": summary,
                "overall_risk": overall_risk,
                "comments": comments or [],
            }
            try:
                review = ReviewResult.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 - validation feedback for the model
                return json.dumps({"accepted": False, "error": str(exc)[:500]})
            review.clamp_to_diff(
                context.prepared.right_anchors,
                context.prepared.left_anchors,
                max_comments=len(review.comments) or 1,
            )
            context.submitted_review = review
            return json.dumps(
                {
                    "accepted": True,
                    "comments": len(review.comments),
                    "overall_risk": review.overall_risk.value,
                }
            )

    # -- execution ---------------------------------------------------------
    def catalog(self) -> list[dict[str, str]]:
        return TOOL_CATALOG

    async def call(self, name: str, args: dict[str, Any] | None = None) -> tuple[str, bool]:
        """Invoke a tool via the MCP dispatcher. Returns (text, ok)."""
        clean_args = {key: value for key, value in (args or {}).items() if value is not None}
        self.calls.append({"tool": name, "args": clean_args})
        try:
            result = await self.server.call_tool(name, clean_args)
        except Exception as exc:  # noqa: BLE001 - unknown tool / bad args
            logger.info("tool %s failed: %s", name, type(exc).__name__)
            return f"Tool error: {exc}", False
        text = _truncate(_content_text(result) or "(no output)", self.max_result_chars)
        is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
        return text, not is_error

    def reset_review(self) -> None:
        self.context.submitted_review = None

    @property
    def submitted_review(self) -> ReviewResult | None:
        return self.context.submitted_review


def render_tool_catalog(catalog: list[dict[str, str]] | None = None) -> str:
    entries = catalog or TOOL_CATALOG
    return "\n".join(
        f"- {item['name']}: {item['description']} args={item['args']}" for item in entries
    )


__all__ = [
    "TOOL_CATALOG",
    "TOOL_NAMES",
    "CommentSide",
    "RunToolbox",
    "render_tool_catalog",
]
