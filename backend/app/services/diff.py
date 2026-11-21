"""Unified-diff parsing, filtering and prompt-safe rendering.

The single most important invariant: a line number handed to the model must map
to a line that really exists in the diff, otherwise GitHub rejects the inline
comment.  Everything here is built around ``PreparedFile.right_lines`` /
``left_lines``, which are the *only* anchors a comment may target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

LineKind = Literal["add", "del", "ctx"]

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_BINARY_HINT = "Binary files"
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".psd",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".so", ".dll", ".dylib", ".exe",
    ".pyc", ".pyo", ".class", ".o", ".a", ".bin", ".dat", ".mp3", ".mp4", ".mov",
    ".wav", ".webm", ".avi", ".heic", ".avif", ".sqlite", ".db",
}  # fmt: skip
# Paths that are never worth a review even when they are text.
_HARD_SKIP_PATTERNS = ("node_modules/", "/node_modules/", ".venv/", "venv/", "__pycache__/")


@dataclass(slots=True)
class DiffLine:
    kind: LineKind
    text: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass(slots=True)
class DiffHunk:
    header: str
    old_start: int
    new_start: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass(slots=True)
class DiffFile:
    path: str
    old_path: str | None
    status: str  # added | modified | deleted | renamed
    is_binary: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.kind == "add")

    @property
    def removed(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.kind == "del")

    @property
    def changed(self) -> int:
        return self.added + self.removed


@dataclass(slots=True)
class PreparedFile:
    """A file rendered for the prompt, with the anchors the model may cite."""

    path: str
    text: str
    right_lines: set[int]
    left_lines: set[int]
    truncated: bool
    added: int
    removed: int


@dataclass(slots=True)
class PreparedDiff:
    text: str
    files: list[PreparedFile]
    skipped: list[tuple[str, str]]
    omitted: list[str]
    total_files_seen: int

    @property
    def right_anchors(self) -> dict[str, set[int]]:
        return {f.path: f.right_lines for f in self.files}

    @property
    def left_anchors(self) -> dict[str, set[int]]:
        return {f.path: f.left_lines for f in self.files}

    @property
    def included_paths(self) -> list[str]:
        return [f.path for f in self.files]


def _clean_diff_path(raw: str) -> str:
    path = raw.strip().split("\t")[0].strip()
    if path == "/dev/null":
        return path
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path.lstrip("/")


def parse_diff(raw: str) -> list[DiffFile]:
    """Parse a GitHub/unified diff into files with numbered hunks."""
    files: list[DiffFile] = []
    current: DiffFile | None = None
    hunk: DiffHunk | None = None
    old_no = new_no = 0

    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\r")

        if line.startswith("diff --git "):
            if current is not None:
                files.append(current)
            parts = line.split(" ")
            old = _clean_diff_path(parts[2]) if len(parts) > 2 else ""
            new = _clean_diff_path(parts[3]) if len(parts) > 3 else old
            current = DiffFile(path=new or old, old_path=old or None, status="modified")
            hunk = None
            continue

        if current is None:
            continue

        if line.startswith("new file mode"):
            current.status = "added"
            continue
        if line.startswith("deleted file mode"):
            current.status = "deleted"
            continue
        if line.startswith("rename from "):
            current.old_path = _clean_diff_path(line[len("rename from ") :])
            current.status = "renamed"
            continue
        if line.startswith("rename to "):
            current.path = _clean_diff_path(line[len("rename to ") :])
            current.status = "renamed"
            continue
        if line.startswith(_BINARY_HINT) or line.startswith("GIT binary patch"):
            current.is_binary = True
            continue
        if line.startswith("--- "):
            candidate = _clean_diff_path(line[4:])
            if candidate != "/dev/null" and current.status != "added":
                current.old_path = candidate
            continue
        if line.startswith("+++ "):
            candidate = _clean_diff_path(line[4:])
            if candidate != "/dev/null":
                current.path = candidate
            continue

        match = _HUNK_RE.match(line)
        if match:
            old_no = int(match.group("old_start"))
            new_no = int(match.group("new_start"))
            hunk = DiffHunk(header=line, old_start=old_no, new_start=new_no)
            current.hunks.append(hunk)
            continue

        if hunk is None:
            continue

        if line.startswith("\\"):
            # "\ No newline at end of file" - metadata, not a diff line.
            continue
        if line.startswith("+"):
            hunk.lines.append(DiffLine("add", line[1:], None, new_no))
            new_no += 1
        elif line.startswith("-"):
            hunk.lines.append(DiffLine("del", line[1:], old_no, None))
            old_no += 1
        elif line.startswith(" ") or line == "":
            hunk.lines.append(DiffLine("ctx", line[1:], old_no, new_no))
            old_no += 1
            new_no += 1

    if current is not None:
        files.append(current)
    return [f for f in files if f.path]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-ish glob into a path-aware regex."""
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        out.append(re.escape(char))
        index += 1

    body = "".join(out)
    if "/" not in pattern:
        # A bare pattern like `package-lock.json` matches at any depth.
        return re.compile(rf"^(?:.*/)?{body}$", re.IGNORECASE)
    return re.compile(rf"^{body}$", re.IGNORECASE)


