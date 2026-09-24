from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_community import proposal_payload, review_payload

from control_panel.config import Settings
from control_panel.database import Base
from control_panel.deliveries import (
    DeliveryResponseError,
    discord_message_url,
    enqueue_delivery,
)
from control_panel.identity import User, current_active_user
from control_panel.job_runner import JobRunner
from control_panel.main import create_app
from control_panel.models import OutboundDelivery, Proposal


def github_user(role: str, login: str) -> User:
    return User(
        id=uuid4(),
        email=f"{login}@example.test",
        hashed_password="not-used-in-this-test",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        role=role,
        github_login=login,
    )


async def build_discord_message_url(transport: httpx.MockTransport) -> str | None:
    async with httpx.AsyncClient(transport=transport) as client:
        return await discord_message_url(
            client,
            "https://discord.example/api/webhooks/id/token",
            '{"id":"message-456","channel_id":"thread-789"}',
        )


async def load_deliveries(factory) -> list[OutboundDelivery]:
    async with factory() as session:
        return list(await session.scalars(select(OutboundDelivery).order_by(OutboundDelivery.created_at)))


async def proposal_message_url(factory, proposal_id: str) -> str | None:
    async with factory() as session:
        proposal = await session.get(Proposal, proposal_id)
        assert proposal is not None
        return proposal.discord_message_url


async def initialize_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def seed_retry_delivery(factory, settings: Settings) -> str:
    async with factory() as session:
        first = await enqueue_delivery(
            session,
            delivery_type="discord",
            destination=settings.discord_webhook_url.get_secret_value(),
            event_type="proposal_created",
            dedupe_key="discord:test:one",
            payload={"content": "one"},
        )
        duplicate = await enqueue_delivery(
            session,
            delivery_type="discord",
            destination=settings.discord_webhook_url.get_secret_value(),
            event_type="proposal_created",
            dedupe_key="discord:test:one",
            payload={"content": "duplicate"},
        )
        assert first is not None
        assert duplicate is not None
        assert duplicate.id == first.id
        first.max_attempts = 2
        await session.commit()
        return first.id


async def retry_state(factory, delivery_id: str, *, make_available: bool = False) -> OutboundDelivery:
    async with factory() as session:
        delivery = await session.get(OutboundDelivery, delivery_id)
        assert delivery is not None
        if make_available:
            delivery.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        return delivery


def test_discord_message_url_uses_webhook_guild_metadata() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"guild_id": "guild-123"}))
    message_url = asyncio.run(build_discord_message_url(transport))
    assert message_url == "https://discord.com/channels/guild-123/thread-789/message-456"


