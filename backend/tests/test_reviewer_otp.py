from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine, inspect, select

from src.core.config import Settings
from src.core.identity import current_active_user
from src.db.models import EmailVerificationChallenge
from src.mailing.notifications import render_email_verification
from src.mailing.verification import (
    EmailVerificationError,
    EmailVerificationPurpose,
    consume_email_verification,
    issue_email_verification,
    send_verification_code,
    verify_email_verification,
)
from src.main import create_app
from tests.test_reviewers import make_user, reviewer_payload


def otp_app(database: Path, *, enabled: bool = True, smtp: bool = True, auth: bool = True):
    app = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            auto_create_schema=True,
            execution_mode="fake",
            form_mail_require_otp=enabled,
            smtp_enabled=smtp,
            smtp_server="smtp.example.test" if smtp else "",
            smtp_from="no-reply@example.test" if smtp else "",
        )
    )
    if auth:
        user = make_user("member", "otp-applicant")
        app.dependency_overrides[current_active_user] = lambda: user
    return app


def test_reviewer_otp_one_use_and_email_binding(tmp_path, monkeypatch) -> None:
    database = tmp_path / "otp.sqlite3"
    sent: list[tuple[str, str]] = []

    async def fake_send(_settings, email: str, code: str, _purpose) -> None:
        sent.append((email, code))

    monkeypatch.setattr("src.mailing.verification.send_verification_code", fake_send)
    app = otp_app(database)
    original_user = make_user("member", "otp-applicant")
    active = {"user": original_user}
    app.dependency_overrides[current_active_user] = lambda: active["user"]
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/config").json()["form_mail_require_otp"] is True
        assert client.post("/api/v1/reviewers", json=reviewer_payload()).status_code == 403
        issued = client.post("/api/v1/reviewers/email-otp", json={"email": "Ada.Reviewer@example.test"})
        assert issued.status_code == 202, issued.text
        challenge_id = issued.json()["challenge_id"]
        assert sent[0][0] == "ada.reviewer@example.test"
        assert len(sent[0][1]) == 6
        assert "code" not in issued.text
        assert client.post("/api/v1/reviewers/email-otp", json={"email": sent[0][0]}).status_code == 429

        wrong = client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": sent[0][0], "challenge_id": challenge_id, "code": "xxxxxx"},
        )
        assert wrong.status_code == 422
        mismatch = client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": "someone@example.test", "challenge_id": challenge_id, "code": sent[0][1]},
        )
        assert mismatch.status_code == 400
        verified = client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": sent[0][0], "challenge_id": challenge_id, "code": sent[0][1]},
        )
        assert verified.status_code == 200, verified.text
        token = verified.json()["verification_token"]
        assert token not in issued.text
        assert client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": sent[0][0], "challenge_id": challenge_id, "code": sent[0][1]},
        ).status_code == 400
        payload = reviewer_payload()
        payload["email"] = "wrong@example.test"
        payload["email_verification_token"] = token
        assert client.post("/api/v1/reviewers", json=payload).status_code == 403
        payload["email"] = "Ada.Reviewer@example.test"
        active["user"] = make_user("member", "different-account")
        assert client.post("/api/v1/reviewers", json=payload).status_code == 403
        active["user"] = original_user
        accepted = client.post("/api/v1/reviewers", json=payload)
        assert accepted.status_code == 201, accepted.text
        assert "email_verification_token" not in accepted.json()
        assert client.post("/api/v1/reviewers", json=payload).status_code == 403

    with sqlite3.connect(database) as connection:
        code_hash, token_hash = connection.execute(
            "SELECT code_hash, token_hash FROM email_verification_challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        assert sent[0][1] not in code_hash
        assert token != token_hash
        assert connection.execute("SELECT COUNT(*) FROM reviewer_applications").fetchone()[0] == 1


def test_reviewer_otp_disabled_or_smtp_unavailable(tmp_path) -> None:
    with TestClient(otp_app(tmp_path / "anonymous.sqlite3", auth=False)) as client:
        schema = client.get("/openapi.json").json()
        for path in ("/api/v1/reviewers/email-otp", "/api/v1/reviewers/email-otp/verify"):
            assert schema["paths"][path]["post"]["security"] == [{"APIKeyCookie": []}]
        assert client.post("/api/v1/reviewers", json=reviewer_payload()).status_code == 401
        assert client.post(
            "/api/v1/reviewers/email-otp", json={"email": "a@example.test"}
        ).status_code == 401
        assert client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={
                "email": "a@example.test",
                "challenge_id": "00000000-0000-0000-0000-000000000000",
                "code": "123456",
            },
        ).status_code == 401
    with TestClient(otp_app(tmp_path / "disabled.sqlite3", enabled=False, smtp=False)) as client:
        assert client.get("/api/v1/auth/config").json()["form_mail_require_otp"] is False
        assert client.post("/api/v1/reviewers/email-otp", json={"email": "a@example.test"}).status_code == 404
        assert client.post("/api/v1/reviewers", json=reviewer_payload()).status_code == 201
    with TestClient(otp_app(tmp_path / "unavailable.sqlite3", smtp=False)) as client:
        assert client.post("/api/v1/reviewers/email-otp", json={"email": "a@example.test"}).status_code == 503
        assert client.post("/api/v1/reviewers", json=reviewer_payload()).status_code == 403


