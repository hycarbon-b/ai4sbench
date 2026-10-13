"""Reusable, purpose-scoped email verification; callers own their authorization policy."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from fastapi_mail import FastMail, MessageSchema, MessageType
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.db.database import begin_immediate
from src.db.models import EmailVerificationChallenge
from src.mailing.notifications import render_email_verification
from src.mailing.smtp import smtp_connection_config

CODE_LIFETIME_SECONDS = 600
TOKEN_LIFETIME_SECONDS = 900
SEND_COOLDOWN_SECONDS = 60
SEND_LIMIT_PER_HOUR = 5
MAX_CODE_ATTEMPTS = 5


class EmailVerificationPurpose(StrEnum):
    REVIEWER_FORM = "reviewer_form"
    BIND_EMAIL = "bind_email"
    CHANGE_EMAIL = "change_email"


ErrorReason = Literal["mail_unavailable", "rate_limited", "invalid_code", "invalid_proof"]


class EmailVerificationError(Exception):
    def __init__(self, reason: ErrorReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class IssuedEmailChallenge:
    challenge_id: str
    expires_in_seconds: int


@dataclass(frozen=True)
class VerifiedEmailProof:
    verification_token: str
    expires_in_seconds: int


_EMAIL_COPY = {
    EmailVerificationPurpose.REVIEWER_FORM: (
        "Verify your AI4S-Benchmark reviewer application email",
        "submit your reviewer application",
    ),
    EmailVerificationPurpose.BIND_EMAIL: (
        "Verify your AI4S-Benchmark email address",
        "bind this email address to your account",
    ),
    EmailVerificationPurpose.CHANGE_EMAIL: (
        "Confirm your new AI4S-Benchmark email address",
        "change the email address on your account",
    ),
}


def _normalized_email(email: str) -> str:
    value = email.strip().lower()
    if len(value) > 200 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("email must be a valid address of at most 200 characters")
    return value


def _scope(purpose: EmailVerificationPurpose, subject_id: str | None) -> tuple[str, str]:
    purpose = EmailVerificationPurpose(purpose)
    subject = (subject_id or "").strip()
    if not subject:
        raise ValueError("email verification requires a subject ID")
    if len(subject) > 100:
        raise ValueError("subject ID is too long")
    return purpose.value, subject


def _code_hash(settings: Settings, challenge_id: str, purpose: str, subject_id: str, code: str) -> str:
    key = settings.auth_jwt_secret.get_secret_value().encode()
    content = f"email-verification:{challenge_id}:{purpose}:{subject_id}:{code}".encode()
    return hmac.new(key, content, hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def send_verification_code(
    settings: Settings, email: str, code: str, purpose: EmailVerificationPurpose
) -> None:
    subject, action = _EMAIL_COPY[purpose]
    rendered = render_email_verification(code, subject, action)
    message = MessageSchema(
        recipients=[email],
        subject=rendered.subject,
        body=rendered.text,
        subtype=MessageType.plain,
        alternative_body=rendered.html,
        multipart_subtype="alternative",
    )
    await FastMail(smtp_connection_config(settings)).send_message(message)


async def issue_email_verification(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    purpose: EmailVerificationPurpose,
    subject_id: str | None = None,
) -> IssuedEmailChallenge:
    """Create and email a challenge. Sending is synchronous; no plaintext code enters the queue."""
    scope_purpose, scope_subject = _scope(purpose, subject_id)
    email = _normalized_email(email)
    if not settings.smtp_enabled or not settings.smtp_server or not settings.smtp_from:
        raise EmailVerificationError("mail_unavailable", "Email verification is unavailable")
    now = int(time.time())
    await begin_immediate(session)
    recent = await session.scalar(
        select(func.max(EmailVerificationChallenge.created_at)).where(
            EmailVerificationChallenge.email == email
        )
    )
    if recent is not None and now - recent < SEND_COOLDOWN_SECONDS:
        raise EmailVerificationError("rate_limited", "Please wait before requesting another code")
    count = await session.scalar(
        select(func.count()).select_from(EmailVerificationChallenge).where(
            EmailVerificationChallenge.email == email,
            EmailVerificationChallenge.created_at > now - 3600,
        )
    )
    if count >= SEND_LIMIT_PER_HOUR:
        raise EmailVerificationError("rate_limited", "Too many codes requested. Try again later")
    await session.execute(
        delete(EmailVerificationChallenge).where(EmailVerificationChallenge.created_at < now - 86400)
    )
    challenge_id = str(uuid.uuid4())
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(
        EmailVerificationChallenge(
            id=challenge_id,
            email=email,
            purpose=scope_purpose,
            subject_id=scope_subject,
            code_hash=_code_hash(settings, challenge_id, scope_purpose, scope_subject, code),
            attempts=0,
            created_at=now,
            expires_at=now + CODE_LIFETIME_SECONDS,
        )
    )
    await session.commit()
    try:
        await send_verification_code(settings, email, code, purpose)
    except Exception as exc:
        await begin_immediate(session)
        await session.execute(
            delete(EmailVerificationChallenge).where(EmailVerificationChallenge.id == challenge_id)
        )
        await session.commit()
        raise EmailVerificationError("mail_unavailable", "Verification email could not be sent") from exc
    # Only a successfully sent code replaces earlier proofs in the same scope.
    await begin_immediate(session)
    await session.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.email == email,
            EmailVerificationChallenge.purpose == scope_purpose,
            EmailVerificationChallenge.subject_id == scope_subject,
            EmailVerificationChallenge.id != challenge_id,
            EmailVerificationChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=int(time.time()))
    )
    await session.commit()
    return IssuedEmailChallenge(challenge_id=challenge_id, expires_in_seconds=CODE_LIFETIME_SECONDS)


async def verify_email_verification(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    purpose: EmailVerificationPurpose,
    challenge_id: str,
    code: str,
    subject_id: str | None = None,
) -> VerifiedEmailProof:
    scope_purpose, scope_subject = _scope(purpose, subject_id)
    email = _normalized_email(email)
    if len(challenge_id) != 36 or not re.fullmatch(r"\d{6}", code):
        raise EmailVerificationError("invalid_code", "Invalid or expired verification code")
    now = int(time.time())
    await begin_immediate(session)
    item = await session.get(EmailVerificationChallenge, challenge_id)
    if (
        item is None
        or item.email != email
        or item.purpose != scope_purpose
        or item.subject_id != scope_subject
        or item.consumed_at is not None
        or item.verified_at is not None
        or item.expires_at <= now
        or item.attempts >= MAX_CODE_ATTEMPTS
    ):
        raise EmailVerificationError("invalid_code", "Invalid or expired verification code")
    item.attempts += 1
    if not hmac.compare_digest(
        item.code_hash, _code_hash(settings, item.id, scope_purpose, scope_subject, code)
    ):
        await session.commit()
        raise EmailVerificationError("invalid_code", "Invalid or expired verification code")
    token = secrets.token_urlsafe(32)
    item.token_hash = _token_hash(token)
    item.verified_at = now
    item.expires_at = now + TOKEN_LIFETIME_SECONDS
    await session.commit()
    return VerifiedEmailProof(verification_token=token, expires_in_seconds=TOKEN_LIFETIME_SECONDS)


async def consume_email_verification(
    session: AsyncSession,
    *,
    email: str,
    purpose: EmailVerificationPurpose,
    token: str,
    subject_id: str | None = None,
) -> None:
    """Atomically consume proof in the caller's transaction; caller commits its business change."""
    scope_purpose, scope_subject = _scope(purpose, subject_id)
    email = _normalized_email(email)
    if not 32 <= len(token) <= 128:
        raise EmailVerificationError("invalid_proof", "Email verification is required or has expired")
    now = int(time.time())
    result = await session.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.token_hash == _token_hash(token),
            EmailVerificationChallenge.email == email,
            EmailVerificationChallenge.purpose == scope_purpose,
            EmailVerificationChallenge.subject_id == scope_subject,
            EmailVerificationChallenge.verified_at.is_not(None),
            EmailVerificationChallenge.consumed_at.is_(None),
            EmailVerificationChallenge.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if result.rowcount != 1:
        raise EmailVerificationError("invalid_proof", "Email verification is required or has expired")
