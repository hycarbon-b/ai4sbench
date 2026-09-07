from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session
from .identity import OAuthAccount, User, current_active_user, current_optional_user
from .models import CloudProfile, Proposal
from .schemas import (
    PROPOSAL_DOMAIN_OPTIONS,
    AuthConfigResponse,
    AuthenticatedUserResponse,
    CloudProfileCreate,
    CloudProfileListResponse,
    CloudProfileResponse,
    ProposalDomainListResponse,
    ProposalListResponse,
    ProposalPreviewResponse,
    ProposalPublishedResponse,
    ProposalSubmission,
    ProposalSyncResponse,
    PullRequestInstructionsResponse,
)

community_router = APIRouter(prefix="/api/v1", tags=["community"])
SessionDep = Annotated[Session, Depends(get_session)]
UserDep = Annotated[User, Depends(current_active_user)]
OptionalUserDep = Annotated[User | None, Depends(current_optional_user)]


def user_dict(user: User) -> dict[str, str]:
    return {
        "id": str(user.id),
        "email": user.email,
        "github_login": user.github_login or "",
        "role": user.role,
    }


def require_admin(user: UserDep) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user


AdminDep = Annotated[User, Depends(require_admin)]


@community_router.get(
    "/auth/me",
    summary="Get the signed-in Website user",
    description=(
        "Returns the current Dashboard cookie session. A 401 means the visitor must sign in with GitHub."
    ),
)
async def me(user: UserDep) -> AuthenticatedUserResponse:
    return user_dict(user)


@community_router.get(
    "/auth/config",
    summary="Read public GitHub-login availability",
    description="Returns only whether GitHub OAuth is configured; no credential values are exposed.",
)
def auth_config(request: Request) -> AuthConfigResponse:
    settings = request.app.state.settings
    return {
        "github_login_enabled": bool(settings.github_oauth_client_id and settings.github_oauth_client_secret)
    }


@community_router.get(
    "/proposal-domains",
    summary="List selectable proposal domains",
    description="Provides the shared multi-select options for the Website and Dashboard proposal forms.",
    response_model=ProposalDomainListResponse,
)
def proposal_domains() -> ProposalDomainListResponse:
    return {"items": list(PROPOSAL_DOMAIN_OPTIONS)}


@community_router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of the Website session",
    description="Clears the local control-plane cookie. It does not revoke the user's GitHub authorization.",
)
def logout(request: Request) -> Response:
    """Clear only the local control-panel session; GitHub authorization is left intact."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    settings = request.app.state.settings
    cross_origin = bool(settings.cors_origins)
    response.delete_cookie(
        "ai4sbench_session",
        path="/",
        secure=settings.environment == "production" or cross_origin,
        httponly=True,
        samesite="none" if cross_origin else "lax",
    )
    return response


@community_router.get(
    "/proposals",
    summary="List recent task proposals",
    description=(
        "Publicly lists the 50 newest locally tracked proposal Discussions without their full form content."
    ),
)
def list_proposals(session: SessionDep) -> ProposalListResponse:
    items = session.scalars(select(Proposal).order_by(Proposal.created_at.desc()).limit(50))
    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "domain": item.domain,
                "field": item.field,
                "task_slug": item.task_slug,
                "status": item.status,
                "discussion_url": item.discussion_url,
                "discussion_number": item.discussion_number,
                "author_login": item.author_login,
                "input_valid": item.input_valid,
            }
            for item in items
        ]
    }


TASK_PROPOSALS_CATEGORY = "Task Proposals"
PROPOSAL_TITLE_RE = re.compile(r"^\s*\[\s*Task Proposal\s*#\d+\s*\]\s*(.+)$", re.IGNORECASE)

DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 100, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title url body closed createdAt updatedAt
        category { name }
        author { login }
        labels(first: 30) { nodes { name } }
      }
    }
  }
}
"""


def github_oauth_account(user: User) -> OAuthAccount:
    github_account = next(
        (account for account in user.oauth_accounts if account.oauth_name == "github"), None
    )
    if github_account is None:
        raise HTTPException(status_code=401, detail="GitHub account is not linked")
    return github_account


async def refresh_github_access_token(request: Request, user: User) -> str:
    github_account = github_oauth_account(user)
    settings = request.app.state.settings
    if not github_account.refresh_token or not settings.github_oauth_client_secret:
        raise HTTPException(status_code=401, detail="GitHub authorization expired; sign in again")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret.get_secret_value(),
                "grant_type": "refresh_token",
                "refresh_token": github_account.refresh_token,
            },
            headers={"Accept": "application/json"},
        )
    is_json = response.headers.get("content-type", "").startswith("application/json")
    payload = response.json() if is_json else {}
    access_token = str(payload.get("access_token") or "")
    if response.status_code >= 400 or not access_token:
        raise HTTPException(status_code=401, detail="GitHub authorization expired; sign in again")
    expires_in = payload.get("expires_in")
    async with request.app.state.auth_session_factory() as session:
        persisted = await session.get(OAuthAccount, github_account.id)
        if persisted is None:
            raise HTTPException(status_code=401, detail="GitHub account is not linked")
        persisted.access_token = access_token
        persisted.refresh_token = str(payload.get("refresh_token") or github_account.refresh_token)
        persisted.expires_at = int(time.time()) + int(expires_in) if expires_in else None
        await session.commit()
    return access_token


