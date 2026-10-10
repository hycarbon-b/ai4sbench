from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.community.deliveries import enqueue_delivery, resend_delivery
from src.core.auth import require_mailing_service
from src.db.database import begin_immediate, get_session
from src.db.models import OutboundDelivery
from src.mailing.schemas import MailingDeliveryCreate, MailingDeliveryResponse

SessionDep = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
EVENT_TYPE = "mailing_api"
mailing_router = APIRouter(
    prefix="/api/v1/mailing",
    tags=["mailing"],
    dependencies=[Depends(require_mailing_service)],
)


async def mailing_delivery(session: AsyncSession, delivery_id: str) -> OutboundDelivery:
    item = await session.scalar(
        select(OutboundDelivery).where(
            OutboundDelivery.id == delivery_id,
            OutboundDelivery.delivery_type == "smtp",
            OutboundDelivery.event_type == EVENT_TYPE,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Mailing delivery not found")
    return item


@mailing_router.post(
    "/deliveries",
    status_code=202,
    response_model=MailingDeliveryResponse,
    summary="Create and queue an email delivery",
    responses={409: {"description": "Idempotency key was used for different content."}},
)
async def create_mailing_delivery(
    body: MailingDeliveryCreate,
    request: Request,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
) -> MailingDeliveryResponse:
    settings = request.app.state.settings
    if not settings.smtp_enabled:
        raise HTTPException(status_code=503, detail="SMTP delivery is disabled")

    payload = body.delivery_payload()
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    dedupe_key = f"smtp:mailing-api:{key_digest}"
    await begin_immediate(session)
    existing = await session.scalar(
        select(OutboundDelivery).where(OutboundDelivery.dedupe_key == dedupe_key)
    )
    if existing is not None:
        if (
            existing.delivery_type != "smtp"
            or existing.event_type != EVENT_TYPE
            or existing.payload != payload
        ):
            raise HTTPException(status_code=409, detail="Idempotency key was used for different content")
        return MailingDeliveryResponse.model_validate(existing)

    item = await enqueue_delivery(
        session,
        delivery_type="smtp",
        destination=f"smtp://{settings.smtp_server}:{settings.smtp_port}",
        event_type=EVENT_TYPE,
        dedupe_key=dedupe_key,
        payload=payload,
    )
    await session.commit()
    return MailingDeliveryResponse.model_validate(item)


@mailing_router.get(
    "/deliveries/{delivery_id}",
    response_model=MailingDeliveryResponse,
    summary="Get an email delivery status",
)
async def get_mailing_delivery(delivery_id: str, session: SessionDep) -> MailingDeliveryResponse:
    return MailingDeliveryResponse.model_validate(await mailing_delivery(session, delivery_id))


@mailing_router.post(
    "/deliveries/{delivery_id}/retry",
    status_code=202,
    response_model=MailingDeliveryResponse,
    summary="Queue a failed email delivery again",
    responses={409: {"description": "Only a terminally failed email can be retried."}},
)
async def retry_mailing_delivery(delivery_id: str, session: SessionDep) -> MailingDeliveryResponse:
    await begin_immediate(session)
    item = await mailing_delivery(session, delivery_id)
    if item.state != "failed":
        raise HTTPException(status_code=409, detail="Only a failed mailing delivery can be retried")
    await resend_delivery(session, item)
    await session.commit()
    return MailingDeliveryResponse.model_validate(item)
