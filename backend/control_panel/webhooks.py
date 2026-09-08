from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import Proposal, WebhookDelivery
from .schemas import ProposalReview


def utcnow() -> datetime:
    return datetime.now(UTC)


def _trim(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _website_url(settings: Settings, proposal_id: str) -> str:
    return f"{settings.website_public_base_url.rstrip('/')}/tasks/task.html?id={proposal_id}"


def _discord_url(settings: Settings) -> str:
    return settings.discord_webhook_url.get_secret_value().strip() if settings.discord_webhook_url else ""


def enqueue_delivery(
    session: Session,
    settings: Settings,
    *,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> WebhookDelivery | None:
    destination_url = _discord_url(settings)
    if not destination_url:
        return None
    existing = session.scalar(select(WebhookDelivery).where(WebhookDelivery.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    delivery = WebhookDelivery(
        event_type=event_type,
        destination_url=destination_url,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    session.add(delivery)
    session.flush()
    return delivery


def enqueue_proposal_notification(
    session: Session, settings: Settings, proposal: Proposal, form: dict[str, Any]
) -> WebhookDelivery | None:
    website_url = _website_url(settings, proposal.id)
    payload = {
        "username": "AI4S-Bench",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"New proposal · #{proposal.discussion_number or proposal.id[:8]}",
                "url": website_url,
                "description": _trim(form.get("problem") or proposal.abstract, 1200),
                "color": 0x2474FF,
                "fields": [
                    {"name": "Title", "value": _trim(form.get("title") or proposal.title, 1000)},
                    {
                        "name": "Domain",
                        "value": _trim(form.get("domain") or proposal.domain, 1000),
                        "inline": True,
                    },
                    {
                        "name": "Field",
                        "value": _trim(form.get("field_name") or proposal.field, 1000),
                        "inline": True,
                    },
                    {"name": "Contributor", "value": f"@{_trim(proposal.author_login, 900)}", "inline": True},
                    {"name": "Discussion", "value": f"[Open on GitHub]({proposal.discussion_url})"},
                ],
                "footer": {"text": "AI4S-Bench proposal intake"},
            }
        ],
    }
    return enqueue_delivery(
        session,
        settings,
        event_type="proposal_created",
        dedupe_key=f"discord:proposal-created:{proposal.id}",
        payload=payload,
    )


def enqueue_review_notification(
    session: Session,
    settings: Settings,
    proposal: Proposal,
    review: ProposalReview,
    reviewer_login: str,
) -> WebhookDelivery | None:
    decision_labels = {
        "approved": "Approved",
        "changes_requested": "Changes requested",
        "rejected": "Rejected",
    }
    colors = {"approved": 0x087A69, "changes_requested": 0xD97706, "rejected": 0xB42318}
    payload = {
        "username": "AI4S-Bench",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{decision_labels[review.review_decision]} · {proposal.title}",
                "url": _website_url(settings, proposal.id),
                "description": _trim(review.review_short_description, 1200),
                "color": colors[review.review_decision],
                "fields": [
                    {
                        "name": "Proposal",
                        "value": f"#{proposal.discussion_number or proposal.id[:8]}",
                        "inline": True,
                    },
                    {"name": "Reviewer", "value": f"@{_trim(reviewer_login, 900)}", "inline": True},
                    {"name": "Difficulty", "value": _trim(review.review_difficulty, 1000), "inline": True},
                    {"name": "Primary metric", "value": _trim(review.review_primary_metric, 1000)},
                    {"name": "Discussion reply", "value": f"[Open review]({proposal.review_comment_url})"},
                ],
                "footer": {"text": "AI4S-Bench scientific review"},
            }
        ],
    }
    return enqueue_delivery(
        session,
        settings,
        event_type="review_published",
        dedupe_key=f"discord:review-published:{proposal.review_comment_node_id}",
        payload=payload,
    )


def claim_delivery(session: Session) -> WebhookDelivery | None:
    now = utcnow()
    candidate_id = session.scalar(
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.available_at <= now,
            WebhookDelivery.attempts < WebhookDelivery.max_attempts,
            WebhookDelivery.state == "pending",
        )
        .order_by(WebhookDelivery.created_at)
        .limit(1)
    )
    if candidate_id is None:
        return None
    return session.scalar(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == candidate_id, WebhookDelivery.state == "pending")
        .values(state="sending", attempts=WebhookDelivery.attempts + 1, updated_at=now)
        .returning(WebhookDelivery)
    )


def send_delivery(delivery: WebhookDelivery) -> tuple[int, str]:
    separator = "&" if "?" in delivery.destination_url else "?"
    with httpx.Client(timeout=15) as client:
        response = client.post(f"{delivery.destination_url}{separator}wait=true", json=delivery.payload)
    response.raise_for_status()
    return response.status_code, response.text[:20_000]


def complete_delivery(session: Session, delivery_id: str, status_code: int, body: str) -> None:
    now = utcnow()
    session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id, WebhookDelivery.state == "sending")
        .values(
            state="completed",
            response_status=status_code,
            response_body=body,
            sent_at=now,
            last_error=None,
            updated_at=now,
        )
    )


def fail_delivery(session: Session, delivery: WebhookDelivery, error: str) -> None:
    terminal = delivery.attempts >= delivery.max_attempts
    delay = min(300, 2 ** max(0, delivery.attempts - 1))
    session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery.id, WebhookDelivery.state == "sending")
        .values(
            state="failed" if terminal else "pending",
            available_at=utcnow() + timedelta(seconds=delay),
            last_error=error[:2_000],
            updated_at=utcnow(),
        )
    )


def resend_delivery(session: Session, delivery: WebhookDelivery) -> WebhookDelivery:
    if delivery.state == "sending":
        raise ValueError("A webhook delivery is already in progress")
    delivery.state = "pending"
    delivery.attempts = 0
    delivery.available_at = utcnow()
    delivery.last_error = None
    delivery.response_status = None
    delivery.response_body = None
    delivery.sent_at = None
    session.flush()
    return delivery
