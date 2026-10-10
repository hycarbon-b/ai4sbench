from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.community.deliveries import enqueue_delivery
from src.core.config import Settings
from src.db.models import OutboundDelivery
from src.jobs.runner import JobRunner
from src.main import create_app

KEY = "mailing-service-key-" * 3
URL = "/api/v1/mailing/deliveries"
HEADERS = {"Authorization": f"Bearer {KEY}", "Idempotency-Key": "request-1"}
BODY = {
    "recipients": ["receiver@example.com"],
    "subject": "Review received",
    "text": "Plain text copy",
    "html": "<p>HTML copy</p>",
    "reply_to": "support@example.com",
}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'mailing.sqlite'}",
        auto_create_schema=True,
        allowed_hosts=("testserver",),
        execution_mode="fake",
        mailing_service_key=KEY,
        smtp_enabled=True,
        smtp_server="smtp.example.test",
        smtp_username="no-reply@example.com",
        smtp_password="smtp-secret",
        smtp_from="no-reply@example.com",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        runner = JobRunner(settings)
        yield client, app, runner
        client.portal.call(runner.engine.dispose)


async def delivery_row(factory, delivery_id: str) -> OutboundDelivery:
    async with factory() as session:
        item = await session.get(OutboundDelivery, delivery_id)
        assert item is not None
        return item


async def set_retry_ready(factory, delivery_id: str) -> None:
    async with factory() as session:
        item = await session.get(OutboundDelivery, delivery_id)
        assert item is not None
        item.available_at = datetime.now(UTC)
        item.max_attempts = 2
        await session.commit()


async def create_other_delivery(factory, delivery_type: str, event_type: str) -> str:
    async with factory() as session:
        item = await enqueue_delivery(
            session,
            delivery_type=delivery_type,
            destination="https://example.test/webhook" if delivery_type == "discord" else "smtp://example.test",
            event_type=event_type,
            dedupe_key=f"{delivery_type}:{event_type}",
            payload={"content": "internal"},
        )
        item.state = "failed"
        await session.commit()
        return item.id


def test_mailing_auth_configuration_and_validation(setup, tmp_path) -> None:
    client, _app, _runner = setup
    assert client.post(URL, json=BODY).status_code == 401
    wrong_key = {"Authorization": "Bearer wrong", "Idempotency-Key": "one"}
    assert client.post(URL, headers=wrong_key, json=BODY).status_code == 401
    assert client.post(URL, headers={"Authorization": f"Bearer {KEY}"}, json=BODY).status_code == 422
    assert client.post(URL, headers={**HEADERS, "Idempotency-Key": "bad key"}, json=BODY).status_code == 422

    for changed in (
        {"recipients": []},
        {"recipients": ["not-an-email"]},
        {"recipients": [f"user{i}@example.com" for i in range(11)]},
        {"subject": "   "},
        {"text": "   "},
        {"html": "   "},
        {"reply_to": "not-an-email"},
        {"from": "spoof@example.test"},
        {"attachments": []},
    ):
        assert client.post(URL, headers=HEADERS, json={**BODY, **changed}).status_code == 422

    docs = client.get("/openapi/mailing/v1.json").json()
    assert set(docs["paths"]) == {URL, f"{URL}/{{delivery_id}}", f"{URL}/{{delivery_id}}/retry"}

    no_key = create_app(
        Settings(
            _env_file=None,
            environment="test",
            database_url=f"sqlite:///{tmp_path / 'no-key.sqlite'}",
            auto_create_schema=True,
            execution_mode="fake",
            smtp_enabled=True,
            smtp_server="smtp.example.test",
            smtp_from="no-reply@example.com",
        )
    )
    with TestClient(no_key) as no_key_client:
        assert no_key_client.post(URL, headers=HEADERS, json=BODY).status_code == 503

    no_smtp = create_app(
        Settings(
            _env_file=None,
            environment="test",
            database_url=f"sqlite:///{tmp_path / 'no-smtp.sqlite'}",
            auto_create_schema=True,
            execution_mode="fake",
            mailing_service_key=KEY,
        )
    )
    with TestClient(no_smtp) as no_smtp_client:
        assert no_smtp_client.post(URL, headers=HEADERS, json=BODY).status_code == 503