def test_proposals_and_reviews_queue_inspectable_discord_deliveries() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
            allowed_hosts=("testserver",),
            reviewer_github_logins=("science-reviewer",),
            discord_webhook_url="https://discord.example/api/webhooks/id/token",
        )
        app = create_app(settings)
        reviewer = github_user("member", "science-reviewer")
        app.dependency_overrides[current_active_user] = lambda: reviewer

        with (
            TestClient(app) as client,
            patch(
                "control_panel.community.create_github_discussion",
                AsyncMock(
                    return_value={
                        "id": "D_kwDONotify",
                        "number": 61,
                        "url": "https://github.com/example/repo/discussions/61",
                    }
                ),
            ),
            patch(
                "control_panel.community.create_github_discussion_comment",
                AsyncMock(
                    return_value={
                        "id": "DC_kwDONotifyReview",
                        "url": "https://github.com/example/repo/discussions/61#discussioncomment-4",
                        "author": {"login": "science-reviewer"},
                        "createdAt": "2026-09-08T01:00:00Z",
                        "updatedAt": "2026-09-08T01:00:00Z",
                    }
                ),
            ),
        ):
            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["can_review"] is True

            created = client.post("/api/v1/proposals", json=proposal_payload())
            assert created.status_code == 201, created.text
            proposal_id = created.json()["id"]

            reviewed = client.post(f"/api/v1/proposals/{proposal_id}/reviews", json=review_payload())
            assert reviewed.status_code == 201, reviewed.text

            deliveries = client.portal.call(load_deliveries, app.state.session_factory)
            assert [item.event_type for item in deliveries] == [
                "proposal_created",
                "review_published",
            ]
            assert all(
                item.destination == "https://discord.example/api/webhooks/id/token" for item in deliveries
            )
            assert deliveries[0].dedupe_key == f"discord:proposal-created:{proposal_id}"
            assert deliveries[1].dedupe_key == "discord:review-published:DC_kwDONotifyReview"
            assert deliveries[0].payload["thread_name"].startswith("Proposal #61")
            assert deliveries[1].payload["thread_name"].startswith("Review Approved")
            assert deliveries[0].payload["embeds"][0]["title"].startswith("New proposal")
            assert "Approved" in deliveries[1].payload["embeds"][0]["title"]

            with patch(
                "control_panel.job_runner.send_delivery",
                side_effect=[
                    (
                        200,
                        '{"id":"discord-message"}',
                        "https://discord.com/channels/guild/thread/proposal-message",
                    ),
                    (
                        200,
                        '{"id":"review-message"}',
                        "https://discord.com/channels/guild/thread/review-message",
                    ),
                ],
            ):
                runner = JobRunner(settings)
                assert client.portal.call(runner.run_once) is True
                assert client.portal.call(runner.run_once) is True
                client.portal.call(runner.engine.dispose)

            assert (
                client.portal.call(proposal_message_url, app.state.session_factory, proposal_id)
                == "https://discord.com/channels/guild/thread/proposal-message"
            )

            proposals = client.get("/api/v1/proposals")
            assert proposals.status_code == 200, proposals.text
            assert proposals.json()["items"][0]["discord_message_url"] == (
                "https://discord.com/channels/guild/thread/proposal-message"
            )

            board = client.get("/api/v1/public/proposals")
            assert board.status_code == 200, board.text
            assert board.json()["items"][0]["discord_message_url"] == (
                "https://discord.com/channels/guild/thread/proposal-message"
            )

            app.dependency_overrides[current_active_user] = lambda: github_user("admin", "operator")
            listed = client.get("/api/v1/deliveries")
            assert listed.status_code == 200, listed.text
            items = listed.json()["items"]
            assert len(items) == 2
            assert all(item["state"] == "completed" for item in items)
            assert all(item["response_status"] == 200 for item in items)
            assert all(item["delivery_type"] == "discord" for item in items)
            assert all(item["destination"].endswith("/id/token") for item in items)

            resent = client.post(f"/api/v1/deliveries/{items[0]['id']}/resend")
            assert resent.status_code == 200, resent.text
            assert resent.json()["state"] == "pending"
            assert resent.json()["attempts"] == 0
            assert resent.json()["sent_at"] is None


def test_webhook_deduplication_and_bounded_retry() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
            discord_webhook_url="https://discord.example/api/webhooks/id/token",
        )
        app = create_app(settings)
        asyncio.run(initialize_schema(app.state.engine))
        delivery_id = asyncio.run(seed_retry_delivery(app.state.session_factory, settings))

        runner = JobRunner(settings)
        response_error = DeliveryResponseError(400, '{"message":"invalid payload"}')
        with patch("control_panel.job_runner.send_delivery", side_effect=response_error):
            assert asyncio.run(runner.run_once()) is True
            delivery = asyncio.run(retry_state(app.state.session_factory, delivery_id, make_available=True))
            assert delivery.state == "pending"
            assert delivery.attempts == 1
            assert "invalid payload" in (delivery.last_error or "")
            assert delivery.response_status == 400
            assert delivery.response_body == '{"message":"invalid payload"}'

            assert asyncio.run(runner.run_once()) is True

        delivery = asyncio.run(retry_state(app.state.session_factory, delivery_id))
        assert delivery.state == "failed"
        assert delivery.attempts == 2
        asyncio.run(runner.engine.dispose())
        asyncio.run(app.state.engine.dispose())


