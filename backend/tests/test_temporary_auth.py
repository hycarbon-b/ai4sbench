from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, update

from src.core.config import Settings
from src.core.identity import OAuthAccount, TemporaryCredential, User, UserManager, current_active_user
from src.db.models import OutboundDelivery, ReviewerApplication
from src.mailing.notifications import render_reviewer_invite
from src.main import create_app


def test_invitation_template_escapes_applicant_name() -> None:
    rendered = render_reviewer_invite(
        "Test <script>", "https://dashboard.example.test/auth/invite#tbinvite_example", "approved"
    )
    assert "Test &lt;script&gt;" in rendered.html
    assert "Test <script>" not in rendered.html
    assert "tbinvite_example" in rendered.text
    assert "Connect GitHub" in rendered.text
    assert "/auth/github/connect" not in rendered.html
    assert "/auth/github/connect" not in rendered.text


@pytest.mark.parametrize("base_url", ["https://dashboard.ai4sbench.org", "https://ai4sbench.org"])
def test_invitation_email_uses_env_base_url(tmp_path, monkeypatch, base_url: str) -> None:
    monkeypatch.setenv("TBCP_INVITE_BASE_URL", f"{base_url}/")
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'invite-base.sqlite3').as_posix()}",
        auto_create_schema=True,
        execution_mode="fake",
        smtp_enabled=True,
        smtp_server="smtp.example.test",
        smtp_from="no-reply@example.com",
    )
    assert settings.invite_base_url == f"{base_url}/"
    app = create_app(settings)
    admin = User(
        id=uuid4(), email="admin@example.com", hashed_password="unused", is_active=True,
        is_verified=True, role="admin", email_status="authorized",
    )
    recipient = User(
        id=uuid4(), email="recipient@example.com", hashed_password="unused", is_active=True,
        is_verified=False, role="member", email_status="unauthorized",
    )
    app.dependency_overrides[current_active_user] = lambda: admin

    async def seed() -> None:
        async with app.state.session_factory() as session:
            session.add(recipient)
            await session.commit()

    async def invitation_delivery() -> OutboundDelivery | None:
        async with app.state.session_factory() as session:
            return await session.scalar(
                select(OutboundDelivery).where(OutboundDelivery.event_type == "account_invite")
            )

    with TestClient(app) as client:
        asyncio.run(seed())
        request = {"user_ids": [str(recipient.id)]}
        preview = client.post("/api/v1/users/invites/preview", json=request)
        assert preview.status_code == 200, preview.text
        assert f"{base_url}/auth/invite#PREVIEW" in preview.json()["items"][0]["html"]
        sent = client.post(
            "/api/v1/users/invites/send", json=request,
            headers={"Idempotency-Key": "base-url-test"},
        )
        assert sent.status_code == 202, sent.text
        delivery = asyncio.run(invitation_delivery())
        assert delivery is not None
        assert f"{base_url}/auth/invite#tbinvite_" in delivery.payload["html"]


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
            smtp_enabled=True,
            smtp_server="smtp.example.test",
            smtp_from="no-reply@example.test",
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
        users = client.get("/api/v1/users")
        assert users.status_code == 200, users.text
        listed = next(item for item in users.json()["items"] if item["id"] == str(applicant.id))
        assert listed["name"] == "Test Reviewer"
        preview = client.post("/api/v1/users/invites/preview", json={"user_ids": [str(applicant.id)]})
        assert preview.status_code == 200, preview.text
        assert "<!doctype html>" in preview.json()["items"][0]["html"]
        assert "#PREVIEW" in preview.json()["items"][0]["html"]
        assert "tbinvite_" not in preview.text
        invite_request = {"user_ids": [str(applicant.id)]}
        invite_headers = {"Idempotency-Key": "reviewer-invite-1"}
        sent = client.post("/api/v1/users/invites/send", json=invite_request, headers=invite_headers)
        assert sent.status_code == 202, sent.text
        assert sent.json()["queued_count"] == 1
        assert client.post(
            "/api/v1/users/invites/send", json=invite_request, headers=invite_headers
        ).json() == sent.json()

        async def invitation_delivery() -> OutboundDelivery | None:
            async with app.state.session_factory() as session:
                return await session.scalar(
                    select(OutboundDelivery).where(OutboundDelivery.event_type == "account_invite")
                )

        delivery = asyncio.run(invitation_delivery())
        assert delivery is not None
        assert delivery.payload["recipients"] == [applicant.email]
        assert "tbinvite_" in delivery.payload["html"]
        previous = client.post(f"/api/v1/users/{applicant.id}/invite").json()["login_url"].split("#", 1)[1]
        invitation = client.post(f"/api/v1/users/{applicant.id}/invite")
        assert invitation.status_code == 200, invitation.text
        body = invitation.json()
        assert body["recipient"] == applicant.email
        assert "still under review" in body["html"]
        assert body["login_url"].startswith("https://ai4sbench.org/auth/invite#tbinvite_")
        assert "href=" in body["html"]

        early_token = client.post(f"/api/v1/users/{applicant.id}/api-tokens").json()["token"]

        app.dependency_overrides.clear()
        early_me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {early_token}"})
        assert early_me.status_code == 200
        assert early_me.json()["email_status"] == "unauthorized"
        assert early_me.json()["can_review"] is False
        token = body["login_url"].split("#", 1)[1]
        assert client.post("/api/v1/auth/invites/redeem", json={"token": previous}).status_code == 401
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
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {early_token}"}
        ).status_code == 401
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

        app.dependency_overrides[current_active_user] = lambda: admin
        expired = client.post(f"/api/v1/users/{applicant.id}/invite").json()["login_url"].split("#", 1)[1]
        app.dependency_overrides.clear()

        async def expire() -> None:
            async with app.state.session_factory() as session:
                await session.execute(
                    update(TemporaryCredential)
                    .where(TemporaryCredential.user_id == applicant.id, TemporaryCredential.kind == "invite")
                    .values(expires_at=int(time.time()) - 1)
                )
                await session.commit()

        asyncio.run(expire())
        assert client.post("/api/v1/auth/invites/redeem", json={"token": expired}).status_code == 401
