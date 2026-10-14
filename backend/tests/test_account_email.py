from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.testclient import TestClient
from fastapi_users import BaseUserManager
from fastapi_users.exceptions import UserNotExists
from sqlalchemy import create_engine, inspect

from src.core.config import Settings
from src.core.identity import OAuthAccount, User, UserManager, current_active_user
from src.mailing.notifications import author_email
from src.main import create_app


def sign_in_as(app, user_id) -> None:
    async def resolve(request: Request) -> User:
        async with request.app.state.session_factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            return user

    app.dependency_overrides[current_active_user] = resolve


def test_first_oauth_callback_marks_email_for_review(monkeypatch) -> None:
    user = User(id=uuid4(), email="github@example.test", role="member", github_email_review_pending=False)
    user.oauth_accounts = [OAuthAccount(
        user_id=user.id, oauth_name="github", account_id="gh-1",
        account_email=user.email, access_token="secret",
    )]
    database = Mock()
    database.update = AsyncMock(return_value=user)
    manager = UserManager(database, Settings(environment="test", execution_mode="fake"))
    manager.get_by_oauth_account = AsyncMock(side_effect=UserNotExists())
    monkeypatch.setattr(BaseUserManager, "oauth_callback", AsyncMock(return_value=user))
    client = Mock()
    client.get = AsyncMock(return_value=Mock(json=lambda: {"login": "example"}))
    context = Mock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("src.core.identity.httpx.AsyncClient", Mock(return_value=context))

    asyncio.run(manager.oauth_callback("github", "token", "gh-1", user.email))
    assert database.update.await_args.args[1]["github_email_review_pending"] is True


def test_first_explicit_github_binding_marks_email_for_review(monkeypatch) -> None:
    user = User(id=uuid4(), email="invite@example.test", role="member", github_email_review_pending=False)
    user.oauth_accounts = []
    database = Mock()
    database.update = AsyncMock(return_value=user)
    database.session.scalar = AsyncMock(return_value=None)
    manager = UserManager(database, Settings(environment="test", execution_mode="fake"))
    manager.get_by_oauth_account = AsyncMock(side_effect=UserNotExists())
    monkeypatch.setattr(BaseUserManager, "oauth_associate_callback", AsyncMock(return_value=user))
    client = Mock()
    client.get = AsyncMock(return_value=Mock(json=lambda: {"login": "example"}))
    context = Mock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("src.core.identity.httpx.AsyncClient", Mock(return_value=context))

    asyncio.run(manager.oauth_associate_callback(user, "github", "token", "gh-1", "github@example.test"))
    assert database.update.await_args.args[1]["github_email_review_pending"] is True


