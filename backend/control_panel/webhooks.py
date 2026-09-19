from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import Proposal, WebhookDelivery
from .schemas import ProposalReview


class WebhookResponseError(RuntimeError):
    def __init__(self, status_code: int, response_body: str) -> None:
        self.status_code = status_code
        self.response_body = response_body[:20_000]
        super().__init__(f"Webhook returned HTTP {status_code}: {self.response_body[:500]}")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _trim(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _website_url(settings: Settings, proposal_id: str) -> str:
    return f"{settings.website_public_base_url.rstrip('/')}/tasks/task.html?id={proposal_id}"


def _discord_url(settings: Settings) -> str:
    return settings.discord_webhook_url.get_secret_value().strip() if settings.discord_webhook_url else ""


async def enqueue_delivery(
    session: AsyncSession,
    settings: Settings,
    *,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> WebhookDelivery | None:
    destination_url = _discord_url(settings)
    if not destination_url:
        return None
    existing = await session.scalar(select(WebhookDelivery).where(WebhookDelivery.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    delivery = WebhookDelivery(
        event_type=event_type,
        destination_url=destination_url,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def enqueue_proposal_notification(
    session: AsyncSession, settings: Settings, proposal: Proposal, form: dict[str, Any]
) -> WebhookDelivery | None:
    website_url = _website_url(settings, proposal.id)
    payload = {
        "thread_name": _trim(
            f"Proposal #{proposal.discussion_number or proposal.id[:8]} - {proposal.title}",
            100,
        ),
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
    return await enqueue_delivery(
        session,
        settings,
        event_type="proposal_created",
        dedupe_key=f"discord:proposal-created:{proposal.id}",
        payload=payload,
    )


async def enqueue_review_notification(
    session: AsyncSession,
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
        "thread_name": _trim(
            f"Review {decision_labels[review.review_decision]} - "
            f"Proposal #{proposal.discussion_number or proposal.id[:8]}",
            100,
        ),
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
    return await enqueue_delivery(
        session,
        settings,
        event_type="review_published",
        dedupe_key=f"discord:review-published:{proposal.review_comment_node_id}",
        payload=payload,
    )


async def claim_delivery(session: AsyncSession) -> WebhookDelivery | None:
    now = utcnow()
    stale = now - timedelta(minutes=5)
    candidate_id = await session.scalar(
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.available_at <= now,
            WebhookDelivery.attempts < WebhookDelivery.max_attempts,
            or_(
                WebhookDelivery.state == "pending",
                (WebhookDelivery.state == "sending") & (WebhookDelivery.updated_at < stale),
            ),
        )
        .order_by(WebhookDelivery.created_at)
        .limit(1)
    )
    if candidate_id is None:
        return None
    return await session.scalar(
        update(WebhookDelivery)
        .where(
            WebhookDelivery.id == candidate_id,
            or_(
                WebhookDelivery.state == "pending",
                (WebhookDelivery.state == "sending") & (WebhookDelivery.updated_at < stale),
            ),
        )
        .values(state="sending", attempts=WebhookDelivery.attempts + 1, updated_at=now)
        .returning(WebhookDelivery)
    )


async def discord_message_url(client: httpx.AsyncClient, destination_url: str, body: str) -> str | None:
    """Build a browser message URL from Discord's execute-webhook response.

    `wait=true` returns the created message and Forum thread IDs, but not the
    guild ID. The authenticated webhook metadata supplies that final component.
    Failure to read metadata must not turn an already-sent Discord message into
    a retryable delivery, which would create a duplicate post.
    """

    try:
        message = json.loads(body)
        message_id = str(message.get("id") or "").strip()
        channel_id = str(message.get("channel_id") or "").strip()
        webhook = await client.get(destination_url)
        webhook.raise_for_status()
        guild_id = str(webhook.json().get("guild_id") or "").strip()
    except (KeyError, TypeError, ValueError, httpx.HTTPError):
        return None
    if not (message_id and channel_id and guild_id):
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


async def send_delivery(delivery: WebhookDelivery) -> tuple[int, str, str | None]:
    separator = "&" if "?" in delivery.destination_url else "?"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{delivery.destination_url}{separator}wait=true", json=delivery.payload)
        body = response.text[:20_000]
        if response.is_error:
            raise WebhookResponseError(response.status_code, body)
        message_url = await discord_message_url(client, delivery.destination_url, body)
    return response.status_code, body, message_url


def proposal_id_from_delivery(delivery: WebhookDelivery) -> str | None:
    prefix = "discord:proposal-created:"
    if delivery.event_type != "proposal_created" or not delivery.dedupe_key.startswith(prefix):
        return None
    return delivery.dedupe_key.removeprefix(prefix) or None


async def complete_delivery(
    session: AsyncSession,
    delivery: WebhookDelivery,
    status_code: int,
    body: str,
    message_url: str | None,
) -> None:
    now = utcnow()
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery.id, WebhookDelivery.state == "sending")
        .values(
            state="completed",
            response_status=status_code,
            response_body=body,
            sent_at=now,
            last_error=None,
            updated_at=now,
        )
    )
    proposal_id = proposal_id_from_delivery(delivery)
    if proposal_id and message_url:
        await session.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id)
            .values(discord_message_url=message_url, updated_at=now)
        )


async def fail_delivery(
    session: AsyncSession,
    delivery: WebhookDelivery,
    error: str,
    *,
    response_status: int | None = None,
    response_body: str | None = None,
) -> None:
    terminal = delivery.attempts >= delivery.max_attempts
    delay = min(300, 2 ** max(0, delivery.attempts - 1))
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery.id, WebhookDelivery.state == "sending")
        .values(
            state="failed" if terminal else "pending",
            available_at=utcnow() + timedelta(seconds=delay),
            last_error=error[:2_000],
            response_status=response_status,
            response_body=response_body[:20_000] if response_body else None,
            updated_at=utcnow(),
        )
    )


async def resend_delivery(session: AsyncSession, delivery: WebhookDelivery) -> WebhookDelivery:
    if delivery.state == "sending":
        raise ValueError("A webhook delivery is already in progress")
    delivery.state = "pending"
    delivery.attempts = 0
    delivery.available_at = utcnow()
    delivery.last_error = None
    delivery.response_status = None
    delivery.response_body = None
    delivery.sent_at = None
    await session.flush()
    return delivery
