from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_community import proposal_payload, review_payload

from control_panel.config import Settings
from control_panel.identity import User, current_active_user
from control_panel.job_runner import JobRunner
from control_panel.main import create_app
from control_panel.models import WebhookDelivery
from control_panel.webhooks import enqueue_delivery


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


def test_proposals_and_reviews_queue_inspectable_discord_deliveries() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
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

            with app.state.session_factory() as session:
                deliveries = list(
                    session.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at))
                )
                assert [item.event_type for item in deliveries] == [
                    "proposal_created",
                    "review_published",
                ]
                assert all(
                    item.destination_url == "https://discord.example/api/webhooks/id/token"
                    for item in deliveries
                )
                assert deliveries[0].dedupe_key == f"discord:proposal-created:{proposal_id}"
                assert deliveries[1].dedupe_key == "discord:review-published:DC_kwDONotifyReview"
                assert deliveries[0].payload["embeds"][0]["title"].startswith("New proposal")
                assert "Approved" in deliveries[1].payload["embeds"][0]["title"]

            with patch(
                "control_panel.job_runner.send_delivery",
                return_value=(200, '{"id":"discord-message"}'),
            ):
                runner = JobRunner(settings)
                assert runner.run_once() is True
                assert runner.run_once() is True
                runner.engine.dispose()

            app.dependency_overrides[current_active_user] = lambda: github_user(
                "admin", "operator"
            )
            listed = client.get("/api/v1/webhook-deliveries")
            assert listed.status_code == 200, listed.text
            items = listed.json()["items"]
            assert len(items) == 2
            assert all(item["state"] == "completed" for item in items)
            assert all(item["response_status"] == 200 for item in items)
            assert all(item["destination_url"].endswith("/id/token") for item in items)

            resent = client.post(f"/api/v1/webhook-deliveries/{items[0]['id']}/resend")
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
        with app.state.session_factory() as session:
            first = enqueue_delivery(
                session,
                settings,
                event_type="proposal_created",
                dedupe_key="discord:test:one",
                payload={"content": "one"},
            )
            duplicate = enqueue_delivery(
                session,
                settings,
                event_type="proposal_created",
                dedupe_key="discord:test:one",
                payload={"content": "duplicate"},
            )
            assert first is not None
            assert duplicate is not None
            assert duplicate.id == first.id
            first.max_attempts = 2
            session.commit()
            delivery_id = first.id

        runner = JobRunner(settings)
        with patch("control_panel.job_runner.send_delivery", side_effect=RuntimeError("offline")):
            assert runner.run_once() is True
            with app.state.session_factory() as session:
                delivery = session.get(WebhookDelivery, delivery_id)
                assert delivery is not None
                assert delivery.state == "pending"
                assert delivery.attempts == 1
                assert "offline" in (delivery.last_error or "")
                delivery.available_at = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()

            assert runner.run_once() is True

        with app.state.session_factory() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            assert delivery is not None
            assert delivery.state == "failed"
            assert delivery.attempts == 2
        runner.engine.dispose()
        app.state.engine.dispose()