async def github_access_token(request: Request, user: User) -> str:
    github_account = github_oauth_account(user)
    if github_account.expires_at and github_account.expires_at <= int(time.time()) + 60:
        return await refresh_github_access_token(request, user)
    return github_account.access_token


def parse_github_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def markdown_section(body: str, heading: str, fallback: str = "") -> str:
    match = re.search(
        rf"(?ims)^##\s*{re.escape(heading)}\s*$\n+(.+?)(?=^##\s|\Z)", body or ""
    )
    return match.group(1).strip() if match else fallback


def markdown_subsection(body: str, heading: str, fallback: str = "") -> str:
    match = re.search(
        rf"(?ims)^###\s*{re.escape(heading)}\s*$\n+(.+?)(?=^##?\s|\Z)", body or ""
    )
    return match.group(1).strip() if match else fallback


def markdown_line(body: str, label: str) -> str | None:
    match = re.search(rf"(?im)^{re.escape(label)}\s*:\s*(.+?)\s*$", body or "")
    return match.group(1).strip() if match else None


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def form_payload_from_discussion(node: dict[str, Any]) -> dict[str, str]:
    """Extract the current Website form fields from a public Discussion.

    Earlier Discussion layouts intentionally remain importable as raw records,
    but they do not pass this new form contract and are flagged in the database.
    """

    body = str(node.get("body") or "")
    raw_title = str(node.get("title") or "Untitled proposal")
    title_match = PROPOSAL_TITLE_RE.match(raw_title)
    title = title_match.group(1).strip() if title_match else raw_title
    domain_path = markdown_section(body, "Scientific Domain")
    parts = [part.strip() for part in domain_path.split(">")]
    author_login = markdown_line(body, "GitHub") or ((node.get("author") or {}).get("login"))
    if author_login and "github.com/" in author_login:
        author_login = author_login.rstrip("/").split("/")[-1]
    return {
        "title": title,
        "domain": parts[0] if parts else "",
        "field_name": parts[-1] if len(parts) > 1 else "",
        "problem": markdown_section(body, "Scientific Problem"),
        "solvability": markdown_section(body, "Solvability"),
        "references": markdown_section(body, "References & Resources"),
        "software": markdown_subsection(body, "Software and tools"),
        "dataset": markdown_subsection(body, "Dataset & artifacts"),
        "compute": markdown_subsection(body, "Computation resources (time and device)"),
        "workflow": markdown_subsection(body, "Expected workflow & outputs"),
        "evaluation": markdown_subsection(body, "How will this task be evaluated?"),
        "leakage": markdown_subsection(body, "Is there risk of cheating and leakage?"),
        "name": markdown_line(body, "Name") or "",
        "affiliation": markdown_line(body, "Institution / affiliation") or "",
        "github": str(author_login or ""),
    }


def validation_errors_for_document(error: ValidationError) -> list[dict[str, Any]]:
    """Return Pydantic errors in a JSON-column-safe representation."""

    return json.loads(error.json(include_url=False))


def discussion_status(labels: list[str]) -> str:
    if any(label.startswith("proposal-approved") for label in labels):
        return "approved"
    if any(label.startswith("proposal-declined") for label in labels):
        return "rejected"
    return "pending"


async def create_github_discussion(
    request: Request, user: User, submission: ProposalSubmission
) -> dict[str, object]:
    settings = request.app.state.settings
    if not settings.github_repository_node_id or not settings.github_discussion_category_id:
        raise HTTPException(status_code=503, detail="GitHub Discussion destination is not configured")
    payload = {
        "query": (
            "mutation($input: CreateDiscussionInput!) { "
            "createDiscussion(input: $input) { discussion { id number url } } }"
        ),
        "variables": {
            "input": {
                "repositoryId": settings.github_repository_node_id,
                "categoryId": settings.github_discussion_category_id,
                "title": submission.title,
                "body": submission.render_discussion(),
            }
        },
    }
    token = await github_access_token(request, user)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.github.com/graphql",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if response.status_code == 401:
            token = await refresh_github_access_token(request, user)
            response = await client.post(
                "https://api.github.com/graphql",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="GitHub authorization expired; sign in again")
    if response.status_code >= 400 or response.json().get("errors"):
        raise HTTPException(status_code=502, detail="GitHub could not create the proposal discussion")
    discussion = response.json()["data"]["createDiscussion"]["discussion"]
    return {
        "id": str(discussion["id"]),
        "number": int(discussion["number"]),
        "url": str(discussion["url"]),
    }


