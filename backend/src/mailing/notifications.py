from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.community.deliveries import enqueue_delivery
from src.core.config import Settings
from src.core.identity import OAuthAccount, User
from src.db.models import OutboundDelivery, Proposal

NotificationKind = Literal["submit_proposal", "review_update"]
CONTACT_EMAIL = "contact@ai4sbench.org"
_TEMPLATES = Environment(
    loader=PackageLoader("src.mailing", "assets"),
    autoescape=select_autoescape(enabled_extensions=("html",), default=False),
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class RenderedNotification:
    subject: str
    text: str
    html: str


def render_notification(kind: NotificationKind, title: str, proposal_url: str) -> RenderedNotification:
    if kind == "submit_proposal":
        subject = f"We received your proposal: {title}"
        heading = "We received your proposal"
    elif kind == "review_update":
        subject = f"A review update is available: {title}"
        heading = "Your proposal has a review update"
    else:
        raise ValueError(f"Unknown notification kind: {kind}")
    context = {
        "subject": subject,
        "heading": heading,
        "proposal_title": title,
        "proposal_url": proposal_url,
    }
    return RenderedNotification(
        subject=subject,
        text=_TEMPLATES.get_template(f"{kind}.txt").render(context).strip(),
        html=_TEMPLATES.get_template(f"{kind}.html").render(context).strip(),
    )


def render_bulk_message(subject: str, body: str, signature: str) -> RenderedNotification:
    context = {
        "subject": subject,
        "heading": subject,
        "body_lines": body.splitlines(),
        "body": body,
        "signature": signature,
    }
    return RenderedNotification(
        subject=subject,
        text=_TEMPLATES.get_template("bulk_message.txt").render(context).strip(),
        html=_TEMPLATES.get_template("bulk_message.html").render(context).strip(),
    )


async def author_email(session: AsyncSession, author_id: str, fallback: str | None = None) -> str | None:
    try:
        user_id = UUID(author_id)
    except ValueError:
        return fallback.strip() if fallback and fallback.strip() else None
    oauth_email = await session.scalar(
        select(OAuthAccount.account_email).where(
            OAuthAccount.user_id == user_id,
            OAuthAccount.oauth_name == "github",
        )
    )
    if oauth_email and oauth_email.strip():
        return oauth_email.strip()
    if fallback and fallback.strip():
        return fallback.strip()
    stored_email = await session.scalar(select(User.email).where(User.id == user_id))
    return stored_email.strip() if stored_email and stored_email.strip() else None


async def enqueue_author_notification(
    session: AsyncSession,
    settings: Settings,
    proposal: Proposal,
    kind: NotificationKind,
    *,
    fallback_email: str | None = None,
) -> OutboundDelivery | None:
    if not settings.smtp_enabled:
        return None
    recipient = await author_email(session, proposal.author_id or "", fallback_email)
    if recipient is None:
        return None
    if kind == "submit_proposal":
        event_id = proposal.id
        event_type = "proposal_submitted"
    else:
        event_id = proposal.review_comment_node_id
        event_type = "review_update"
    if not event_id:
        return None
    proposal_url = f"{settings.website_public_base_url.rstrip('/')}/tasks/task.html?id={proposal.id}"
    rendered = render_notification(kind, proposal.title, proposal_url)
    return await enqueue_delivery(
        session,
        delivery_type="smtp",
        destination=f"smtp://{settings.smtp_server}:{settings.smtp_port}",
        event_type=event_type,
        dedupe_key=f"smtp:{kind}:{event_id}",
        payload={
            "recipients": [recipient],
            "subject": rendered.subject,
            "text": rendered.text,
            "html": rendered.html,
            "reply_to": [CONTACT_EMAIL],
        },
    )