def is_ignored(path: str, patterns: list[re.Pattern[str]]) -> bool:
    normalised = path.replace("\\", "/").lstrip("./")
    return any(pattern.match(normalised) for pattern in patterns)


def _skip_reason(diff_file: DiffFile, patterns: list[re.Pattern[str]]) -> str | None:
    path = diff_file.path
    if any(marker in f"/{path}" for marker in _HARD_SKIP_PATTERNS):
        return "vendored"
    if diff_file.is_binary:
        return "binary"
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix in _BINARY_EXTENSIONS:
        return "binary"
    if is_ignored(path, patterns):
        return "ignored"
    if diff_file.changed == 0:
        return "empty"
    if path.endswith((".min.js", ".min.css", ".map")):
        return "generated"
    return None


def render_hunk(hunk: DiffHunk) -> str:
    out = [hunk.header]
    for line in hunk.lines:
        if line.kind == "add":
            out.append(f"+ new {line.new_lineno} | {line.text}")
        elif line.kind == "del":
            out.append(f"- old {line.old_lineno} | {line.text}")
        else:
            out.append(f"  new {line.new_lineno} | {line.text}")
    return "\n".join(out)


def prepare_file(diff_file: DiffFile, max_chars: int) -> PreparedFile:
    """Render a file's hunks, dropping whole hunks that exceed the budget.

    Whole-hunk truncation keeps every advertised line number real.
    """
    header = f"FILE: {diff_file.path} ({diff_file.status}, +{diff_file.added}/-{diff_file.removed})"
    if diff_file.status == "renamed" and diff_file.old_path:
        header += f" [renamed from {diff_file.old_path}]"

    chunks: list[str] = [header]
    right: set[int] = set()
    left: set[int] = set()
    used = len(header)
    truncated = False

    for hunk in diff_file.hunks:
        rendered = render_hunk(hunk)
        if used + len(rendered) + 1 > max_chars:
            truncated = True
            break
        chunks.append(rendered)
        used += len(rendered) + 1
        for line in hunk.lines:
            if line.new_lineno is not None:
                right.add(line.new_lineno)
            if line.old_lineno is not None:
                left.add(line.old_lineno)

    if truncated:
        remaining = sum(1 for _ in diff_file.hunks) - (len(chunks) - 1)
        chunks.append(f"... [truncated: {remaining} further hunk(s) not shown]")

    return PreparedFile(
        path=diff_file.path,
        text="\n".join(chunks),
        right_lines=right,
        left_lines=left,
        truncated=truncated,
        added=diff_file.added,
        removed=diff_file.removed,
    )


def prepare_diff(
    raw: str,
    *,
    ignore_globs: list[str],
    max_file_chars: int = 12_000,
    max_total_chars: int = 60_000,
    max_files: int = 40,
) -> PreparedDiff:
    """Filter, truncate and render a diff for a single LLM call."""
    patterns = [glob_to_regex(glob) for glob in ignore_globs]
    parsed = parse_diff(raw)

    skipped: list[tuple[str, str]] = []
    candidates: list[DiffFile] = []
    for diff_file in parsed:
        reason = _skip_reason(diff_file, patterns)
        if reason:
            skipped.append((diff_file.path, reason))
        else:
            candidates.append(diff_file)

    # Review the biggest changes first, then restore original order for reading.
    ranked = sorted(candidates, key=lambda f: f.changed, reverse=True)[:max_files]
    keep_order = {id(f): index for index, f in enumerate(candidates)}
    ranked.sort(key=lambda f: keep_order[id(f)])
    omitted = [f.path for f in candidates if f not in ranked]

    prepared: list[PreparedFile] = []
    omitted.extend([])
    used = 0
    for diff_file in ranked:
        allowance = min(max_file_chars, max(2_000, max_total_chars - used))
        if allowance < 400:
            omitted.append(diff_file.path)
            continue
        rendered = prepare_file(diff_file, allowance)
        if used + len(rendered.text) + 2 > max_total_chars:
            omitted.append(diff_file.path)
            continue
        used += len(rendered.text) + 2
        prepared.append(rendered)

    body = "\n\n".join(item.text for item in prepared)
    notes: list[str] = []
    if omitted:
        notes.append(
            "OMITTED FOR SIZE (do not comment on these): " + ", ".join(sorted(set(omitted))[:50])
        )
    if skipped:
        listed = ", ".join(f"{path} ({reason})" for path, reason in skipped[:50])
        notes.append(f"FILTERED OUT (do not comment on these): {listed}")
    text = body + ("\n\n" + "\n".join(notes) if notes else "")

    return PreparedDiff(
        text=text,
        files=prepared,
        skipped=skipped,
        omitted=sorted(set(omitted)),
        total_files_seen=len(parsed),
    )


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for logging and budget math."""
    return max(1, len(text) // 4)