def proposal_preview(submission: ProposalSubmission) -> dict[str, object]:
    """Return the exact normalized submission and Discussion content.

    This is deliberately shared by preview and publish responses so the Website
    never has to duplicate the server-side rendering template.
    """

    return {
        "input": submission.model_dump(mode="json"),
        "derived": {
            "domain": submission.domain,
            "field": submission.field_slug,
            "task_slug": submission.task_slug,
        },
        "discussion": {
            "title": submission.title,
            "body": submission.render_discussion(),
        },
    }


@community_router.post(
    "/proposals/preview",
    summary="Preview the exact GitHub Discussion",
    description=(
        "Validates a Website proposal and returns its normalized input, derived identifiers and the "
        "exact Discussion title and Markdown body. This endpoint never writes the database or GitHub. "
        "Without a body it returns the required fields; with an authenticated session it uses that "
        "GitHub identity exactly as publishing does."
    ),
    response_model=ProposalPreviewResponse,
)
async def preview_proposal(
    user: OptionalUserDep, body: ProposalSubmission | None = None
) -> ProposalPreviewResponse:
    """Render a proposal exactly as publishing would, without side effects.

    Publishing replaces the form's GitHub field with the authenticated
    contributor identity. Before sign-in, the form value is used so visitors
    can still inspect the rendered Discussion.
    """

    if body is None:
        return {
            "input": None,
            "derived": None,
            "discussion": None,
            "github_identity_source": None,
            "missing_fields": [
                name for name, field in ProposalSubmission.model_fields.items() if field.is_required()
            ],
        }
    submission = body.with_github_identity(user.github_login) if user else body
    return {
        **proposal_preview(submission),
        "github_identity_source": "authenticated" if user else "form",
    }


@community_router.post(
    "/proposals",
    status_code=status.HTTP_201_CREATED,
    summary="Publish a proposal as a GitHub Discussion",
    description=(
        "Requires a signed-in GitHub user. Creates one Discussion, persists its local tracking record, "
        "and returns the exact normalized input and rendered content used for publication."
    ),
    responses={
        401: {"description": "GitHub sign-in is missing or expired."},
        502: {"description": "GitHub did not accept the Discussion creation request."},
        503: {"description": "The GitHub Discussion destination is not configured."},
    },
    response_model=ProposalPublishedResponse,
    response_model_exclude_none=True,
)
async def create_proposal(
    body: ProposalSubmission, request: Request, session: SessionDep, user: UserDep
) -> ProposalPublishedResponse:
    submission = body.with_github_identity(user.github_login)
    discussion = await create_github_discussion(request, user, submission)
    item = Proposal(
        author_id=str(user.id),
        author_login=user.github_login,
        title=submission.title,
        abstract=submission.problem,
        domain=submission.domain,
        field=submission.field_slug,
        task_slug=submission.task_slug,
        evidence=submission.references,
        document=submission.model_dump(mode="json"),
        input_valid=True,
        status="pending",
        discussion_url=str(discussion["url"]),
        discussion_node_id=str(discussion["id"]),
        discussion_number=int(discussion["number"]),
    )
    session.add(item)
    session.commit()
    return {
        "id": item.id,
        "status": item.status,
        "discussion_url": item.discussion_url,
        **proposal_preview(submission),
    }


