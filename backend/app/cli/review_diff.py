"""CLI: review a unified diff without GitHub or a database.

Usage (from backend/, venv active):

    python -m app.cli.review_diff --diff ..\\sample.diff --repo owner/name --pr 12
    python -m app.cli.review_diff --diff sample.diff --repo o/n --pr 1 --provider fake
    git diff main...HEAD | Out-File -Encoding utf8 sample.diff

Exit codes: 0 ok, 1 error, 2 skipped (quota / nothing to review).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.config import get_settings
from app.logs import configure_logging, redact
from app.services.agent import run_agent
from app.services.llm import build_provider
from app.services.llm.base import LLMConfigError, LLMError
from app.services.quota import QuotaGuard
from app.services.review import ReviewRequest, review_diff
from app.services.steps import Step

_MARKERS = {
    "note": "·",
    "llm_call": "◆",
    "tool_call": "→",
    "tool_result": "←",
    "verdict": "★",
    "error": "!",
}


def _print_step(step: Step) -> None:
    suffix = ""
    if step.tokens:
        suffix += f" [{step.tokens} tokens]"
    if step.duration_ms is not None:
        suffix += f" [{step.duration_ms} ms]"
    print(f"{_MARKERS.get(step.kind, '·')} {step.title}{suffix}")
    if step.kind == "error" and step.detail:
        print(f"    {redact(step.detail.get('message', ''))}")


class PrintingSink:
    async def emit(self, kind, title, detail=None, *, tokens=None, duration_ms=None) -> Step:
        step = Step(kind=kind, title=title, detail=detail, tokens=tokens, duration_ms=duration_ms)
        _print_step(step)
        return step


async def _run(args: argparse.Namespace, diff_text: str) -> int:
    settings = get_settings()

    try:
        provider = build_provider(settings)
    except LLMConfigError as exc:
        print(f"Configuration problem: {exc.message}", file=sys.stderr)
        print("Tip: run with --provider fake to exercise the pipeline offline.", file=sys.stderr)
        return 1

    request = ReviewRequest(
        repo=args.repo,
        pr_number=args.pr,
        pr_title=args.title,
        head_sha=args.sha,
        diff=diff_text,
        base_ref=args.base,
        mode=args.mode,
    )

    print(f"DiffWitch · reviewing {args.repo}#{args.pr} ({args.diff}) in {args.mode} mode")
    runner = review_diff if args.mode == "lite" else run_agent
    try:
        outcome = await runner(
            request,
            settings=settings,
            provider=provider,
            guard=QuotaGuard(settings),
            sink=PrintingSink(),
        )
    except LLMError as exc:
        print(f"LLM error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        closer = getattr(provider, "aclose", None)
        if closer is not None:
            await closer()

    if outcome.result is not None:
        payload = outcome.result.model_dump(mode="json")
        print()
        print(json.dumps(payload, indent=2))
        if args.out:
            await asyncio.to_thread(
                Path(args.out).write_text, json.dumps(payload, indent=2), "utf-8"
            )
            print(f"\nSaved to {args.out}")

    if outcome.skipped_reason:
        print(f"\nSkipped: {outcome.skipped_reason}", file=sys.stderr)
        if outcome.error:
            print(f"Reason: {outcome.error}", file=sys.stderr)
        return 2 if outcome.result is None else 0
    if outcome.error:
        print(f"\nFailed: {outcome.error}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a unified diff with DiffWitch.")
    parser.add_argument("--diff", required=True, help="Path to a unified diff file")
    parser.add_argument("--repo", default="local/repo", help="owner/name")
    parser.add_argument("--pr", type=int, default=1, help="PR number")
    parser.add_argument("--title", default="", help="PR title")
    parser.add_argument("--sha", default="0" * 40, help="Head SHA")
    parser.add_argument("--base", default="main", help="Base ref")
    parser.add_argument("--out", help="Write the JSON review to this path")
    parser.add_argument(
        "--mode",
        choices=["lite", "agent"],
        default="lite",
        help="lite = one LLM call, agent = bounded MCP tool loop",
    )
    parser.add_argument(
        "--provider",
        choices=["env", "gemini", "fake"],
        default="env",
        help="Override LLM_PROVIDER for this run",
    )
    args = parser.parse_args()

    if args.provider != "env":
        os.environ["LLM_PROVIDER"] = args.provider
        get_settings.cache_clear()

    configure_logging(get_settings().log_level)

    diff_path = Path(args.diff)
    if not diff_path.is_file():
        print(f"Diff file not found: {diff_path}", file=sys.stderr)
        return 1
    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    if not diff_text.strip():
        print("The diff file is empty.", file=sys.stderr)
        return 1

    return asyncio.run(_run(args, diff_text))


if __name__ == "__main__":
    raise SystemExit(main())
