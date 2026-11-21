"""Data a run's MCP tools operate on.

Phase 3 binds a static diff (tests, CLI); Phase 4 binds a GitHub-backed reader
for the same interface, so tools never change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.schemas import ReviewResult
from app.services.diff import DiffFile, PreparedDiff, prepare_diff


@dataclass(slots=True)
class PRMetadata:
    repo: str
    number: int
    title: str = ""
    author: str = ""
    state: str = "open"
    url: str = ""
    head_sha: str = ""
    base_sha: str = ""
    base_ref: str = ""
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "author": self.author,
            "state": self.state,
            "url": self.url,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "base_ref": self.base_ref,
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
        }


@runtime_checkable
class FileReader(Protocol):
    """Reads a file at a ref, 1-indexed inclusive line range."""

    async def read_file(self, path: str, ref: str, start_line: int, end_line: int) -> str: ...


class StaticFileReader:
    """Dict-backed reader for tests and offline runs."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files
        self.reads: list[tuple[str, str, int, int]] = []

    async def read_file(self, path: str, ref: str, start_line: int, end_line: int) -> str:
        self.reads.append((path, ref, start_line, end_line))
        content = self._files.get(path)
        if content is None:
            raise FileNotFoundError(f"{path} is not available at ref '{ref}'")
        lines = content.splitlines()
        start = max(1, start_line)
        end = min(len(lines), max(start, end_line))
        return "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(start, end + 1))


@dataclass
class PRContext:
    metadata: PRMetadata
    diff: str
    prepared: PreparedDiff
    diff_files: dict[str, DiffFile]
    reader: FileReader | None = None
    submitted_review: ReviewResult | None = None
    notes: list[str] = field(default_factory=list)


def build_context(
    metadata: PRMetadata,
    diff: str,
    *,
    settings,
    reader: FileReader | None = None,
) -> PRContext:
    from app.services.diff import parse_diff

    prepared = prepare_diff(
        diff,
        ignore_globs=settings.ignore_glob_list,
        max_file_chars=settings.max_file_diff_chars,
        max_total_chars=settings.max_prompt_chars,
        max_files=settings.max_files_per_review,
    )
    parsed = {item.path: item for item in parse_diff(diff)}
    return PRContext(
        metadata=metadata,
        diff=diff,
        prepared=prepared,
        diff_files=parsed,
        reader=reader,
    )
