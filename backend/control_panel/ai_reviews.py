"""Service-authenticated AI review intake and durable sticky publication.

A short publication lease serializes remote writes for each Discussion. Intake
retries while that lease is held; no database transaction spans network I/O.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_principal, require_ai_review_service
from .config import Settings
from .database import get_session
from .deliveries import complete_delivery, enqueue_delivery, fail_delivery, resend_delivery
from .models import AIReviewPublication, OutboundDelivery, Proposal, ProposalAIReview
from .schemas import AIReviewAccepted, AIReviewDiscordRecovery, AIReviewSubmission
from .services import begin_immediate

MARKER = "<!-- ai4sbench-proposal-ai-review:v1 -->"
EVENT = "proposal_ai_review"
ai_review_router = APIRouter(prefix="/api/v1", tags=["AI reviews"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class PublicationError(RuntimeError):
    def __init__(self, message: str, *, terminal: bool = False, response_status: int | None = None):
        super().__init__(message)
        self.terminal = terminal
        self.response_status = response_status


class PublicationBusy(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def leased(publication: AIReviewPublication) -> bool:
    return bool(publication.lease_until and publication.lease_until.replace(tzinfo=UTC) > now())


def proposal_digest(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()


def require_publish_config(settings: Settings) -> None:
    if (
        not settings.ai_review_github_token
        or not settings.discord_webhook_url
        or not settings.github_repository
    ):
        raise HTTPException(503, "AI review GitHub/Discord publishing is not configured")


async def github_request(settings: Settings, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    token = settings.ai_review_github_token
    if not token:
        raise PublicationError("AI review GitHub token is not configured", terminal=True)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            )
        if response.is_error:
            raise PublicationError("GitHub publication request failed", response_status=response.status_code)
        value = response.json()
        if value.get("errors") or not isinstance(value.get("data"), dict):
            raise PublicationError("GitHub returned GraphQL errors")
        return value["data"]
    except (httpx.HTTPError, ValueError) as error:
        # Do not include transport URLs/headers or arbitrary remote error text in logs.
        raise PublicationError("GitHub request failed or returned invalid JSON") from error


async def fetch_discussion(settings: Settings, repository: str, number: int) -> dict[str, Any]:
    owner, name = repository.split("/", 1)
    data = await github_request(
        settings,
        """
        query AIReviewDiscussion($owner:String!,$name:String!,$number:Int!) {
          repository(owner:$owner,name:$name) {
            discussion(number:$number) { id number title body url category { name } }
          }
        }
    """,
        {"owner": owner, "name": name, "number": number},
    )
    discussion = (data.get("repository") or {}).get("discussion")
    if not discussion:
        raise HTTPException(404, "Discussion not found")
    return discussion


def validate_discussion(body: AIReviewSubmission, discussion: dict[str, Any]) -> None:
    if discussion["id"] != body.discussion_node_id or discussion["number"] != body.discussion_number:
        raise HTTPException(409, "Discussion identity mismatch")
    if (discussion.get("category") or {}).get("name") != "Task Proposals":
        raise HTTPException(422, "Discussion is not in Task Proposals")
    if proposal_digest(discussion["title"], discussion["body"]) != body.proposal_digest:
        raise HTTPException(409, "Proposal changed since review; run a new review")


async def check_tombstone(session: AsyncSession, node_id: str) -> None:
    proposal = await session.scalar(select(Proposal).where(Proposal.discussion_node_id == node_id))
    if proposal and proposal.deleted_at:
        raise HTTPException(410, "Proposal was deleted")


async def accepted(session: AsyncSession, review: ProposalAIReview) -> dict[str, Any]:
    deliveries = await session.scalars(
        select(OutboundDelivery).where(
            OutboundDelivery.dedupe_key.in_(
                [f"ai-review:{review.id}:github_discussion", f"ai-review:{review.id}:discord"]
            )
        )
    )
    return {
        "review_id": review.id,
        "publication_id": review.publication_id,
        "deliveries": [
            {"id": item.id, "delivery_type": item.delivery_type, "state": item.state} for item in deliveries
        ],
    }


@ai_review_router.post(
    "/internal/proposal-ai-reviews",
    status_code=202,
    response_model=AIReviewAccepted,
    dependencies=[Depends(require_ai_review_service)],
)
async def submit_ai_review(body: AIReviewSubmission, request: Request, session: SessionDep):
    settings = request.app.state.settings
    require_publish_config(settings)
    if body.repository.lower() != settings.github_repository.lower():
        raise HTTPException(403, "Repository is not configured for AI reviews")
    body.repository = settings.github_repository.lower()
    document = body.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    existing_query = select(ProposalAIReview).where(
        ProposalAIReview.repository == body.repository,
        ProposalAIReview.run_id == body.run_id,
        ProposalAIReview.run_attempt == body.run_attempt,
    )
    existing = await session.scalar(existing_query)
    if existing:
        if existing.request_digest != digest:
            raise HTTPException(409, "Run/attempt already contains a different review")
        return await accepted(session, existing)
    await session.rollback()
    try:
        discussion = await fetch_discussion(settings, body.repository, body.discussion_number)
    except PublicationError as error:
        raise HTTPException(502, str(error)) from error
    validate_discussion(body, discussion)
    await begin_immediate(session)
    # Serialize concurrent duplicate requests, without holding the write lock during GitHub reads.
    existing = await session.scalar(existing_query)
    if existing:
        if existing.request_digest != digest:
            raise HTTPException(409, "Run/attempt already contains a different review")
        return await accepted(session, existing)
    await check_tombstone(session, body.discussion_node_id)
    publication = await session.scalar(
        select(AIReviewPublication).where(AIReviewPublication.discussion_node_id == body.discussion_node_id)
    )
    if publication and leased(publication):
        raise HTTPException(503, "Review publication in progress; retry", headers={"Retry-After": "10"})
    if publication and publication.repository != body.repository:
        raise HTTPException(409, "Discussion repository changed")
    if publication and publication.latest_review_id:
        latest = await session.get(ProposalAIReview, publication.latest_review_id)
        if latest and (body.run_id, body.run_attempt) < (latest.run_id, latest.run_attempt):
            raise HTTPException(409, "A newer review has already been accepted")
    if publication is None:
        publication = AIReviewPublication(
            discussion_node_id=body.discussion_node_id,
            discussion_number=body.discussion_number,
            repository=body.repository,
        )
        session.add(publication)
        await session.flush()
    domain_match = re.search(r"##\s*Scientific Domain[^\n]*\n+([^\n]+)", discussion["body"], re.I)
    domain_parts = domain_match[1].split(">") if domain_match else []
    field = domain_parts[1] if len(domain_parts) > 1 else domain_parts[0] if domain_parts else ""
    field = field.strip().lower().replace(" ", "-")
    pool = settings.ai_review_reviewers_by_field.get(field) or settings.ai_review_reviewer_logins
    if not publication.assigned_reviewer and pool:
        # Stable least-loaded domain pool, then the configured backup pool.
        assignments = list(await session.scalars(select(AIReviewPublication.assigned_reviewer)))
        candidates = [login for login in pool if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login)]
        if candidates:
            publication.assigned_reviewer = min(
                candidates, key=lambda login: (assignments.count(login), login)
            )
    review = ProposalAIReview(
        publication_id=publication.id,
        repository=body.repository,
        run_id=body.run_id,
        run_attempt=body.run_attempt,
        document=document,
        request_digest=digest,
    )
    session.add(review)
    await session.flush()
    publication.latest_review_id = review.id
    for channel in ("github_discussion", "discord"):
        await enqueue_delivery(
            session,
            delivery_type=channel,
            destination=f"ai-review:{publication.id}",
            event_type=EVENT,
            dedupe_key=f"ai-review:{review.id}:{channel}",
            payload={"review_id": review.id},
        )
    await session.commit()
    return await accepted(session, review)


@ai_review_router.get(
    "/internal/proposal-ai-reviews/{review_id}",
    response_model=AIReviewAccepted,
    dependencies=[Depends(require_ai_review_service)],
)
async def get_ai_review(review_id: str, session: SessionDep):
    review = await session.get(ProposalAIReview, review_id)
    if not review:
        raise HTTPException(404, "AI review not found")
    return await accepted(session, review)


def inline(value: str | None, limit: int = 1000) -> str:
    return (value or "Not available").replace("\n", " ").replace("\r", " ").replace("`", "'")[:limit]


def render_github(body: AIReviewSubmission, publication: AIReviewPublication) -> str:
    run_url = f"https://github.com/{body.repository}/actions/runs/{body.run_id}/attempts/{body.run_attempt}"
    result = body.result
    lines = [
        MARKER,
        "## AI proposal review",
        "",
        f"**Recommendation:** {result.decision if result else '⚠️ Review unavailable'}",
        "",
    ]
    if result:
        lines += [
            f"**Reason:** {inline(result.decision_reason)}",
            f"**Scientific domain:** {inline(result.scientific_domain)}",
            f"**Summary:** {inline(result.summary, 4000)}",
            f"**Justification:** {inline(result.justification, 4000)}",
            f"**Author:** {inline(result.author_name)}",
            f"**Author–task fit (advisory):** {result.author_fit or 'N/A'} — "
            f"{inline(result.author_fit_reason)}",
            f"**Conflict of interest (advisory):** {result.coi or 'N/A'} — {inline(result.coi_reason)}",
        ]
        for label, url in (
            ("Professional profile", result.author_profile),
            ("Academic profile", result.author_academic_profile),
        ):
            if url and url.startswith(("https://", "http://")):
                lines.append(f"**{label}:** {inline(url)}")
        lines += ["", "<details><summary>Full review</summary>", "", result.review, "", "</details>"]
    else:
        # Arbitrary provider error strings are never published (they can contain credentials).
        lines += [
            "The automated review did not complete. A maintainer with write/admin access can "
            "comment `/review` to retry. See the workflow run for diagnostics."
        ]
    if publication.assigned_reviewer:
        lines += ["", f"**Assigned reviewer:** @{publication.assigned_reviewer}"]
    lines += [
        "",
        "> Automated advice only; a human maintainer makes the final decision.",
        "> Text-only review: linked images were not inspected.",
        f"> Model: `{inline(body.model, 200)}` · [Workflow run]({run_url}) · "
        f"[Rubric](https://github.com/harbor-framework/terminal-bench-science/blob/"
        f"{body.upstream_sha}/rubrics/task-proposal.md)",
    ]
    return "\n".join(lines)


def render_discord(body: AIReviewSubmission, publication: AIReviewPublication, title: str) -> dict[str, Any]:
    result = body.result
    decision = result.decision if result else "Review unavailable"
    fields = []
    if result:
        for label, value in (
            ("Scientific domain", result.scientific_domain),
            ("Summary", result.summary),
            ("Justification", result.justification),
            ("Author", result.author_name),
            ("Professional profile", result.author_profile),
            ("Academic profile", result.author_academic_profile),
            ("Author–task fit (advisory)", f"{result.author_fit or 'N/A'}: {result.author_fit_reason or ''}"),
            ("Conflict of interest (advisory)", f"{result.coi or 'N/A'}: {result.coi_reason or ''}"),
        ):
            if value:
                fields.append({"name": label, "value": inline(value, 500)})
    url = publication.github_comment_url or (
        f"https://github.com/{body.repository}/discussions/{body.discussion_number}"
    )
    fields.append({"name": "GitHub review", "value": url})
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{decision} · {title}"[:256],
                "url": url,
                "description": "Automated advice; human approval is still required. Text-only review.",
                "color": 0x087A69
                if decision in {"Accept", "Strong Accept"}
                else (0xB42318 if decision in {"Reject", "Strong Reject"} else 0xD97706),
                "fields": fields,
                "footer": {"text": f"AI4S AI review {publication.id}"},
            }
        ],
    }


async def find_sticky(settings: Settings, publication: AIReviewPublication) -> dict[str, Any] | None:
    cursor = None
    while True:
        data = await github_request(
            settings,
            """
            query AIReviewSticky($id:ID!,$cursor:String) {
              viewer { login }
              node(id:$id) { ... on Discussion { comments(first:100,after:$cursor) {
                nodes { id url body author { login } }
                pageInfo { hasNextPage endCursor }
              } } }
            }
        """,
            {"id": publication.discussion_node_id, "cursor": cursor},
        )
        comments = data["node"]["comments"]
        for comment in comments["nodes"]:
            if (
                MARKER in comment["body"]
                and (comment.get("author") or {}).get("login") == data["viewer"]["login"]
            ):
                return comment
        if not comments["pageInfo"]["hasNextPage"]:
            return None
        cursor = comments["pageInfo"]["endCursor"]


async def publish_github(
    settings: Settings, body: AIReviewSubmission, publication: AIReviewPublication
) -> dict[str, str]:
    comment_id = publication.github_comment_id
    if not comment_id:
        sticky = await find_sticky(settings, publication)
        comment_id = sticky["id"] if sticky else None
    if comment_id:
        data = await github_request(
            settings,
            """
            mutation AIReviewUpdate($id:ID!,$body:String!) {
              updateDiscussionComment(input:{commentId:$id,body:$body}) { comment { id url } }
            }
        """,
            {"id": comment_id, "body": render_github(body, publication)},
        )
        return data["updateDiscussionComment"]["comment"]
    data = await github_request(
        settings,
        """
        mutation AIReviewCreate($id:ID!,$body:String!) {
          addDiscussionComment(input:{discussionId:$id,body:$body}) { comment { id url } }
        }
    """,
        {"id": publication.discussion_node_id, "body": render_github(body, publication)},
    )
    return data["addDiscussionComment"]["comment"]


async def sync_labels(settings: Settings, body: AIReviewSubmission) -> None:
    if not settings.ai_review_sync_labels or not body.result:
        return
    owner, name = body.repository.split("/", 1)
    wanted = f"author-fit: {body.result.author_fit.lower()}" if body.result.author_fit else None
    for label in (
        "author-fit: direct",
        "author-fit: adjacent",
        "author-fit: unrelated",
        "author-fit: coi disclosed",
    ):
        if label.endswith("coi disclosed") and body.result.coi != "Disclosed":
            continue  # COI disclosure is add-only.
        if not label.endswith("coi disclosed") and not wanted:
            continue
        data = await github_request(
            settings,
            """
            query AIReviewLabel($owner:String!,$name:String!,$label:String!) {
              repository(owner:$owner,name:$name) { label(name:$label) { id } }
            }
        """,
            {"owner": owner, "name": name, "label": label},
        )
        label_node = data["repository"]["label"]
        if not label_node:
            continue
        operation = (
            "addLabelsToLabelable"
            if label == wanted or label.endswith("coi disclosed")
            else "removeLabelsFromLabelable"
        )
        await github_request(
            settings,
            "mutation AIReviewLabels($id:ID!,$labels:[ID!]!) { "
            + operation
            + "(input:{labelableId:$id,labelIds:$labels}) { clientMutationId } }",
            {"id": body.discussion_node_id, "labels": [label_node["id"]]},
        )


async def discord_request(
    settings: Settings,
    method: str,
    *,
    message_id: str | None = None,
    thread_id: str | None = None,
    payload: dict | None = None,
) -> dict:
    if not settings.discord_webhook_url:
        raise PublicationError("Discord webhook is not configured", terminal=True)
    url = httpx.URL(settings.discord_webhook_url.get_secret_value())
    if message_id:
        url = url.copy_with(path=url.path.rstrip("/") + f"/messages/{message_id}")
    url = url.copy_merge_params({"wait": "true"})
    if thread_id:
        url = url.copy_merge_params({"thread_id": thread_id})
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, url, json=payload)
        if response.is_error:
            raise PublicationError("Discord request failed", response_status=response.status_code)
        value = response.json()
        if not value.get("id") or not value.get("channel_id"):
            raise PublicationError("Discord response has no message identity")
        return value
    except (httpx.HTTPError, ValueError) as error:
        raise PublicationError(
            "Discord response unavailable; reconcile initial sends before retrying"
        ) from error


async def acquire_publication(factory, delivery: OutboundDelivery):
    async with factory() as session:
        await begin_immediate(session)
        review = await session.get(ProposalAIReview, delivery.payload["review_id"])
        if not review:
            raise PublicationError("AI review record is missing", terminal=True)
        publication = await session.get(AIReviewPublication, review.publication_id)
        if publication.latest_review_id != review.id:
            await complete_delivery(session, delivery, 204, "Superseded by a newer review", None)
            await session.commit()
            return None
        if leased(publication):
            raise PublicationBusy()
        await check_tombstone(session, publication.discussion_node_id)
        publication.lease_token = str(uuid.uuid4())
        publication.lease_until = now() + timedelta(seconds=120)
        await session.commit()
        return review, publication


async def save_publication(factory, publication: AIReviewPublication, **values):
    async with factory() as session:
        await begin_immediate(session)
        current = await session.get(AIReviewPublication, publication.id)
        if current.lease_token != publication.lease_token or not leased(current):
            raise PublicationError("Publication lease expired")
        for key, value in values.items():
            setattr(current, key, value)
            setattr(publication, key, value)
        await session.commit()


async def publish_ai_delivery(factory, settings: Settings, delivery: OutboundDelivery) -> None:
    publication = None
    try:
        acquired = await acquire_publication(factory, delivery)
        if acquired is None:
            return
        review, publication = acquired
        body = AIReviewSubmission.model_validate(review.document)
        if body.repository.lower() != settings.github_repository.lower():
            raise PublicationError("Configured proposal repository changed", terminal=True)
        async with asyncio.timeout(60):
            discussion = await fetch_discussion(settings, body.repository, body.discussion_number)
            validate_discussion(body, discussion)
            if delivery.delivery_type == "github_discussion":
                comment = await publish_github(settings, body, publication)
                await save_publication(
                    factory, publication, github_comment_id=comment["id"], github_comment_url=comment["url"]
                )
                await github_request(
                    settings,
                    """
                    mutation AIReviewReaction($id:ID!) {
                      addReaction(input:{subjectId:$id,content:EYES}) { reaction { content } }
                    }
                """,
                    {"id": body.discussion_node_id},
                )
                await sync_labels(settings, body)
                message_url = comment["url"]
            else:
                if publication.discord_create_started and not publication.discord_message_id:
                    raise PublicationError(
                        "Discord initial send is uncertain; administrator recovery required", terminal=True
                    )
                webhook = await discord_request(settings, "GET")
                payload = render_discord(body, publication, discussion["title"])
                if publication.discord_message_id:
                    message = await discord_request(
                        settings,
                        "PATCH",
                        message_id=publication.discord_message_id,
                        thread_id=publication.discord_thread_id,
                        payload=payload,
                    )
                else:
                    if publication.discord_create_started:
                        raise PublicationError(
                            "Discord initial send is uncertain; use the administrator recovery endpoint",
                            terminal=True,
                        )
                    # Commit BEFORE POST. A process death must not cause an automatic duplicate.
                    await save_publication(factory, publication, discord_create_started=True)
                    if settings.ai_review_discord_forum:
                        payload["thread_name"] = (
                            f"AI review #{body.discussion_number} - {discussion['title']}"[:100]
                        )
                    try:
                        message = await discord_request(settings, "POST", payload=payload)
                    except PublicationError as error:
                        if error.response_status and 400 <= error.response_status < 500:
                            await save_publication(factory, publication, discord_create_started=False)
                        else:
                            error.terminal = True
                        raise
                guild_id = webhook.get("guild_id")
                message_url = (
                    f"https://discord.com/channels/{guild_id}/{message['channel_id']}/{message['id']}"
                    if guild_id
                    else None
                )
                await save_publication(
                    factory,
                    publication,
                    discord_message_id=message["id"],
                    discord_thread_id=message["channel_id"] if settings.ai_review_discord_forum else None,
                    discord_message_url=message_url,
                    discord_create_started=False,
                )
        async with factory() as session:
            await complete_delivery(session, delivery, 200, "AI review published", message_url)
            await session.commit()
    except PublicationBusy:
        async with factory() as session:
            current = await session.get(OutboundDelivery, delivery.id)
            current.state = "pending"
            current.attempts = max(0, current.attempts - 1)
            current.available_at = now() + timedelta(seconds=10)
            await session.commit()
    except Exception as error:
        async with factory() as session:
            current = await session.get(OutboundDelivery, delivery.id)
            terminal = getattr(error, "terminal", False) or isinstance(error, HTTPException)
            if terminal:
                current.attempts = current.max_attempts
            message = (
                str(error)
                if isinstance(error, PublicationError)
                else (
                    str(error.detail)
                    if isinstance(error, HTTPException)
                    else "AI publication failed; retry required"
                )
            )
            await fail_delivery(
                session, current, message, response_status=getattr(error, "response_status", None)
            )
            await session.commit()
    finally:
        if publication:
            async with factory() as session:
                await begin_immediate(session)
                current = await session.get(AIReviewPublication, publication.id)
                if current.lease_token == publication.lease_token:
                    current.lease_token = None
                    current.lease_until = None
                await session.commit()


@ai_review_router.post(
    "/ai-review-publications/{publication_id}/recover-discord",
    dependencies=[Depends(get_principal)],
    response_model=AIReviewAccepted,
)
async def recover_discord(
    publication_id: str, body: AIReviewDiscordRecovery, request: Request, session: SessionDep
):
    await begin_immediate(session)
    publication = await session.get(AIReviewPublication, publication_id)
    if not publication:
        raise HTTPException(404, "AI publication not found")
    if leased(publication):
        raise HTTPException(409, "Publication is in progress")
    if not publication.discord_create_started or publication.discord_message_id:
        raise HTTPException(409, "Publication has no uncertain initial Discord send")
    lease_token = str(uuid.uuid4())
    publication.lease_token = lease_token
    publication.lease_until = now() + timedelta(seconds=120)
    await session.commit()
    try:
        message = None
        if body.message_id:
            message = await discord_request(
                request.app.state.settings, "GET", message_id=body.message_id, thread_id=body.thread_id
            )
            marker = f"AI4S AI review {publication.id}"
            if not any(
                (embed.get("footer") or {}).get("text") == marker for embed in message.get("embeds", [])
            ):
                raise HTTPException(422, "Message does not belong to this AI review")
        await begin_immediate(session)
        await session.refresh(publication)
        if publication.lease_token != lease_token or not leased(publication):
            raise HTTPException(409, "Recovery lease expired; retry")
        if message:
            publication.discord_message_id = message["id"]
            publication.discord_thread_id = (
                message["channel_id"] if request.app.state.settings.ai_review_discord_forum else None
            )
        publication.discord_create_started = False
        delivery = await session.scalar(
            select(OutboundDelivery).where(
                OutboundDelivery.dedupe_key == f"ai-review:{publication.latest_review_id}:discord"
            )
        )
        if delivery:
            # A dead worker may have left the delivery in sending. The publication
            # lease above proves that no live sender currently owns this Discussion.
            if delivery.state == "sending":
                delivery.state = "failed"
            await resend_delivery(session, delivery)
        await session.commit()
        review = await session.get(ProposalAIReview, publication.latest_review_id)
        return await accepted(session, review)
    except PublicationError as error:
        raise HTTPException(502, str(error)) from error
    finally:
        await session.rollback()
        await begin_immediate(session)
        current = await session.get(AIReviewPublication, publication_id)
        if current.lease_token == lease_token:
            current.lease_token = None
            current.lease_until = None
        await session.commit()