def test_reviewer_otp_failed_send_and_attempt_limit(tmp_path, monkeypatch) -> None:
    database = tmp_path / "failed.sqlite3"

    async def failing_send(_settings, _email: str, _code: str, _purpose) -> None:
        raise RuntimeError("secret SMTP failure")

    monkeypatch.setattr("src.mailing.verification.send_verification_code", failing_send)
    with TestClient(otp_app(database)) as client:
        response = client.post("/api/v1/reviewers/email-otp", json={"email": "a@example.test"})
        assert response.status_code == 503
        assert "secret" not in response.text

    sent: list[str] = []

    async def fake_send(_settings, _email: str, code: str, _purpose) -> None:
        sent.append(code)

    monkeypatch.setattr("src.mailing.verification.send_verification_code", fake_send)
    with TestClient(otp_app(tmp_path / "attempts.sqlite3")) as client:
        issued = client.post("/api/v1/reviewers/email-otp", json={"email": "a@example.test"}).json()
        bad_code = "000000" if sent[0] != "000000" else "111111"
        for _ in range(5):
            assert client.post(
                "/api/v1/reviewers/email-otp/verify",
                json={"email": "a@example.test", "challenge_id": issued["challenge_id"], "code": bad_code},
            ).status_code == 400
        assert client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": "a@example.test", "challenge_id": issued["challenge_id"], "code": sent[0]},
        ).status_code == 400


def test_reviewer_otp_expiry_and_new_code_invalidate_proof(tmp_path, monkeypatch) -> None:
    database = tmp_path / "expiry.sqlite3"
    sent: list[str] = []

    async def fake_send(_settings, _email: str, code: str, _purpose) -> None:
        sent.append(code)

    monkeypatch.setattr("src.mailing.verification.send_verification_code", fake_send)
    email = "a@example.test"
    with TestClient(otp_app(database)) as client:
        first = client.post("/api/v1/reviewers/email-otp", json={"email": email}).json()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE email_verification_challenges SET expires_at = 0 WHERE id = ?",
                (first["challenge_id"],),
            )
        assert client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": email, "challenge_id": first["challenge_id"], "code": sent[0]},
        ).status_code == 400
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE email_verification_challenges SET expires_at = 9999999999 WHERE id = ?",
                (first["challenge_id"],),
            )
        proof = client.post(
            "/api/v1/reviewers/email-otp/verify",
            json={"email": email, "challenge_id": first["challenge_id"], "code": sent[0]},
        ).json()["verification_token"]
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE email_verification_challenges SET created_at = created_at - 61 WHERE id = ?",
                (first["challenge_id"],),
            )
        assert client.post("/api/v1/reviewers/email-otp", json={"email": email}).status_code == 202
        payload = reviewer_payload()
        payload["email"] = email
        payload["email_verification_token"] = proof
        assert client.post("/api/v1/reviewers", json=payload).status_code == 403