def test_smtp_delivery_uses_fastapi_mail_without_recording_credentials() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
            smtp_enabled=True,
            smtp_server="smtp.example.test",
            smtp_username="mailer",
            smtp_password="smtp-secret",
            smtp_from="notifications@example.com",
        )
        app = create_app(settings)
        asyncio.run(initialize_schema(app.state.engine))

        async def seed() -> str:
            async with app.state.session_factory() as session:
                delivery = await enqueue_delivery(
                    session,
                    delivery_type="smtp",
                    destination="smtp://smtp.example.test:587",
                    event_type="reviewer_application_received",
                    dedupe_key="smtp:test:one",
                    payload={
                        "recipients": ["recipient@example.com"],
                        "subject": "Review received",
                        "text": "Your application was received.",
                        "reply_to": ["support@example.com"],
                    },
                )
                await session.commit()
                return delivery.id

        delivery_id = asyncio.run(seed())
        runner = JobRunner(settings)
        with patch("control_panel.deliveries.FastMail") as fast_mail:
            fast_mail.return_value.send_message = AsyncMock()
            assert asyncio.run(runner.run_once()) is True
            message = fast_mail.return_value.send_message.await_args.args[0]
            assert str(message.recipients[0]) == "recipient <recipient@example.com>"
            assert message.subject == "Review received"
            assert str(message.reply_to[0]) == "support <support@example.com>"

        delivery = asyncio.run(retry_state(app.state.session_factory, delivery_id))
        assert delivery.state == "completed"
        assert delivery.response_status == 250
        assert "smtp-secret" not in str(delivery.payload)
        assert "smtp-secret" not in (delivery.response_body or "")
        asyncio.run(runner.engine.dispose())
        asyncio.run(app.state.engine.dispose())


def test_smtp_delivery_failure_retries_and_becomes_terminal() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
            smtp_enabled=True,
            smtp_server="smtp.example.test",
            smtp_from="notifications@example.com",
        )
        app = create_app(settings)
        asyncio.run(initialize_schema(app.state.engine))

        async def seed() -> str:
            async with app.state.session_factory() as session:
                delivery = await enqueue_delivery(
                    session,
                    delivery_type="smtp",
                    destination="smtp://smtp.example.test:587",
                    event_type="test",
                    dedupe_key="smtp:test:failure",
                    payload={
                        "recipients": ["recipient@example.com"],
                        "subject": "Test",
                        "text": "Body",
                    },
                )
                delivery.max_attempts = 1
                await session.commit()
                return delivery.id

        delivery_id = asyncio.run(seed())
        runner = JobRunner(settings)
        with patch("control_panel.deliveries.FastMail") as fast_mail:
            fast_mail.return_value.send_message = AsyncMock(side_effect=RuntimeError("SMTP unavailable"))
            assert asyncio.run(runner.run_once()) is True

        delivery = asyncio.run(retry_state(app.state.session_factory, delivery_id))
        assert delivery.state == "failed"
        assert delivery.attempts == 1
        assert delivery.last_error == "RuntimeError: SMTP unavailable"
        asyncio.run(runner.engine.dispose())
        asyncio.run(app.state.engine.dispose())


def test_outbound_delivery_migration_preserves_discord_history() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        database_url = f"sqlite:///{database.as_posix()}"
        config.set_main_option("sqlalchemy.url", database_url)
        with patch.dict(os.environ, {"TBCP_DATABASE_URL": database_url}):
            command.upgrade(config, "20260916_0015")
            connection = sqlite3.connect(database)
            try:
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    INSERT INTO webhook_deliveries
                    (id, event_type, destination_url, payload, dedupe_key, state, attempts,
                     max_attempts, available_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "delivery-1",
                        "proposal_created",
                        "https://discord.example/api/webhooks/id/token",
                        '{"content":"proposal"}',
                        "discord:proposal-created:proposal-1",
                        "completed",
                        1,
                        3,
                        now,
                        now,
                        now,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            command.upgrade(config, "head")

        connection = sqlite3.connect(database)
        try:
            row = connection.execute(
                "SELECT delivery_type, destination, event_type, dedupe_key, state, payload "
                "FROM outbound_deliveries WHERE id = 'delivery-1'"
            ).fetchone()
            assert row == (
                "discord",
                "https://discord.example/api/webhooks/id/token",
                "proposal_created",
                "discord:proposal-created:proposal-1",
                "completed",
                '{"content":"proposal"}',
            )
        finally:
            connection.close()
