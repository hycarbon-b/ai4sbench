from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.community.ai_reviews import EVENT as AI_REVIEW_EVENT
from src.community.deliveries import enqueue_delivery
from src.community.reviewer_selector import reviewer_matches_proposal
from src.db.models import AIReviewPublication, Proposal, ReviewerApplication


async def sync_ai_reviewer_comment(session: AsyncSession, proposal: Proposal) -> None:
    """Keep the existing AI review sticky comment aligned with the manual assignment."""
    if not proposal.discussion_node_id:
        return
    publication = await session.scalar(
        select(AIReviewPublication).where(
            AIReviewPublication.discussion_node_id == proposal.discussion_node_id
        )
    )
    if publication is None or publication.assigned_reviewer == proposal.assigned_reviewer_login:
        return
    publication.assigned_reviewer = proposal.assigned_reviewer_login
    if publication.latest_review_id and publication.github_comment_id:
        await enqueue_delivery(
            session,
            delivery_type="github_discussion",
            destination=f"ai-review:{publication.id}",
            event_type=AI_REVIEW_EVENT,
            dedupe_key=f"proposal-reviewer:{proposal.id}:{uuid.uuid4()}",
            payload={"review_id": publication.latest_review_id},
        )


async def reconcile_proposal_reviewer(session: AsyncSession, proposal: Proposal) -> None:
    """Clear assignments that no longer match a changed proposal or application."""
    if not proposal.assigned_reviewer_application_id:
        return
    application = await session.get(ReviewerApplication, proposal.assigned_reviewer_application_id)
    latest_id = (
        await session.scalar(
            select(ReviewerApplication.id)
            .where(ReviewerApplication.user_id == application.user_id)
            .order_by(ReviewerApplication.created_at.desc())
            .limit(1)
        )
        if application and application.user_id
        else None
    )
    if (
        application is None
        or latest_id != application.id
        or not reviewer_matches_proposal(proposal, application)
    ):
        proposal.assigned_reviewer_application_id = None
        proposal.assigned_reviewer_login = None
        proposal.reviewer_assigned_at = None
        proposal.reviewer_assigned_by = None
    else:
        proposal.assigned_reviewer_login = application.github
    await sync_ai_reviewer_comment(session, proposal)