def test_reviewer_otp_template_has_plain_text_and_html() -> None:
    rendered = render_email_verification("123456", "Verify your email", "submit a reviewer application")
    assert "123456" in rendered.text and "123456" in rendered.html
    assert "10 minutes" in rendered.text
    assert "notification.html" not in rendered.html


def test_reviewer_otp_uses_multipart_smtp(monkeypatch) -> None:
    messages = []

    class FakeFastMail:
        def __init__(self, _configuration) -> None:
            pass

        async def send_message(self, message) -> None:
            messages.append(message)

    monkeypatch.setattr("src.mailing.verification.FastMail", FakeFastMail)
    settings = Settings(
        environment="test",
        smtp_enabled=True,
        smtp_server="smtp.example.test",
        smtp_from="no-reply@example.com",
    )
    asyncio.run(
        send_verification_code(
            settings, "person@example.com", "123456", EmailVerificationPurpose.REVIEWER_FORM
        )
    )
    assert len(messages) == 1
    assert "123456" in messages[0].body
    assert "123456" in messages[0].alternative_body
    assert messages[0].multipart_subtype.value == "alternative"


def test_shared_verification_is_scoped_to_purpose_and_account(tmp_path, monkeypatch) -> None:
    database = tmp_path / "scopes.sqlite3"
    sent: list[tuple[EmailVerificationPurpose, str]] = []

    async def fake_send(_settings, _email: str, code: str, purpose: EmailVerificationPurpose) -> None:
        sent.append((purpose, code))

    monkeypatch.setattr("src.mailing.verification.send_verification_code", fake_send)
    app = otp_app(database)
    email = "person@example.com"

    async def exercise() -> None:
        factory = app.state.session_factory
        settings = app.state.settings
        with pytest.raises(ValueError, match="subject ID"):
            async with factory() as session:
                await issue_email_verification(
                    session, settings, email=email, purpose=EmailVerificationPurpose.BIND_EMAIL
                )
        async with factory() as session:
            bound = await issue_email_verification(
                session,
                settings,
                email=email,
                purpose=EmailVerificationPurpose.BIND_EMAIL,
                subject_id="user-1",
            )
        async with factory() as session:
            with pytest.raises(EmailVerificationError) as wrong_user:
                await verify_email_verification(
                    session,
                    settings,
                    email=email,
                    purpose=EmailVerificationPurpose.BIND_EMAIL,
                    subject_id="user-2",
                    challenge_id=bound.challenge_id,
                    code=sent[0][1],
                )
            assert wrong_user.value.reason == "invalid_code"
        async with factory() as session:
            with pytest.raises(EmailVerificationError) as wrong_purpose:
                await verify_email_verification(
                    session,
                    settings,
                    email=email,
                    purpose=EmailVerificationPurpose.CHANGE_EMAIL,
                    subject_id="user-1",
                    challenge_id=bound.challenge_id,
                    code=sent[0][1],
                )
            assert wrong_purpose.value.reason == "invalid_code"
        async with factory() as session:
            proof = await verify_email_verification(
                session,
                settings,
                email=email,
                purpose=EmailVerificationPurpose.BIND_EMAIL,
                subject_id="user-1",
                challenge_id=bound.challenge_id,
                code=sent[0][1],
            )
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE email_verification_challenges SET created_at = created_at - 61 WHERE id = ?",
                (bound.challenge_id,),
            )
        async with factory() as session:
            changed = await issue_email_verification(
                session,
                settings,
                email=email,
                purpose=EmailVerificationPurpose.CHANGE_EMAIL,
                subject_id="user-1",
            )
            assert changed.challenge_id != bound.challenge_id
        async with factory() as session:
            with pytest.raises(EmailVerificationError) as wrong_proof:
                await consume_email_verification(
                    session,
                    email=email,
                    purpose=EmailVerificationPurpose.CHANGE_EMAIL,
                    subject_id="user-1",
                    token=proof.verification_token,
                )
            assert wrong_proof.value.reason == "invalid_proof"
        async with factory() as session:
            await session.scalar(select(EmailVerificationChallenge.id).limit(1))
            await consume_email_verification(
                session,
                email=email,
                purpose=EmailVerificationPurpose.BIND_EMAIL,
                subject_id="user-1",
                token=proof.verification_token,
            )
            await session.commit()
        async with factory() as session:
            with pytest.raises(EmailVerificationError) as replay:
                await consume_email_verification(
                    session,
                    email=email,
                    purpose=EmailVerificationPurpose.BIND_EMAIL,
                    subject_id="user-1",
                    token=proof.verification_token,
                )
            assert replay.value.reason == "invalid_proof"

    with TestClient(app):
        asyncio.run(exercise())


