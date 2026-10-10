from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.community.deliveries import enqueue_delivery
from src.core.auth import get_principal
from src.core.identity import OAuthAccount, User
from src.db.database import begin_immediate, get_session
from src.db.models import OutboundDelivery, Proposal, ReviewerApplication
from src.mailing.notifications import CONTACT_EMAIL, render_bulk_message

SessionDep = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
EVENT_TYPE = "admin_bulk_email"
EMAIL_ADAPTER = TypeAdapter(EmailStr)
admin_mailing_router = APIRouter(
    prefix="/api/v1/admin-mail",
    tags=["admin-mail"],
    dependencies=[Depends(get_principal)],
)


class MailRecipient(BaseModel):
    user_id: UUID
    email: EmailStr
    github_login: str | None
    proposal_count: int
    reviewer_statuses: list[str]


class MailRecipientsResponse(BaseModel):
    items: list[MailRecipient]


class BulkMailCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_ids: list[UUID] = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)
    signature: str = Field(min_length=1, max_length=120)

    @field_validator("subject", "body", "signature")
    @classmethod
    def nonblank(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("Mail fields must not be blank")
        return result


class BulkMailResponse(BaseModel):
    queued_count: int
    delivery_ids: list[str]


async def eligible_recipients(session: AsyncSession) -> list[MailRecipient]:
    proposal_rows = await session.execute(select(Proposal.author_id).where(Proposal.deleted_at.is_(None)))
    proposal_counts = Counter(author_id for (author_id,) in proposal_rows.all())
    applications = (await session.execute(
        select(ReviewerApplication.submitted_by_login, ReviewerApplication.status)
    )).all()
    reviewer_statuses: dict[str, set[str]] = defaultdict(set)
    for login, status in applications:
        if login:
            reviewer_statuses[login.casefold()].add(status)

    accounts = (await session.execute(
        select(User, OAuthAccount.account_email)
        .join(OAuthAccount, OAuthAccount.user_id == User.id)
        .where(OAuthAccount.oauth_name == "github", OAuthAccount.account_email.is_not(None))
    )).unique().all()
    recipients: dict[UUID, MailRecipient] = {}
    for user, raw_email in accounts:
        count = proposal_counts[str(user.id)]
        statuses = sorted(reviewer_statuses.get((user.github_login or "").casefold(), set()))
        if not count and not statuses or not raw_email:
            continue
        try:
            email = EMAIL_ADAPTER.validate_python(raw_email.strip())
        except ValueError:
            continue
        recipients[user.id] = MailRecipient(
            user_id=user.id,
            email=email,
            github_login=user.github_login,
            proposal_count=count,
            reviewer_statuses=statuses,
        )
    return sorted(
        recipients.values(), key=lambda item: ((item.github_login or "").casefold(), str(item.email))
    )


@admin_mailing_router.get("/recipients", response_model=MailRecipientsResponse)
async def list_mail_recipients(session: SessionDep) -> MailRecipientsResponse:
    return MailRecipientsResponse(items=await eligible_recipients(session))


@admin_mailing_router.post("/deliveries", status_code=202, response_model=BulkMailResponse)
async def create_bulk_mail(
    body: BulkMailCreate,
    request: Request,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
) -> BulkMailResponse:
    settings = request.app.state.settings
    if not settings.smtp_enabled or not settings.smtp_server or not settings.smtp_from:
        raise HTTPException(status_code=503, detail="SMTP delivery is disabled or incomplete")
    if len(set(body.recipient_ids)) != len(body.recipient_ids):
        raise HTTPException(status_code=422, detail="Recipient IDs must be unique")

    await begin_immediate(session)
    eligible = {item.user_id: item for item in await eligible_recipients(session)}
    if any(user_id not in eligible for user_id in body.recipient_ids):
        raise HTTPException(
            status_code=422, detail="One or more recipients no longer have an eligible OAuth email"
        )
    target_emails = list(dict.fromkeys(
        str(eligible[user_id].email).casefold() for user_id in body.recipient_ids
    ))

    rendered = render_bulk_message(body.subject, body.body, body.signature)
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    prefix = f"smtp:admin-bulk:{key_digest}:"
    request_fingerprint = hashlib.sha256(json.dumps({
        "recipients": sorted(target_emails),
        "subject": body.subject,
        "body": body.body,
        "signature": body.signature,
    }, sort_keys=True).encode("utf-8")).hexdigest()
    existing = list((await session.scalars(
        select(OutboundDelivery).where(
            OutboundDelivery.event_type == EVENT_TYPE,
            OutboundDelivery.dedupe_key.like(f"{prefix}%"),
        )
    )).all())
    if existing:
        if len(existing) != len(target_emails) or any(
            item.payload.get("request_fingerprint") != request_fingerprint for item in existing
        ):
            raise HTTPException(status_code=409, detail="Idempotency key was used for different content")
        return BulkMailResponse(queued_count=len(existing), delivery_ids=[item.id for item in existing])

    deliveries = []
    for email in target_emails:
        item = await enqueue_delivery(
            session,
            delivery_type="smtp",
            destination=f"smtp://{settings.smtp_server}:{settings.smtp_port}",
            event_type=EVENT_TYPE,
            dedupe_key=f"{prefix}{hashlib.sha256(email.casefold().encode('utf-8')).hexdigest()}",
            payload={
                "recipients": [email],
                "subject": rendered.subject,
                "text": rendered.text,
                "html": rendered.html,
                "reply_to": [CONTACT_EMAIL],
                "request_fingerprint": request_fingerprint,
            },
        )
        deliveries.append(item)
    await session.commit()
    return BulkMailResponse(queued_count=len(deliveries), delivery_ids=[item.id for item in deliveries])
