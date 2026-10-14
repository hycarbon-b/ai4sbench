from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

from src.core.config import Settings
from src.core.identity import OAuthAccount, User, UserManager, current_active_user
from src.db.models import ReviewerApplication
from src.main import create_app


def test_github_binding_rejects_an_identity_owned_by_another_user() -> None:
    current = User(id=uuid4(), email="current@example.test", role="member")
    current.oauth_accounts = []
    owner = User(id=uuid4(), email="owner@example.test", role="member")
    manager = UserManager(Mock(), Settings(environment="test", execution_mode="fake"))
    manager.get_by_oauth_account = AsyncMock(return_value=owner)

    async def attempt() -> None:
        await manager.oauth_associate_callback(current, "github", "token", "external-id", current.email)

    with pytest.raises(HTTPException) as error:
        asyncio.run(attempt())
    assert error.value.status_code == 409


def test_github_binding_rejects_a_second_identity_on_one_account() -> None:
    current = User(id=uuid4(), email="current@example.test", role="member")
    current.oauth_accounts = [
        OAuthAccount(
            user_id=current.id, oauth_name="github", account_id="old-external-id",
            account_email=current.email, access_token="old-token",
        )
    ]
    manager = UserManager(Mock(), Settings(environment="test", execution_mode="fake"))

    async def attempt() -> None:
        await manager.oauth_associate_callback(current, "github", "token", "new-external-id", current.email)

    with pytest.raises(HTTPException) as error:
        asyncio.run(attempt())
    assert error.value.status_code == 409


def test_temporary_credentials_migration_from_previous_revision(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.sqlite3'}"
    monkeypatch.setenv("TBCP_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "20261013_0022")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "temporary_credentials" in inspector.get_table_names()
        assert "user_id" in {column["name"] for column in inspector.get_columns("reviewer_applications")}
        assert "email_status" in {column["name"] for column in inspector.get_columns("user")}
        command.downgrade(config, "20261013_0022")
        assert "temporary_credentials" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_invite_cookie_bearer_and_reviewer_permission(tmp_path) -> None:
    app = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite:///{(tmp_path / 'control.sqlite3').as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
        )
    )
    admin = User(
        id=uuid4(), email="admin@example.test", hashed_password="unused", is_active=True,
        is_verified=True, role="admin", email_status="authorized",
    )
    applicant = User(
        id=uuid4(), email="reviewer@example.test", hashed_password="unused", is_active=True,
        is_verified=False, role="member", email_status="unauthorized",
    )
    app.dependency_overrides[current_active_user] = lambda: admin

    with TestClient(app) as client:
        async def seed() -> None:
            async with app.state.session_factory() as session:
                applicant.oauth_accounts = []
                session.add(applicant)
                session.add(ReviewerApplication(
                    schema_version="tb-reviewer-application/v1", name="Test Reviewer",
                    affiliation="Example", email=applicant.email, github=None,
                    domains=[], domains_display=[], research_background="Test background",
                    status="pending", user_id=applicant.id,
                ))
                await session.commit()

        asyncio.run(seed())
        invitation = client.post(f"/api/v1/users/{applicant.id}/invite")
        assert invitation.status_code == 200, invitation.text
        body = invitation.json()
        assert body["recipient"] == applicant.email
        assert "still under review" in body["html"]
        assert body["login_url"].startswith("http://testserver/auth/invite#tbinvite_")
        assert "href=" in body["html"]

        app.dependency_overrides.clear()
        token = body["login_url"].split("#", 1)[1]
        assert client.post("/api/v1/auth/invites/redeem", json={"token": token}).status_code == 204
        assert client.post("/api/v1/auth/invites/redeem", json={"token": token}).status_code == 401
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["email_status"] == "authorized"
        assert me.json()["can_review"] is False
        assert me.json()["github_linked"] is False

        app.dependency_overrides[current_active_user] = lambda: admin
        api_token = client.post(f"/api/v1/users/{applicant.id}/api-tokens").json()["token"]
        app.dependency_overrides.clear()
        client.cookies.clear()
        bearer = {"Authorization": f"Bearer {api_token}"}
        assert client.get("/api/v1/auth/me", headers=bearer).json()["id"] == str(applicant.id)
        assert client.get("/api/v1/auth/me").status_code == 401

        async def approve() -> None:
            async with app.state.session_factory() as session:
                item = (await session.scalars(select(ReviewerApplication))).first()
                item.status = "approved"
                await session.commit()

        asyncio.run(approve())
        assert client.get("/api/v1/auth/me", headers=bearer).json()["can_review"] is True
        app.dependency_overrides[current_active_user] = lambda: admin
        assert client.delete(f"/api/v1/users/{applicant.id}/api-tokens").status_code == 204
        app.dependency_overrides.clear()
        assert client.get("/api/v1/auth/me", headers=bearer).status_code == 401
