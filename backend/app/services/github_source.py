"""Repository access for AGENT-mode tools, backed by the GitHub API."""

from __future__ import annotations

from app.services.github import GitHubClient, GitHubError


class GitHubFileReader:
    """Implements ``app.mcp.context.FileReader`` over the contents API."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        owner: str,
        repo: str,
        head_ref: str,
        base_ref: str = "",
    ) -> None:
        self._client = client
        self._owner = owner
        self._repo = repo
        self._refs = {
            "head": head_ref,
            "base": base_ref or head_ref,
            "pr": head_ref,
        }
        self._default = head_ref

    async def read_file(self, path: str, ref: str, start_line: int, end_line: int) -> str:
        resolved = self._refs.get(ref.strip().lower(), ref or self._default)
        try:
            content = await self._client.read_file(self._owner, self._repo, path, resolved)
        except GitHubError as exc:
            raise FileNotFoundError(f"{path} at {resolved}: {exc.message}") from exc

        lines = content.splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), max(start, int(end_line)))
        if start > len(lines):
            return f"(file {path} has only {len(lines)} lines)"
        return "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(start, end + 1))
