"""Repository-scoped GitHub App credentials, cached only in process memory."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import httpx
import jwt

from src.core.config import Settings


class GitHubAppError(RuntimeError):
    def __init__(self, message: str, *, terminal: bool = False, response_status: int | None = None):
        super().__init__(message)
        self.terminal = terminal
        self.response_status = response_status


def app_requested(settings: Settings) -> bool:
    return bool(
        settings.ai_review_github_app_id
        or settings.ai_review_github_app_installation_id
        or settings.ai_review_github_app_private_key_path
    )


def app_configured(settings: Settings) -> bool:
    return bool(
        settings.ai_review_github_app_id
        and settings.ai_review_github_app_installation_id
        and settings.ai_review_github_app_private_key_path
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", settings.github_repository)
    )


class GitHubAppAuth:
    def __init__(self, issuer: str, installation_id: int, key_path: Path, repository: str):
        self.issuer = issuer
        self.installation_id = installation_id
        self.key_path = key_path
        self.repository = repository
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)
        self._login = ""
        self._lock = asyncio.Lock()

    def invalidate(self, rejected_token: str) -> None:
        # A concurrent request might already have replaced the rejected token.
        if self._token == rejected_token:
            self._token = None

    async def login(self) -> str:
        await self.token()
        return self._login

    async def token(self) -> str:
        async with self._lock:
            current = datetime.now(UTC)
            if self._token and self._expires_at > current + timedelta(minutes=2):
                return self._token
            try:
                # Read on refresh so a rotated key file is used without a restart.
                encoded = jwt.encode(
                    {
                        "iat": int(current.timestamp()) - 60,
                        "exp": int(current.timestamp()) + 540,
                        "iss": self.issuer,
                    },
                    self.key_path.read_text(encoding="utf-8"),
                    algorithm="RS256",
                )
            except (OSError, ValueError, TypeError, jwt.PyJWTError):
                raise GitHubAppError("GitHub App private key could not sign a JWT", terminal=True) from None
            headers = {
                "Authorization": f"Bearer {encoded}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            }
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    app = await client.get("https://api.github.com/app", headers=headers)
                    self._check_response(app)
                    slug = app.json().get("slug")
                    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]+", slug):
                        raise ValueError("Invalid app slug")
                    result = await client.post(
                        f"https://api.github.com/app/installations/{self.installation_id}/access_tokens",
                        headers=headers,
                        json={
                            "repositories": [self.repository.split("/", 1)[1]],
                            "permissions": {"discussions": "write"},
                        },
                    )
                    self._check_response(result)
                    value = result.json()
                token = value.get("token")
                expires_at = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
                if not isinstance(token, str) or not token or not expires_at.tzinfo or expires_at <= current:
                    raise ValueError("Invalid token response")
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                # Never retain provider response bodies, JWTs, private keys or tokens in errors.
                raise GitHubAppError("GitHub App authentication failed or returned invalid JSON") from None
            self._token, self._expires_at, self._login = token, expires_at, f"{slug}[bot]"
            return token

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if not response.is_success:
            raise GitHubAppError(
                "GitHub App authentication request failed",
                terminal=response.status_code in {401, 403, 404, 422},
                response_status=response.status_code,
            )


@lru_cache(maxsize=8)
def _auth(issuer: str, installation_id: int, key_path: Path, repository: str) -> GitHubAppAuth:
    return GitHubAppAuth(issuer, installation_id, key_path, repository)


def github_app_auth(settings: Settings) -> GitHubAppAuth | None:
    if not app_requested(settings):
        return None
    if not app_configured(settings):
        raise GitHubAppError("GitHub App publishing configuration is incomplete", terminal=True)
    return _auth(
        settings.ai_review_github_app_id,
        settings.ai_review_github_app_installation_id,
        settings.ai_review_github_app_private_key_path,
        settings.github_repository,
    )
