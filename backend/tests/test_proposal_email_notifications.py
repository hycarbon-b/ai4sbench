from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_community import proposal_payload, review_payload

from src.core.config import Settings
from src.core.identity import OAuthAccount, User, current_active_user
from src.db.models import OutboundDelivery, Proposal
from src.jobs.runner import JobRunner
from src.mailing.notifications import author_email, enqueue_author_notification, render_notification
from src.main import create_app


def test_notification_templates_escape_title_and_keep_review_generic() -> None:
    title = 'A <study> & "results"'
    url = "https://ai4sbench.org/tasks/task.html?id=proposal-1"
    for kind in ("submit_proposal", "review_update"):
        rendered = render_notification(kind, title, url)
        assert rendered.subject.endswith(title)
        assert title in rendered.text
        assert url in rendered.text
        assert "contact@ai4sbench.org" in rendered.text
        assert "\n" not in rendered.text
        assert "A &lt;study&gt; &amp;" in rendered.html
        assert title not in rendered.html
        assert f'href="{url}"' in rendered.html
        assert rendered.html.count("<p ") == 1
        assert "#0D1730" in rendered.html
        assert "#2474FF" in rendered.html
        assert "<img" not in rendered.html
    review = render_notification("review_update", title, url)
    assert "approved" not in review.text.lower()
    assert "rejected" not in review.html.lower()


async def seed_author(factory, author: User) -> None:
    async with factory() as session:
        session.add(author)
        await session.flush()
        session.add(
            OAuthAccount(
                user_id=author.id,
                oauth_name="github",
                access_token="not-a-real-token",
                account_id="101",
                account_email="new-oauth@example.com",
            )
        )
        await session.commit()


async def mail_rows(factory) -> list[OutboundDelivery]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(OutboundDelivery)
                .where(OutboundDelivery.delivery_type == "smtp")
                .order_by(OutboundDelivery.created_at)
            )
        )


async def duplicate_and_skip(factory, settings: Settings, proposal_id: str) -> None:
    async with factory() as session:
        proposal = await session.get(Proposal, proposal_id)
        assert proposal is not None
        first = await enqueue_author_notification(session, settings, proposal, "submit_proposal")
        second = await enqueue_author_notification(session, settings, proposal, "submit_proposal")
        assert first is not None and second is not None and first.id == second.id
        first_review = await enqueue_author_notification(session, settings, proposal, "review_update")
        second_review = await enqueue_author_notification(session, settings, proposal, "review_update")
        assert first_review is not None and second_review is not None
        assert first_review.id == second_review.id
        await session.commit()
    disabled = settings.model_copy(update={"smtp_enabled": False})
    async with factory() as session:
        proposal = await session.get(Proposal, proposal_id)
        assert proposal is not None
        assert await enqueue_author_notification(session, disabled, proposal, "submit_proposal") is None
        proposal.author_id = str(uuid4())
        assert await enqueue_author_notification(session, settings, proposal, "submit_proposal") is None
        await session.rollback()


async def retry_ready(factory, delivery_id: str) -> None:
    async with factory() as session:
        delivery = await session.get(OutboundDelivery, delivery_id)
        assert delivery is not None
        delivery.available_at = datetime.now(UTC)
        delivery.max_attempts = 2
        await session.commit()


async def check_recipient_choice(factory, author: User) -> None:
    async with factory() as session:
        account = await session.scalar(select(OAuthAccount).where(OAuthAccount.user_id == author.id))
        assert account is not None
        account.account_email = "101@users.noreply.github.com"
        await session.flush()
        assert await author_email(session, str(author.id), author.email) == account.account_email
        account.account_email = ""
        await session.flush()
        assert await author_email(session, str(author.id)) == author.email
        await session.rollback()


def test_publish_events_queue_author_email_and_runner_retries(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'notifications.sqlite'}",
        auto_create_schema=True,
        allowed_hosts=("testserver",),
        execution_mode="fake",
        reviewer_github_logins=("scientific-reviewer",),
        smtp_enabled=True,
        smtp_server="smtp.example.test",
        smtp_from="no-reply@example.com",
        smtp_password="fake-smtp-password",
    )
    author = User(
        id=uuid4(),
        email="old-user@example.com",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role="member",
        github_login="proposal-author",
    )
    reviewer = User(
        id=uuid4(),
        email="reviewer@example.com",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role="member",
        github_login="scientific-reviewer",
    )
    app = create_app(settings)
    active = {"user": author}
    app.dependency_overrides[current_active_user] = lambda: active["user"]
    with (
        TestClient(app) as client,
        patch(
            "src.community.routes.create_github_discussion",
            AsyncMock(
                return_value={
                    "id": "D_testEmail",
                    "number": 51,
                    "url": "https://github.com/example/repo/discussions/51",
                }
            ),
        ),
        patch(
            "src.community.routes.create_github_discussion_comment",
            AsyncMock(
                return_value={
                    "id": "DC_testReviewEmail",
                    "url": "https://github.com/example/repo/discussions/51#discussioncomment-1",
                    "author": {"login": "scientific-reviewer"},
                }
            ),
        ),
    ):
        client.portal.call(seed_author, app.state.session_factory, author)
        created = client.post("/api/v1/proposals", json=proposal_payload())
        assert created.status_code == 201, created.text
        proposal_id = created.json()["id"]

        active["user"] = reviewer
        reviewed = client.post(f"/api/v1/proposals/{proposal_id}/reviews", json=review_payload())
        assert reviewed.status_code == 201, reviewed.text
        deliveries = client.portal.call(mail_rows, app.state.session_factory)
        assert len(deliveries) == 2
        assert [item.event_type for item in deliveries] == ["proposal_submitted", "review_update"]
        assert deliveries[0].dedupe_key == f"smtp:submit_proposal:{proposal_id}"
        assert deliveries[1].dedupe_key == "smtp:review_update:DC_testReviewEmail"
        for delivery in deliveries:
            assert delivery.payload["recipients"] == ["new-oauth@example.com"]
            assert delivery.payload["reply_to"] == ["contact@ai4sbench.org"]
            assert f"/tasks/task.html?id={proposal_id}" in delivery.payload["text"]
            assert "reviewer@example.com" not in str(delivery.payload)
            assert "fake-smtp-password" not in str(delivery.payload)
        assert "approved" not in deliveries[1].payload["text"].lower()
        client.portal.call(check_recipient_choice, app.state.session_factory, author)
        client.portal.call(duplicate_and_skip, app.state.session_factory, settings, proposal_id)
        assert len(client.portal.call(mail_rows, app.state.session_factory)) == 2

        runner = JobRunner(settings)
        try:
            with patch("src.community.deliveries.FastMail") as fast_mail:
                fast_mail.return_value.send_message = AsyncMock()
                assert client.portal.call(runner.run_once) is True
                sent = fast_mail.return_value.send_message.await_args.args[0]
                assert [recipient.email for recipient in sent.recipients] == ["new-oauth@example.com"]
                assert sent.alternative_body == deliveries[0].payload["html"]
                assert sent.multipart_subtype.value == "alternative"
                fast_mail.return_value.send_message = AsyncMock(side_effect=RuntimeError("secret"))
                assert client.portal.call(runner.run_once) is True
                assert client.portal.call(mail_rows, app.state.session_factory)[1].state == "pending"
                client.portal.call(retry_ready, app.state.session_factory, deliveries[1].id)
                assert client.portal.call(runner.run_once) is True
                failed = client.portal.call(mail_rows, app.state.session_factory)[1]
                assert failed.state == "failed"
                assert "secret" not in (failed.last_error or "")
        finally:
            client.portal.call(runner.engine.dispose)
