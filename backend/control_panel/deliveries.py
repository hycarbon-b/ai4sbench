from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import OutboundDelivery, Proposal
from .schemas import ProposalReview

DeliveryType = Literal["discord", "github_discussion", "smtp"]


class DeliveryResponseError(RuntimeError):
    def __init__(self, status_code: int, response_body: str) -> None:
        self.status_code = status_code
        self.response_body = response_body[:20_000]
        super().__init__(f"Delivery returned HTTP {status_code}: {self.response_body[:500]}")


class DeliveryConfigurationError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def _trim(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _website_url(settings: Settings, proposal_id: str) -> str:
    return f"{settings.website_public_base_url.rstrip('/')}/tasks/task.html?id={proposal_id}"


def _discord_destination(settings: Settings) -> str:
    return settings.discord_webhook_url.get_secret_value().strip() if settings.discord_webhook_url else ""


async def enqueue_delivery(
    session: AsyncSession,
    *,
    delivery_type: DeliveryType,
    destination: str,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> OutboundDelivery:
    if not destination.strip():
        raise ValueError("delivery destination must not be empty")
    existing = await session.scalar(select(OutboundDelivery).where(OutboundDelivery.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    delivery = OutboundDelivery(
        delivery_type=delivery_type,
        destination=destination,
        event_type=event_type,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def enqueue_discord_delivery(
    session: AsyncSession,
    settings: Settings,
    *,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> OutboundDelivery | None:
    destination = _discord_destination(settings)
    if not destination:
        return None
    return await enqueue_delivery(
        session,
        delivery_type="discord",
        destination=destination,
        event_type=event_type,
        dedupe_key=dedupe_key,
        payload=payload,
    )


async def enqueue_proposal_notification(
    session: AsyncSession, settings: Settings, proposal: Proposal, form: dict[str, Any]
) -> OutboundDelivery | None:
    payload = {
        "thread_name": _trim(
            f"Proposal #{proposal.discussion_number or proposal.id[:8]} - {proposal.title}", 100
        ),
        "username": "AI4S-Bench",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"New proposal · #{proposal.discussion_number or proposal.id[:8]}",
                "url": _website_url(settings, proposal.id),
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
    return await enqueue_discord_delivery(
        session,
        settings,
        event_type="proposal_created",
        dedupe_key=f"discord:proposal-created:{proposal.id}",
        payload=payload,
    )


async def enqueue_review_notification(
    session: AsyncSession, settings: Settings, proposal: Proposal, review: ProposalReview, reviewer_login: str
) -> OutboundDelivery | None:
    labels = {"approved": "Approved", "changes_requested": "Changes requested", "rejected": "Rejected"}
    colors = {"approved": 0x087A69, "changes_requested": 0xD97706, "rejected": 0xB42318}
    payload = {
        "thread_name": _trim(
            f"Review {labels[review.review_decision]} - "
            f"Proposal #{proposal.discussion_number or proposal.id[:8]}",
            100,
        ),
        "username": "AI4S-Bench",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{labels[review.review_decision]} · {proposal.title}",
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
    return await enqueue_discord_delivery(
        session,
        settings,
        event_type="review_published",
        dedupe_key=f"discord:review-published:{proposal.review_comment_node_id}",
        payload=payload,
    )


async def claim_delivery(session: AsyncSession) -> OutboundDelivery | None:
    now = utcnow()
    stale = now - timedelta(minutes=5)
    candidate_id = await session.scalar(
        select(OutboundDelivery.id)
        .where(
            OutboundDelivery.available_at <= now,
            OutboundDelivery.attempts < OutboundDelivery.max_attempts,
            or_(
                OutboundDelivery.state == "pending",
                (OutboundDelivery.state == "sending") & (OutboundDelivery.updated_at < stale),
            ),
        )
        .order_by(OutboundDelivery.created_at)
        .limit(1)
    )
    if candidate_id is None:
        return None
    return await session.scalar(
        update(OutboundDelivery)
        .where(
            OutboundDelivery.id == candidate_id,
            or_(
                OutboundDelivery.state == "pending",
                (OutboundDelivery.state == "sending") & (OutboundDelivery.updated_at < stale),
            ),
        )
        .values(state="sending", attempts=OutboundDelivery.attempts + 1, updated_at=now)
        .returning(OutboundDelivery)
    )


async def discord_message_url(client: httpx.AsyncClient, destination: str, body: str) -> str | None:
    try:
        message = json.loads(body)
        message_id = str(message.get("id") or "").strip()
        channel_id = str(message.get("channel_id") or "").strip()
        webhook = await client.get(destination)
        webhook.raise_for_status()
        guild_id = str(webhook.json().get("guild_id") or "").strip()
    except (KeyError, TypeError, ValueError, httpx.HTTPError):
        return None
    if not (message_id and channel_id and guild_id):
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


async def _send_discord(delivery: OutboundDelivery) -> tuple[int, str, str | None]:
    separator = "&" if "?" in delivery.destination else "?"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{delivery.destination}{separator}wait=true", json=delivery.payload)
        body = response.text[:20_000]
        if response.is_error:
            raise DeliveryResponseError(response.status_code, body)
        message_url = await discord_message_url(client, delivery.destination, body)
    return response.status_code, body, message_url


def _smtp_config(settings: Settings) -> ConnectionConfig:
    if not settings.smtp_enabled:
        raise DeliveryConfigurationError("SMTP delivery is disabled")
    if not settings.smtp_server or not settings.smtp_from:
        raise DeliveryConfigurationError("SMTP server and sender are not configured")
    return ConnectionConfig(
        MAIL_USERNAME=settings.smtp_username,
        MAIL_PASSWORD=settings.smtp_password.get_secret_value() if settings.smtp_password else "",
        MAIL_FROM=settings.smtp_from,
        MAIL_PORT=settings.smtp_port,
        MAIL_SERVER=settings.smtp_server,
        MAIL_FROM_NAME=settings.smtp_from_name,
        MAIL_STARTTLS=settings.smtp_starttls,
        MAIL_SSL_TLS=settings.smtp_ssl_tls,
        USE_CREDENTIALS=bool(settings.smtp_username),
        VALIDATE_CERTS=settings.smtp_validate_certs,
        TIMEOUT=settings.smtp_timeout_seconds,
    )


async def _send_smtp(delivery: OutboundDelivery, settings: Settings) -> tuple[int, str, None]:
    recipients = delivery.payload.get("recipients")
    subject = str(delivery.payload.get("subject") or "").strip()
    text = str(delivery.payload.get("text") or "").strip()
    html = delivery.payload.get("html")
    reply_to = delivery.payload.get("reply_to")
    if (
        not isinstance(recipients, list)
        or not recipients
        or not all(isinstance(item, str) and item.strip() for item in recipients)
    ):
        raise ValueError("SMTP payload requires a non-empty recipients list")
    if not subject or not text:
        raise ValueError("SMTP payload requires subject and text")
    if html is not None and not isinstance(html, str):
        raise ValueError("SMTP payload html must be a string when provided")
    if reply_to is not None and (
        not isinstance(reply_to, list) or not all(isinstance(item, str) for item in reply_to)
    ):
        raise ValueError("SMTP payload reply_to must be a list of strings when provided")
    message = MessageSchema(
        recipients=recipients,
        subject=subject,
        body=html or text,
        subtype=MessageType.html if html else MessageType.plain,
        reply_to=reply_to or [],
    )
    await FastMail(_smtp_config(settings)).send_message(message)
    return 250, "SMTP accepted message", None


async def send_delivery(delivery: OutboundDelivery, settings: Settings) -> tuple[int, str, str | None]:
    if delivery.delivery_type == "discord":
        return await _send_discord(delivery)
    if delivery.delivery_type == "smtp":
        return await _send_smtp(delivery, settings)
    raise DeliveryConfigurationError(
        "GitHub Discussion delivery is reserved for a future asynchronous workflow"
    )


def proposal_id_from_delivery(delivery: OutboundDelivery) -> str | None:
    prefix = "discord:proposal-created:"
    if delivery.delivery_type != "discord" or delivery.event_type != "proposal_created":
        return None
    return delivery.dedupe_key.removeprefix(prefix) if delivery.dedupe_key.startswith(prefix) else None


async def complete_delivery(
    session: AsyncSession, delivery: OutboundDelivery, status_code: int, body: str, message_url: str | None
) -> None:
    now = utcnow()
    await session.execute(
        update(OutboundDelivery)
        .where(OutboundDelivery.id == delivery.id, OutboundDelivery.state == "sending")
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
    delivery: OutboundDelivery,
    error: str,
    *,
    response_status: int | None = None,
    response_body: str | None = None,
) -> None:
    terminal = delivery.attempts >= delivery.max_attempts
    delay = min(300, 2 ** max(0, delivery.attempts - 1))
    await session.execute(
        update(OutboundDelivery)
        .where(OutboundDelivery.id == delivery.id, OutboundDelivery.state == "sending")
        .values(
            state="failed" if terminal else "pending",
            available_at=utcnow() + timedelta(seconds=delay),
            last_error=error[:2_000],
            response_status=response_status,
            response_body=response_body[:20_000] if response_body else None,
            updated_at=utcnow(),
        )
    )


async def resend_delivery(session: AsyncSession, delivery: OutboundDelivery) -> OutboundDelivery:
    if delivery.state == "sending":
        raise ValueError("An outbound delivery is already in progress")
    delivery.state = "pending"
    delivery.attempts = 0
    delivery.available_at = utcnow()
    delivery.last_error = None
    delivery.response_status = None
    delivery.response_body = None
    delivery.sent_at = None
    await session.flush()
    return delivery