def test_create_idempotency_status_and_smtp_send(setup) -> None:
    client, app, runner = setup
    created = client.post(URL, headers=HEADERS, json=BODY)
    assert created.status_code == 202, created.text
    delivery_id = created.json()["id"]
    assert created.json()["state"] == "pending"
    assert "payload" not in created.json()
    assert "smtp-secret" not in created.text
    assert "Plain text copy" not in created.text

    duplicate = client.post(URL, headers=HEADERS, json=BODY)
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == delivery_id
    changed = client.post(URL, headers=HEADERS, json={**BODY, "subject": "Different"})
    assert changed.status_code == 409

    queued = client.portal.call(delivery_row, app.state.session_factory, delivery_id)
    assert queued.delivery_type == "smtp"
    assert queued.event_type == "mailing_api"
    assert queued.payload["reply_to"] == ["support@example.com"]
    assert "smtp-secret" not in str(queued.payload)

    with patch("src.community.deliveries.FastMail") as fast_mail:
        fast_mail.return_value.send_message = AsyncMock()
        assert client.portal.call(runner.run_once) is True
        message = fast_mail.return_value.send_message.await_args.args[0]
        assert message.body == "Plain text copy"
        assert message.alternative_body == "<p>HTML copy</p>"
        assert message.multipart_subtype.value == "alternative"
        assert message.subject == "Review received"

    status = client.get(f"{URL}/{delivery_id}", headers=HEADERS)
    assert status.status_code == 200
    assert status.json()["state"] == "completed"
    assert status.json()["attempts"] == 1
    assert status.json()["sent_at"] is not None
    assert "payload" not in status.json()
    assert client.post(f"{URL}/{delivery_id}/retry", headers=HEADERS).status_code == 409


def test_failure_retries_and_only_terminal_failure_can_be_requeued(setup, caplog) -> None:
    client, app, runner = setup
    created = client.post(URL, headers=HEADERS, json=BODY)
    assert created.status_code == 202
    delivery_id = created.json()["id"]
    client.portal.call(set_retry_ready, app.state.session_factory, delivery_id)

    with patch("src.community.deliveries.FastMail") as fast_mail:
        fast_mail.return_value.send_message = AsyncMock(side_effect=RuntimeError("smtp-secret leaked"))
        with caplog.at_level(logging.ERROR):
            assert client.portal.call(runner.run_once) is True
        pending = client.portal.call(delivery_row, app.state.session_factory, delivery_id)
        assert pending.state == "pending"
        assert pending.attempts == 1
        assert pending.last_error == "RuntimeError: SMTP delivery failed"
        assert client.post(f"{URL}/{delivery_id}/retry", headers=HEADERS).status_code == 409

        client.portal.call(set_retry_ready, app.state.session_factory, delivery_id)
        assert client.portal.call(runner.run_once) is True

    failed = client.get(f"{URL}/{delivery_id}", headers=HEADERS)
    assert failed.json()["state"] == "failed"
    assert "smtp-secret" not in failed.text
    assert "smtp-secret" not in caplog.text
    stored = client.portal.call(delivery_row, app.state.session_factory, delivery_id)
    assert "smtp-secret" not in (stored.last_error or "")

    retried = client.post(f"{URL}/{delivery_id}/retry", headers=HEADERS)
    assert retried.status_code == 202
    assert retried.json()["state"] == "pending"
    assert retried.json()["attempts"] == 0
    with patch("src.community.deliveries.FastMail") as fast_mail:
        fast_mail.return_value.send_message = AsyncMock()
        assert client.portal.call(runner.run_once) is True
    assert client.get(f"{URL}/{delivery_id}", headers=HEADERS).json()["state"] == "completed"


def test_other_deliveries_are_not_exposed(setup) -> None:
    client, app, _runner = setup
    for delivery_type, event_type in (("discord", "mailing_api"), ("smtp", "internal_mail")):
        delivery_id = client.portal.call(
            create_other_delivery,
            app.state.session_factory,
            delivery_type,
            event_type,
        )
        assert client.get(f"{URL}/{delivery_id}", headers=HEADERS).status_code == 404
        assert client.post(f"{URL}/{delivery_id}/retry", headers=HEADERS).status_code == 404