async def fetch_all_discussions(settings: object, token: str) -> list[dict[str, Any]]:
    repository = str(getattr(settings, "github_repository", "")).strip().strip("/")
    if repository.count("/") != 1:
        raise HTTPException(
            status_code=503, detail="GitHub repository must be configured as owner/repository"
        )
    owner, name = repository.split("/", 1)
    cursor: str | None = None
    nodes: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=45) as client:
        while True:
            response = await client.post(
                "https://api.github.com/graphql",
                json={
                    "query": DISCUSSIONS_QUERY,
                    "variables": {"owner": owner, "name": name, "cursor": cursor},
                },
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            payload = response.json()
            if response.status_code >= 400 or payload.get("errors"):
                raise HTTPException(status_code=502, detail="GitHub could not scan proposal discussions")
            connection = payload["data"]["repository"]["discussions"]
            nodes.extend(connection["nodes"])
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                return nodes
            cursor = page["endCursor"]


@community_router.post(
    "/proposals/sync-discussions",
    summary="Synchronize proposal Discussions",
    description=(
        "Administrator-only import of configured GitHub proposal Discussions into the local "
        "tracking database."
    ),
)
async def sync_proposal_discussions(
    request: Request, session: SessionDep, user: AdminDep
) -> ProposalSyncResponse:
    """Rebuild proposal records from Discussions and mark legacy layouts invalid."""

    nodes = await fetch_all_discussions(request.app.state.settings, await github_access_token(request, user))
    created_count = 0
    updated_count = 0
    scanned_count = 0
    invalid_count = 0
    for node in nodes:
        if (node.get("category") or {}).get("name") != TASK_PROPOSALS_CATEGORY:
            continue
        scanned_count += 1
        form_payload = form_payload_from_discussion(node)
        try:
            submission = ProposalSubmission.model_validate(form_payload)
            input_valid = True
            document = submission.model_dump(mode="json")
            title = submission.title
            abstract = submission.problem
            domain = submission.domain
            field = submission.field_slug
            task_slug = submission.task_slug
            evidence = submission.references
            author_login = submission.github
        except ValidationError as error:
            input_valid = False
            invalid_count += 1
            document = {
                "source": "github-discussion",
                "form_payload": form_payload,
                "validation_errors": validation_errors_for_document(error),
            }
            title = form_payload["title"][:160] or "Untitled proposal"
            abstract = form_payload["problem"] or str(node.get("body") or "")
            domain = slugify(form_payload["domain"]) or "unknown"
            field = slugify(form_payload["field_name"]) or "unknown"
            task_slug = slugify(title) or "untitled-proposal"
            evidence = form_payload["references"] or "Not provided"
            author_login = form_payload["github"] or None
        labels = [str(label["name"]) for label in (node.get("labels") or {}).get("nodes", [])]
        values = {
            "author_login": author_login,
            "title": title,
            "abstract": abstract,
            "domain": domain,
            "field": field,
            "task_slug": task_slug,
            "evidence": evidence,
            "document": document,
            "input_valid": input_valid,
            "status": discussion_status(labels),
            "discussion_url": str(node["url"]),
            "discussion_node_id": str(node["id"]),
            "discussion_number": int(node["number"]),
            "github_created_at": parse_github_time(node.get("createdAt")),
            "github_updated_at": parse_github_time(node.get("updatedAt")),
        }
        existing = session.scalar(
            select(Proposal).where(Proposal.discussion_node_id == values["discussion_node_id"])
        )
        if existing is None:
            existing = session.scalar(
                select(Proposal).where(Proposal.discussion_url == values["discussion_url"])
            )
        if existing is None:
            author_id = str(uuid.uuid5(uuid.NAMESPACE_URL, values["discussion_url"]))
            session.add(Proposal(author_id=author_id, **values))
            created_count += 1
            continue
        if any(getattr(existing, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(existing, key, value)
            updated_count += 1
    session.commit()
    return {
        "scanned_count": scanned_count,
        "created_count": created_count,
        "updated_count": updated_count,
        "invalid_count": invalid_count,
    }


@community_router.get("/proposals/{proposal_id}/pull-request", response_model=PullRequestInstructionsResponse)
def pull_request_instructions(
    proposal_id: str, request: Request, session: SessionDep, user: UserDep
) -> PullRequestInstructionsResponse:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.author_id != str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403, detail="Only the proposal author or an administrator can open its PR guide"
        )
    repository = request.app.state.settings.github_repository
    return {
        "branch": f"task/{proposal.domain}-{proposal.field}-{proposal.task_slug}",
        "task_path": f"tasks/{proposal.domain}/{proposal.field}/{proposal.task_slug}",
        "proposal_url": proposal.discussion_url or "",
        "compare_url": f"https://github.com/{repository}/compare/main..." if repository else "",
    }


@community_router.get("/cloud-profiles", response_model=CloudProfileListResponse)
def list_cloud_profiles(session: SessionDep, _user: AdminDep) -> CloudProfileListResponse:
    items = session.scalars(select(CloudProfile).order_by(CloudProfile.name))
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "provider": item.provider,
                "allocation": item.allocation,
                "enabled": item.enabled,
            }
            for item in items
        ]
    }


@community_router.post(
    "/cloud-profiles", status_code=status.HTTP_201_CREATED, response_model=CloudProfileResponse
)
def create_cloud_profile(
    body: CloudProfileCreate, session: SessionDep, user: AdminDep
) -> CloudProfileResponse:
    if session.scalar(select(CloudProfile).where(CloudProfile.name == body.name)):
        raise HTTPException(status_code=409, detail="Cloud profile name already exists")
    item = CloudProfile(
        name=body.name,
        provider=body.provider,
        allocation=body.allocation,
        enabled=body.enabled,
        created_by=user.github_login or user.email,
    )
    session.add(item)
    session.commit()
    return {
        "id": item.id,
        "name": item.name,
        "provider": item.provider,
        "allocation": item.allocation,
        "enabled": item.enabled,
    }
