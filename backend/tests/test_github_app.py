from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.community.ai_reviews import PublicationError, github_request, require_publish_config
from src.community.github_app import GitHubAppAuth, GitHubAppError, github_app_auth
from src.core.config import Settings


@pytest.fixture
def app_settings(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return Settings(
        _env_file=None,
        github_repository="example/tasks",
        ai_review_github_token="unused-pat",
        ai_review_github_app_id="12345",
        ai_review_github_app_installation_id=678,
        ai_review_github_app_private_key_path=path,
        discord_webhook_url="https://discord.example/webhook",
    ), key.public_key()


def mock_github(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "src.community.github_app.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_scoped_rs256_tokens_are_cached_and_refreshed(app_settings, monkeypatch):
    settings, public_key = app_settings
    minted = []

    def handler(request):
        claims = jwt.decode(
            request.headers["Authorization"].removeprefix("Bearer "),
            public_key,
            algorithms=["RS256"],
            issuer="12345",
        )
        assert 0 < claims["exp"] - datetime.now(UTC).timestamp() <= 600
        assert claims["iat"] < datetime.now(UTC).timestamp()
        if request.url.path == "/app":
            return httpx.Response(200, json={"slug": "ai4s-review"})
        assert request.url.path == "/app/installations/678/access_tokens"
        import json

        assert json.loads(request.content) == {
            "repositories": ["tasks"],
            "permissions": {"discussions": "write"},
        }
        token = f"synthetic-installation-{len(minted)}"
        minted.append(token)
        return httpx.Response(
            201, json={"token": token, "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        )

    mock_github(monkeypatch, handler)

    async def scenario():
        app = github_app_auth(settings)
        tokens = await asyncio.gather(*(app.token() for _ in range(10)))
        assert len(set(tokens)) == 1 and len(minted) == 1
        assert await app.login() == "ai4s-review[bot]"
        app._expires_at = datetime.now(UTC) + timedelta(seconds=60)
        fresh = await app.token()
        assert fresh != tokens[0] and len(minted) == 2
        app.invalidate(tokens[0])
        assert await app.token() == fresh
        app.invalidate(fresh)
        assert await app.token() != fresh and len(minted) == 3

    asyncio.run(scenario())


def test_graphql_uses_app_and_retries_401_once_without_pat(app_settings, monkeypatch):
    settings, _ = app_settings
    minted = 0
    graph_tokens = []

    def handler(request):
        nonlocal minted
        if request.url.path == "/app":
            return httpx.Response(200, json={"slug": "ai4s-review"})
        if request.url.path.endswith("/access_tokens"):
            minted += 1
            return httpx.Response(
                201,
                json={
                    "token": f"synthetic-{minted}",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        graph_tokens.append(request.headers["Authorization"])
        if len(graph_tokens) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"data": {"ok": True}})

    mock_github(monkeypatch, handler)
    assert asyncio.run(github_request(settings, "query Test { viewer { login } }", {})) == {"ok": True}
    assert graph_tokens == ["Bearer synthetic-1", "Bearer synthetic-2"]
    assert minted == 2


def test_partial_app_config_never_falls_back_to_pat():
    settings = Settings(
        _env_file=None,
        github_repository="example/tasks",
        ai_review_github_token="unused-pat",
        ai_review_github_app_id="12345",
        discord_webhook_url="https://discord.example/webhook",
    )
    with pytest.raises(GitHubAppError, match="incomplete"):
        github_app_auth(settings)
    with pytest.raises(PublicationError, match="incomplete"):
        asyncio.run(github_request(settings, "query Test { viewer { login } }", {}))
    with pytest.raises(HTTPException) as error:
        require_publish_config(settings)
    assert error.value.status_code == 503


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500, 302])
def test_authentication_failures_do_not_expose_remote_secrets(app_settings, monkeypatch, status):
    settings, _ = app_settings
    mock_github(monkeypatch, lambda request: httpx.Response(status, text="remote-secret-value"))
    with pytest.raises(GitHubAppError) as error:
        asyncio.run(github_app_auth(settings).token())
    assert "remote-secret-value" not in str(error.value)
    assert error.value.response_status == status
    assert error.value.terminal == (status in {401, 403, 404, 422})


def test_invalid_private_key_has_sanitized_terminal_error(tmp_path):
    path = tmp_path / "bad.pem"
    path.write_text("secret-invalid-key")
    app = GitHubAppAuth("12345", 678, path, "example/tasks")
    with pytest.raises(GitHubAppError) as error:
        asyncio.run(app.token())
    assert error.value.terminal
    assert "secret-invalid-key" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"token": "secret", "expires_at": "bad"},
        {"token": "", "expires_at": "2099-01-01T00:00:00Z"},
        {"token": "secret", "expires_at": "2000-01-01T00:00:00Z"},
        {"token": "secret", "expires_at": "2099-01-01T00:00:00"},
    ],
)
def test_malformed_token_response_is_rejected(app_settings, monkeypatch, value):
    settings, _ = app_settings

    def handler(request):
        if request.url.path == "/app":
            return httpx.Response(200, json={"slug": "ai4s-review"})
        return httpx.Response(201, json=value)

    mock_github(monkeypatch, handler)
    with pytest.raises(GitHubAppError, match="invalid JSON"):
        asyncio.run(github_app_auth(settings).token())


def test_app_configuration_does_not_require_a_pat(app_settings):
    settings, _ = app_settings
    settings.ai_review_github_token = None
    require_publish_config(settings)


def test_empty_optional_app_environment_fields_remain_disabled(monkeypatch):
    monkeypatch.setenv("TBCP_AI_REVIEW_GITHUB_APP_ID", "")
    monkeypatch.setenv("TBCP_AI_REVIEW_GITHUB_APP_INSTALLATION_ID", "")
    monkeypatch.setenv("TBCP_AI_REVIEW_GITHUB_APP_PRIVATE_KEY_PATH", "")
    settings = Settings(_env_file=None)
    assert settings.ai_review_github_app_installation_id is None
    assert settings.ai_review_github_app_private_key_path is None
    assert github_app_auth(settings) is None