def test_email_review_migration_keeps_existing_accounts_unprompted(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.sqlite3'}"
    monkeypatch.setenv("TBCP_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "20261015_0024")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert "github_email_review_pending" in {
            column["name"] for column in inspect(engine).get_columns("user")
        }
        assert "email_preference_set" in {
            column["name"] for column in inspect(engine).get_columns("user")
        }
        command.downgrade(config, "20261015_0024")
        assert "github_email_review_pending" not in {
            column["name"] for column in inspect(engine).get_columns("user")
        }
        assert "email_preference_set" not in {
            column["name"] for column in inspect(engine).get_columns("user")
        }
    finally:
        engine.dispose()


def test_first_github_email_choice_and_unverified_address(tmp_path, monkeypatch) -> None:
    app = create_app(Settings(
        environment="test", database_url=f"sqlite:///{(tmp_path / 'control.sqlite3').as_posix()}",
        auto_create_schema=True, execution_mode="fake",
    ))
    user = User(
        id=uuid4(), email="invite@example.test", hashed_password="unused", is_active=True,
        is_verified=True, role="member", email_status="authorized", github_login="example",
        github_email_review_pending=True,
    )
    sign_in_as(app, user.id)
    with TestClient(app) as client:
        async def seed() -> None:
            async with app.state.session_factory() as session:
                session.add(user)
                session.add(OAuthAccount(
                    user_id=user.id, oauth_name="github", account_id="gh-1",
                    account_email="github@example.test", access_token="secret",
                ))
                await session.commit()

        asyncio.run(seed())
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["github_email"] == "github@example.test"
        assert me.json()["github_email_review_pending"] is True
        assert client.post("/api/v1/auth/email/choice", json={
            "choice": "change", "email": "other@example.test",
        }).status_code == 403
        assert client.post("/api/v1/auth/email/choice", json={
            "choice": "change", "email": "github@example.test",
        }).status_code == 204
        me = client.get("/api/v1/auth/me").json()
        assert me["email"] == "github@example.test"
        assert me["email_status"] == "from_github_oauth"
        assert me["github_email_review_pending"] is False
        assert client.post("/api/v1/auth/email/choice", json={"choice": "keep"}).status_code == 409


def test_custom_email_requires_scoped_proof(tmp_path, monkeypatch) -> None:
    app = create_app(Settings(
        environment="test", database_url=f"sqlite:///{(tmp_path / 'control.sqlite3').as_posix()}",
        auto_create_schema=True, execution_mode="fake", smtp_enabled=True,
        smtp_server="smtp.example.test", smtp_from="no-reply@example.test",
    ))
    user = User(
        id=uuid4(), email="invite@example.test", hashed_password="unused", is_active=True,
        is_verified=True, role="member", email_status="authorized", github_login="example",
        github_email_review_pending=True,
    )
    sign_in_as(app, user.id)
    sent = []

    async def fake_send(_settings, email, code, _purpose) -> None:
        sent.append((email, code))

    monkeypatch.setattr("src.mailing.verification.send_verification_code", fake_send)
    with TestClient(app) as client:
        async def seed() -> None:
            async with app.state.session_factory() as session:
                session.add(user)
                session.add(OAuthAccount(
                    user_id=user.id, oauth_name="github", account_id="gh-1",
                    account_email="github@example.test", access_token="secret",
                ))
                await session.commit()

        asyncio.run(seed())
        issued = client.post("/api/v1/auth/email/code", json={"email": "other@example.test"})
        assert issued.status_code == 202, issued.text
        assert sent[0][0] == "other@example.test"
        proof = client.post("/api/v1/auth/email/code/verify", json={
            "email": "other@example.test", "challenge_id": issued.json()["challenge_id"], "code": sent[0][1],
        })
        assert proof.status_code == 200, proof.text
        changed = client.post("/api/v1/auth/email/choice", json={
            "choice": "change", "email": "other@example.test",
            "verification_token": proof.json()["verification_token"],
        })
        assert changed.status_code == 204
        me = client.get("/api/v1/auth/me").json()
        assert me["email"] == "other@example.test"
        assert me["email_status"] == "authorized"

        async def recipient() -> str | None:
            async with app.state.session_factory() as session:
                return await author_email(session, str(user.id))

        assert asyncio.run(recipient()) == "other@example.test"


def test_another_accounts_email_cannot_be_claimed(tmp_path) -> None:
    app = create_app(Settings(
        environment="test", database_url=f"sqlite:///{(tmp_path / 'control.sqlite3').as_posix()}",
        auto_create_schema=True, execution_mode="fake",
    ))
    user = User(
        id=uuid4(), email="invite@example.test", hashed_password="unused", is_active=True,
        is_verified=True, role="member", github_email_review_pending=True,
    )
    owner = User(
        id=uuid4(), email="other@example.test", hashed_password="unused", is_active=True,
        is_verified=True, role="member",
    )
    sign_in_as(app, user.id)
    with TestClient(app) as client:
        async def seed() -> None:
            async with app.state.session_factory() as session:
                session.add_all([user, owner])
                session.add(OAuthAccount(
                    user_id=user.id, oauth_name="github", account_id="gh-1",
                    account_email="github@example.test", access_token="secret",
                ))
                await session.commit()

        asyncio.run(seed())
        response = client.post("/api/v1/auth/email/choice", json={
            "choice": "change", "email": "other@example.test",
        })
        assert response.status_code == 409
        assert client.get("/api/v1/auth/me").json()["github_email_review_pending"] is True
