from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.community.deliveries import resend_delivery
from src.community.schemas import (
    OutboundDeliveryListResponse,
    OutboundDeliveryResponse,
    ReviewerApplicationListResponse,
    ReviewerApplicationResponse,
    ReviewerApplicationUpdate,
)
from src.core.auth import Principal, get_principal
from src.db.database import get_session
from src.db.models import OutboundDelivery, ReviewerApplication

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[Principal, Depends(get_principal)]
admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_principal)])


@admin_router.get(
    "/reviewer-applications",
    tags=["reviewers"],
    response_model=ReviewerApplicationListResponse,
    summary="List reviewer applications",
    description="Returns the complete applicant dossiers and administrator decisions, newest first.",
)
async def list_reviewer_applications(session: SessionDep) -> ReviewerApplicationListResponse:
    items = await session.scalars(select(ReviewerApplication).order_by(ReviewerApplication.created_at.desc()))
    return {"items": list(items)}


@admin_router.get(
    "/reviewer-applications/{application_id}",
    tags=["reviewers"],
    response_model=ReviewerApplicationResponse,
    summary="Get one reviewer application",
    responses={404: {"description": "The reviewer application does not exist."}},
)
async def get_reviewer_application(
    application_id: str,
    session: SessionDep,
) -> ReviewerApplicationResponse:
    item = await session.get(ReviewerApplication, application_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Reviewer application not found")
    return item


@admin_router.patch(
    "/reviewer-applications/{application_id}",
    tags=["reviewers"],
    response_model=ReviewerApplicationResponse,
    summary="Manage a reviewer application",
    description=(
        "Updates the GitHub identity, private administrator notes, or decision. "
        "An approved application with a GitHub username grants reviewer access immediately."
    ),
    responses={404: {"description": "The reviewer application does not exist."}},
)
async def update_reviewer_application(
    application_id: str,
    body: ReviewerApplicationUpdate,
    session: SessionDep,
    principal: AdminDep,
) -> ReviewerApplicationResponse:
    item = await session.get(ReviewerApplication, application_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Reviewer application not found")

    if "github" in body.model_fields_set:
        item.github = body.github
    if "admin_notes" in body.model_fields_set:
        item.admin_notes = body.admin_notes
    if "status" in body.model_fields_set and body.status is not None:
        item.status = body.status
        if body.status == "pending":
            item.reviewed_by = None
            item.reviewed_at = None
        else:
            item.reviewed_by = principal.subject
            item.reviewed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(item)
    return item


def outbound_delivery_dict(item: OutboundDelivery) -> dict[str, object]:
    return {
        "id": item.id,
        "event_type": item.event_type,
        "delivery_type": item.delivery_type,
        "destination": item.destination,
        "payload": item.payload,
        "dedupe_key": item.dedupe_key,
        "state": item.state,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "available_at": item.available_at,
        "last_error": item.last_error,
        "response_status": item.response_status,
        "response_body": item.response_body,
        "sent_at": item.sent_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@admin_router.get(
    "/deliveries",
    tags=["operations"],
    response_model=OutboundDeliveryListResponse,
    summary="List outbound deliveries",
)
async def list_outbound_deliveries(session: SessionDep) -> OutboundDeliveryListResponse:
    items = await session.scalars(
        select(OutboundDelivery).order_by(OutboundDelivery.created_at.desc()).limit(100)
    )
    return {"items": [outbound_delivery_dict(item) for item in items]}


@admin_router.post(
    "/deliveries/{delivery_id}/resend",
    tags=["operations"],
    response_model=OutboundDeliveryResponse,
    summary="Queue an outbound delivery again",
    responses={
        404: {"description": "The outbound delivery does not exist."},
        409: {"description": "The outbound delivery is currently being sent."},
    },
)
async def post_resend_outbound_delivery(delivery_id: str, session: SessionDep) -> OutboundDeliveryResponse:
    delivery = await session.get(OutboundDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Outbound delivery not found")
    try:
        await resend_delivery(session, delivery)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return outbound_delivery_dict(delivery)
