"""GitHub App authentication and REST client.

Flow: sign a short-lived RS256 JWT as the App, exchange it for a per-installation
access token, cache that token until shortly before it expires, then call the API
with least-privilege scopes (Pull requests: write, Contents: read).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt

from app.config import Settings

logger = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
_DIFF_ACCEPT = "application/vnd.github.v3.diff"
_RAW_ACCEPT = "application/vnd.github.raw"


class GitHubError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.body = body


def build_app_jwt(app_id: str, private_key: str, *, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)}
    return jwt.encode(payload, private_key, algorithm="RS256")


class InstallationTokenCache:
    """In-memory token cache. Render restarts simply re-mint."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] - 300 > time.time():
            return cached[0]

        async with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - 300 > time.time():
                return cached[0]

            private_key = self._settings.github_private_key
            if not private_key:
                raise GitHubError("GITHUB_PRIVATE_KEY_B64 is not configured")
            if not self._settings.github_app_id:
                raise GitHubError("GITHUB_APP_ID is not configured")

            token_jwt = build_app_jwt(self._settings.github_app_id, private_key)
            response = await self._client.post(
                f"{self._settings.github_api_base}/app/installations/{installation_id}"
                "/access_tokens",
                headers={
                    "Authorization": f"Bearer {token_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": _API_VERSION,
                    "User-Agent": self._settings.github_user_agent,
                },
            )
            if response.status_code != 201:
                raise GitHubError(
                    f"Could not mint an installation token (HTTP {response.status_code}).",
                    status_code=response.status_code,
                    retryable=response.status_code >= 500 or response.status_code == 429,
                )
            payload = response.json()
            token = str(payload["token"])
            expires_raw = str(payload.get("expires_at", ""))
            try:
                expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                expires = time.time() + 3000
            self._tokens[installation_id] = (token, expires)
            logger.info("minted installation token for %s", installation_id)
            return token

    def invalidate(self, installation_id: int) -> None:
        self._tokens.pop(installation_id, None)


class GitHubClient:
    """Thin, typed wrapper over the endpoints DiffWitch needs."""

    def __init__(
        self,
        token: str,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": self._settings.github_user_agent,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        accept: str = "application/vnd.github+json",
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self._settings.github_api_base}{path}"
        try:
            response = await self._client.request(
                method,
                url,
                headers=self._headers(accept),
                json=json_body,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(
                f"GitHub request failed ({type(exc).__name__}).", retryable=True
            ) from exc

        if response.status_code >= 400:
            message = _extract_error(response)
            retryable = response.status_code >= 500 or response.status_code == 429
            if response.status_code == 403 and "rate limit" in message.lower():
                retryable = True
            raise GitHubError(
                f"GitHub {method} {path} failed: {message}",
                status_code=response.status_code,
                retryable=retryable,
                body=_safe_json(response),
            )
        return response

    async def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        response = await self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return response.json()

    async def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        response = await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}", accept=_DIFF_ACCEPT
        )
        return response.text

    async def list_pull_files(
        self, owner: str, repo: str, number: int, *, limit: int = 300
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            params={"per_page": min(100, limit)},
        )
        return response.json()

    async def read_file(
        self, owner: str, repo: str, path: str, ref: str, *, max_bytes: int = 400_000
    ) -> str:
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
            accept=_RAW_ACCEPT,
            params={"ref": ref},
        )
        content = response.content[:max_bytes]
        if b"\x00" in content[:1024]:
            raise GitHubError(f"{path} looks binary.")
        return content.decode("utf-8", errors="replace")

    async def list_review_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}/comments",
            params={"per_page": 100},
        )
        return response.json()

    async def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        body: str,
        comments: list[dict[str, Any]],
        commit_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"body": body, "event": "COMMENT", "comments": comments}
        if commit_id:
            payload["commit_id"] = commit_id
        response = await self._request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", json_body=payload
        )
        return response.json()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class GitHubApp:
    """Factory that hands out clients for any installation."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._tokens = InstallationTokenCache(settings, self._client)

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.github_app_id
            and self._settings.github_private_key
            and self._settings.github_webhook_secret
        )

    async def client_for_installation(self, installation_id: int) -> GitHubClient:
        token = await self._tokens.get(installation_id)
        return GitHubClient(token, self._settings, client=self._client)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_error(response: httpx.Response) -> str:
    payload = _safe_json(response)
    if isinstance(payload, dict):
        message = payload.get("message")
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            details = ", ".join(str(item.get("message", item)) for item in errors[:3])
            return f"{message} ({details})" if message else details
        if message:
            return str(message)
    return response.text[:300] or f"HTTP {response.status_code}"


def decode_base64_file(payload: dict[str, Any]) -> str:
    """Decode a contents-API payload (used by tests and fallbacks)."""
    content = str(payload.get("content", "")).replace("\n", "")
    return base64.b64decode(content).decode("utf-8", errors="replace")


def utcnow() -> datetime:
    return datetime.now(UTC)
