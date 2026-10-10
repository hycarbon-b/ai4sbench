from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.core.config import Settings
from src.core.identity import OAuthAccount, User, current_active_user
from src.db.models import OutboundDelivery, Proposal, ReviewerApplication
from src.jobs.runner import JobRunner
from src.main import create_app

URL = "/api/v1/admin-mail"


def user(login: str, role: str = "member") -> User:
    return User(
        id=uuid4(),
        email=f"old-{login}@example.test",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        role=role,
        github_login=login,
    )


async def seed(factory, author: User, reviewer: User, unlinked: User) -> None:
    async with factory() as session:
        session.add_all([author, reviewer, unlinked])
        await session.flush()
        for index, person in enumerate((author, reviewer), start=1):
            session.add(OAuthAccount(
                user_id=person.id,
                oauth_name="github",
                access_token="fake-oauth-token",
                account_id=str(index),
                account_email=f"oauth-{person.github_login}@example.com",
            ))
        session.add(Proposal(
            author_id=str(author.id),
            author_login=author.github_login,
            title="An active proposal",
            abstract="Local test proposal",
            domain="Physics",
            field="Materials",
            task_slug="test-task",
            evidence="Local test evidence",
        ))
        session.add(ReviewerApplication(
            schema_version="tb-reviewer-application/v1",
            name="Reviewer",
            affiliation="Example Institute",
            email="form-address@example.test",
            github=reviewer.github_login,
            submitted_by_login=reviewer.github_login,
            research_background="Scientific review background",
            status="approved",
        ))
        session.add(ReviewerApplication(
            schema_version="tb-reviewer-application/v1",
            name="Author who also applied",
            affiliation="Example Institute",
            email="author-form@example.test",
            github=author.github_login,
            submitted_by_login=author.github_login,
            research_background="Scientific review background",
            status="pending",
        ))
        session.add(ReviewerApplication(
            schema_version="tb-reviewer-application/v1",
            name="Unlinked applicant",
            affiliation="Example Institute",
            email="unlinked-form@example.test",
            github=unlinked.github_login,
            submitted_by_login=None,
            research_background="Scientific review background",
            status="pending",
        ))
        await session.commit()


async def mail_rows(factory) -> list[OutboundDelivery]:
    async with factory() as session:
        return list((await session.scalars(
            select(OutboundDelivery).where(OutboundDelivery.event_type == "admin_bulk_email")
        )).all())


async def use_same_oauth_email(factory, reviewer_id) -> None:
    async with factory() as session:
        account = await session.scalar(select(OAuthAccount).where(OAuthAccount.user_id == reviewer_id))
        assert account is not None
        account.account_email = "oauth-author@example.com"
        await session.commit()


def test_admin_mail_selects_oauth_addresses_and_queues_private_deliveries(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'admin-mail.sqlite'}",
        auto_create_schema=True,
        allowed_hosts=("testserver",),
        execution_mode="fake",
        smtp_enabled=True,
        smtp_server="smtp.example.test",
        smtp_password="smtp-secret",
        smtp_from="no-reply@example.com",
    )
    app = create_app(settings)
    admin = user("admin", "admin")
    author = user("author")
    reviewer = user("reviewer")
    unlinked = user("unlinked")
    active = {"user": admin}
    app.dependency_overrides[current_active_user] = lambda: active["user"]
    with TestClient(app) as client:
        client.portal.call(seed, app.state.session_factory, author, reviewer, unlinked)
        active["user"] = author
        assert client.get(f"{URL}/recipients").status_code == 403
        assert client.post(f"{URL}/deliveries", json={}).status_code == 403
        active["user"] = admin

        listed = client.get(f"{URL}/recipients")
        assert listed.status_code == 200, listed.text
        items = {item["github_login"]: item for item in listed.json()["items"]}
        assert set(items) == {"author", "reviewer"}
        assert items["author"]["proposal_count"] == 1
        assert items["author"]["reviewer_statuses"] == ["pending"]
        assert items["reviewer"]["reviewer_statuses"] == ["approved"]
        assert items["reviewer"]["email"] == "oauth-reviewer@example.com"
        assert "form-address@example.test" not in listed.text
        assert "old-reviewer@example.test" not in listed.text

        body = {
            "recipient_ids": [str(author.id), str(reviewer.id)],
            "subject": "News <today>",
            "body": "Hello <script>alert(1)</script>\nSecond line",
            "signature": "Research & Operations",
        }
        headers = {"Idempotency-Key": "dashboard-send-1"}
        assert client.post(f"{URL}/deliveries", json=body).status_code == 422
        unknown = client.post(
            f"{URL}/deliveries", headers=headers, json={**body, "recipient_ids": [str(unlinked.id)]}
        )
        assert unknown.status_code == 422
        duplicate = client.post(
            f"{URL}/deliveries", headers=headers, json={**body, "recipient_ids": [str(author.id)] * 2}
        )
        assert duplicate.status_code == 422
        created = client.post(f"{URL}/deliveries", headers=headers, json=body)
        assert created.status_code == 202, created.text
        assert created.json()["queued_count"] == 2
        assert "smtp-secret" not in created.text
        assert "Hello" not in created.text
        assert client.post(f"{URL}/deliveries", headers=headers, json=body).json() == created.json()
        changed = client.post(f"{URL}/deliveries", headers=headers, json={**body, "subject": "Changed"})
        assert changed.status_code == 409

        rows = client.portal.call(mail_rows, app.state.session_factory)
        assert len(rows) == 2
        assert {tuple(item.payload["recipients"]) for item in rows} == {
            ("oauth-author@example.com",), ("oauth-reviewer@example.com",)
        }
        for row in rows:
            assert row.state == "pending"
            assert row.payload["reply_to"] == ["contact@ai4sbench.org"]
            assert "Hello <script>alert(1)</script>" in row.payload["text"]
            assert "&lt;script&gt;" in row.payload["html"]
            assert "<script>" not in row.payload["html"]
            assert "Research &amp; Operations" in row.payload["html"]
            assert "smtp-secret" not in str(row.payload)

        runner = JobRunner(settings)
        try:
            with patch("src.community.deliveries.FastMail") as fast_mail:
                fast_mail.return_value.send_message = AsyncMock()
                assert client.portal.call(runner.run_once) is True
                message = fast_mail.return_value.send_message.await_args.args[0]
                assert message.multipart_subtype.value == "alternative"
                assert "Research &amp; Operations" in message.alternative_body
        finally:
            client.portal.call(runner.engine.dispose)

        client.portal.call(use_same_oauth_email, app.state.session_factory, reviewer.id)
        deduped = client.post(
            f"{URL}/deliveries", headers={"Idempotency-Key": "dashboard-send-2"}, json=body
        )
        assert deduped.status_code == 202, deduped.text
        assert deduped.json()["queued_count"] == 1
        assert len(client.portal.call(mail_rows, app.state.session_factory)) == 3


def test_admin_mail_requires_smtp(tmp_path) -> None:
    app = create_app(Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'no-smtp.sqlite'}",
        auto_create_schema=True,
        execution_mode="fake",
    ))
    app.dependency_overrides[current_active_user] = lambda: user("admin", "admin")
    with TestClient(app) as client:
        response = client.post(
            f"{URL}/deliveries",
            headers={"Idempotency-Key": "no-smtp"},
            json={
                "recipient_ids": [str(uuid4())],
                "subject": "Subject",
                "body": "Body",
                "signature": "Team",
            },
        )
        assert response.status_code == 503
