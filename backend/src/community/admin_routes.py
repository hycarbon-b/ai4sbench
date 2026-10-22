from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.community.deliveries import resend_delivery
from src.community.reviewer_assignment import reconcile_proposal_reviewer, sync_ai_reviewer_comment
from src.community.reviewer_selector import reviewer_selector
from src.community.schemas import (
    AdminProposalListResponse,
    AdminUserListResponse,
    AdminUserResponse,
    OutboundDeliveryListResponse,
    OutboundDeliveryResponse,
    ProposalReviewerAssignmentResponse,
    ProposalReviewerAssignmentUpdate,
    ProposalReviewerCandidateListResponse,
    ReviewerApplicationListResponse,
    ReviewerApplicationResponse,
    ReviewerApplicationUpdate,
)
from src.core.auth import Principal, get_principal
from src.core.identity import User
from src.db.database import begin_immediate, get_session
from src.db.models import OutboundDelivery, Proposal, ReviewerApplication

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[Principal, Depends(get_principal)]
admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_principal)])


@admin_router.get(
    "/users",
    tags=["users"],
    response_model=AdminUserListResponse,
    summary="List registered users and email authorization status",
)
async def list_users(session: SessionDep) -> AdminUserListResponse:
    users = (await session.execute(select(User).order_by(User.github_login, User.email))).unique().scalars()
    applications = await session.scalars(
        select(ReviewerApplication).order_by(ReviewerApplication.created_at.desc())
    )
    application_by_user = {}
    for application in applications:
        if application.user_id is not None:
            application_by_user.setdefault(application.user_id, application)
    return {
        "items": [
            AdminUserResponse(
                id=str(user.id),
                email=user.email,
                name=(application_by_user[user.id].name if user.id in application_by_user else None),
                github_login=user.github_login,
                role=user.role,
                email_status=user.email_status,
                reviewer_application_status=(
                    application_by_user[user.id].status if user.id in application_by_user else None
                ),
                can_review=(
                    user.role == "admin"
                    or (user.id in application_by_user and application_by_user[user.id].status == "approved")
                ),
                github_linked=any(account.oauth_name == "github" for account in user.oauth_accounts),
            )
            for user in users
        ]
    }


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
    assigned_proposals = await session.scalars(
        select(Proposal).where(Proposal.assigned_reviewer_application_id == item.id)
    )
    for proposal in assigned_proposals:
        await reconcile_proposal_reviewer(session, proposal)
    await session.commit()
    await session.refresh(item)
    return item


async def active_assignment_proposal(session: AsyncSession, proposal_id: str) -> Proposal:
    proposal = await session.get(Proposal, proposal_id)
    if proposal is None or proposal.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


@admin_router.get(
    "/admin/proposals",
    tags=["proposals"],
    response_model=AdminProposalListResponse,
    summary="List active proposals with reviewer assignments",
)
async def list_admin_proposals(session: SessionDep) -> AdminProposalListResponse:
    proposals = await session.scalars(
        select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.created_at.desc())
    )
    def field_name(proposal: Proposal) -> str:
        document = proposal.document if isinstance(proposal.document, dict) else {}
        form = document.get("form_payload", document)
        return str(form.get("field_name") or proposal.field) if isinstance(form, dict) else proposal.field

    return {
        "items": [
            {
                "id": proposal.id,
                "title": proposal.title,
                "domain": proposal.domain,
                "field": proposal.field,
                "field_name": field_name(proposal),
                "task_slug": proposal.task_slug,
                "status": proposal.status,
                "discussion_url": proposal.discussion_url,
                "discussion_number": proposal.discussion_number,
                "discord_message_url": proposal.discord_message_url,
                "author_login": proposal.author_login,
                "input_valid": proposal.input_valid,
                "assigned_reviewer_application_id": proposal.assigned_reviewer_application_id,
                "assigned_reviewer_login": proposal.assigned_reviewer_login,
                "reviewer_assigned_at": proposal.reviewer_assigned_at,
                "reviewer_assigned_by": proposal.reviewer_assigned_by,
            }
            for proposal in proposals
        ]
    }


@admin_router.get(
    "/proposals/{proposal_id}/reviewer-candidates",
    tags=["proposals"],
    response_model=ProposalReviewerCandidateListResponse,
    summary="List eligible reviewers for a proposal",
)
async def proposal_reviewer_candidates(
    proposal_id: str, session: SessionDep
) -> ProposalReviewerCandidateListResponse:
    proposal = await active_assignment_proposal(session, proposal_id)
    applications = await session.scalars(
        select(ReviewerApplication).order_by(ReviewerApplication.created_at.desc())
    )
    return {
        "items": [
            {
                "id": application.id,
                "name": application.name,
                "github": application.github,
                "email": application.email,
                "affiliation": application.affiliation,
                "role": application.role,
                "domain": application.domain,
                "field_name": application.field_name,
                "research_background": application.research_background,
            }
            for application in reviewer_selector(proposal, applications)
        ]
    }


@admin_router.patch(
    "/proposals/{proposal_id}/reviewer",
    tags=["proposals"],
    response_model=ProposalReviewerAssignmentResponse,
    summary="Assign, change, or clear a proposal reviewer",
    responses={
        404: {"description": "Proposal not found."},
        422: {"description": "Reviewer is not approved or is incompatible with the proposal."},
    },
)
async def assign_proposal_reviewer(
    proposal_id: str,
    body: ProposalReviewerAssignmentUpdate,
    session: SessionDep,
    principal: AdminDep,
) -> ProposalReviewerAssignmentResponse:
    await begin_immediate(session)
    proposal = await active_assignment_proposal(session, proposal_id)
    if body.reviewer_application_id is None:
        proposal.assigned_reviewer_application_id = None
        proposal.assigned_reviewer_login = None
        proposal.reviewer_assigned_at = None
        proposal.reviewer_assigned_by = None
    else:
        applications = await session.scalars(
            select(ReviewerApplication).order_by(ReviewerApplication.created_at.desc())
        )
        candidate = next(
            (
                application
                for application in reviewer_selector(proposal, applications)
                if application.id == body.reviewer_application_id
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Choose an approved reviewer whose domain matches this proposal "
                    "and who is not its author"
                ),
            )
        if proposal.assigned_reviewer_application_id != candidate.id:
            proposal.assigned_reviewer_application_id = candidate.id
            proposal.assigned_reviewer_login = candidate.github
            proposal.reviewer_assigned_at = datetime.now(UTC)
            proposal.reviewer_assigned_by = principal.subject
    await sync_ai_reviewer_comment(session, proposal)
    await session.commit()
    return {
        "proposal_id": proposal.id,
        "assigned_reviewer_application_id": proposal.assigned_reviewer_application_id,
        "assigned_reviewer_login": proposal.assigned_reviewer_login,
        "reviewer_assigned_at": proposal.reviewer_assigned_at,
        "reviewer_assigned_by": proposal.reviewer_assigned_by,
    }


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