def test_failed_resend_preserves_existing_verified_proof(tmp_path, monkeypatch) -> None:
    database = tmp_path / "resend.sqlite3"
    sent: list[str] = []

    async def fake_send(_settings, _email: str, code: str, _purpose) -> None:
        sent.append(code)

    monkeypatch.setattr("src.mailing.verification.send_verification_code", fake_send)
    app = otp_app(database)
    email = "person@example.com"

    async def exercise() -> None:
        factory = app.state.session_factory
        settings = app.state.settings
        async with factory() as session:
            issued = await issue_email_verification(
                session,
                settings,
                email=email,
                purpose=EmailVerificationPurpose.REVIEWER_FORM,
                subject_id="user-1",
            )
        async with factory() as session:
            proof = await verify_email_verification(
                session,
                settings,
                email=email,
                purpose=EmailVerificationPurpose.REVIEWER_FORM,
                subject_id="user-1",
                challenge_id=issued.challenge_id,
                code=sent[0],
            )
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE email_verification_challenges SET created_at = created_at - 61 WHERE id = ?",
                (issued.challenge_id,),
            )

        async def failing_send(_settings, _email: str, _code: str, _purpose) -> None:
            raise RuntimeError("SMTP unavailable")

        monkeypatch.setattr("src.mailing.verification.send_verification_code", failing_send)
        async with factory() as session:
            with pytest.raises(EmailVerificationError) as failure:
                await issue_email_verification(
                    session,
                    settings,
                    email=email,
                    purpose=EmailVerificationPurpose.REVIEWER_FORM,
                    subject_id="user-1",
                )
            assert failure.value.reason == "mail_unavailable"
        async with factory() as session:
            await consume_email_verification(
                session,
                email=email,
                purpose=EmailVerificationPurpose.REVIEWER_FORM,
                token=proof.verification_token,
                subject_id="user-1",
            )
            await session.commit()

    with TestClient(app):
        asyncio.run(exercise())


def test_reviewer_otp_migration_up_and_down(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.sqlite3'}"
    config = Config("alembic.ini")
    with patch.dict("os.environ", {"TBCP_DATABASE_URL": database_url}):
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            assert "email_verification_challenges" in inspect(engine).get_table_names()
            metadata = MetaData()
            EmailVerificationChallenge.__table__.to_metadata(metadata)
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "include_object": lambda obj, name, kind, reflected, compare_to: (
                            kind != "table" or name == "email_verification_challenges"
                        ),
                    },
                )
                assert compare_metadata(context, metadata) == []
            command.downgrade(config, "20261011_0019")
            assert "email_verification_challenges" not in inspect(engine).get_table_names()
        finally:
            engine.dispose()
