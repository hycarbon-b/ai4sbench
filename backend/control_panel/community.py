from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session
from .identity import User, current_active_user
from .models import CloudProfile, Proposal
from .schemas import CloudProfileCreate, ProposalAuthorInformation, ProposalDocument

community_router = APIRouter(prefix="/api/v1", tags=["community"])
SessionDep = Annotated[Session, Depends(get_session)]
UserDep = Annotated[User, Depends(current_active_user)]


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


@community_router.get("/auth/me")
async def me(user: UserDep) -> dict[str, str]:
    return user_dict(user)


@community_router.get("/auth/config")
def auth_config(request: Request) -> dict[str, bool]:
    settings = request.app.state.settings
    return {
        "github_login_enabled": bool(settings.github_oauth_client_id and settings.github_oauth_client_secret)
    }


@community_router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    """Clear only the local control-panel session; GitHub authorization is left intact."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie("ai4sbench_session", path="/")
    return response


@community_router.get("/proposals")
def list_proposals(session: SessionDep) -> dict[str, list[dict[str, object]]]:
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


def github_access_token(user: User) -> str:
    github_account = next(
        (account for account in user.oauth_accounts if account.oauth_name == "github"), None
    )
    if github_account is None:
        raise HTTPException(status_code=401, detail="GitHub account is not linked")
    return github_account.access_token


def parse_github_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def markdown_section(body: str, heading: str, fallback: str = "Not provided") -> str:
    match = re.search(
        rf"(?ims)^##\s*{re.escape(heading)}\s*$\n+(.+?)(?=^##\s|\Z)", body or ""
    )
    return match.group(1).strip() if match else fallback


def markdown_line(body: str, label: str) -> str | None:
    match = re.search(rf"(?im)^{re.escape(label)}\s*:\s*(.+?)\s*$", body or "")
    return match.group(1).strip() if match else None


def conflict_of_interest(body: str) -> str:
    match = re.search(
        r"(?ims)^Commercial Affiliation & Conflicts of Interest:\s*\n+(.+?)(?=^---\s*$|^##\s|\Z)",
        body or "",
    )
    return match.group(1).strip() if match else "None"


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def document_from_discussion(node: dict[str, Any]) -> ProposalDocument:
    """Convert the public Terminal-Bench-Science Markdown format into our canonical schema."""

    body = str(node.get("body") or "")
    raw_title = str(node.get("title") or "Untitled proposal")
    title_match = PROPOSAL_TITLE_RE.match(raw_title)
    title = title_match.group(1).strip() if title_match else raw_title
    domain_path = markdown_section(body, "Scientific Domain", "Unknown > Unknown > Unspecified")
    parts = [part.strip() for part in domain_path.split(">")]
    domain = slugify(parts[0]) if parts and parts[0] else "unknown"
    field = slugify(parts[1]) if len(parts) > 1 and parts[1] else "unknown"
    subfield = parts[2] if len(parts) > 2 and parts[2] else (parts[1] if len(parts) > 1 else "Unspecified")
    task_slug_match = re.search(r"(?im)^Proposed task slug:\s*`?([^`\s]+)`?\s*$", body)
    task_slug = task_slug_match.group(1) if task_slug_match else slugify(title)
    author_login = markdown_line(body, "GitHub") or ((node.get("author") or {}).get("login"))
    if author_login and "github.com/" in author_login:
        author_login = author_login.rstrip("/").split("/")[-1]
    data = {
        "title": title,
        "domain": domain or "unknown",
        "field": field or "unknown",
        "subfield": subfield,
        "task_slug": task_slug or "untitled-proposal",
        "scientific_problem": markdown_section(
            body, "Scientific Problem", markdown_section(body, "Task Proposal")
        ),
        "workflow_details": markdown_section(body, "Workflow Details"),
        "dependencies_and_system_requirements": markdown_section(body, "Dependencies & System Requirements"),
        "dataset": markdown_section(body, "Dataset"),
        "evaluation_strategy": markdown_section(body, "Evaluation Strategy"),
        "complexity": markdown_section(body, "Complexity"),
        "references_and_resources": markdown_section(
            body, "References & Resources", markdown_section(body, "Evidence & Provenance")
        ),
        "additional_information": markdown_section(body, "Additional Information", "None provided"),
        "author_information": ProposalAuthorInformation.model_construct(
            author=markdown_line(body, "Author"),
            email=markdown_line(body, "Email"),
            role=markdown_line(body, "Role"),
            professional_profile=markdown_line(body, "Professional Profile"),
            academic_profile=markdown_line(body, "Academic Profile"),
            github=author_login,
            discord=markdown_line(body, "Discord"),
            recommended_reviewers=markdown_line(body, "Recommended Reviewers"),
            relevant_experience=markdown_line(body, "Relevant Experience"),
            conflicts_of_interest=conflict_of_interest(body),
        ),
    }
    # Public upstream records can predate individual fields.  The canonical JSON
    # remains useful in that case, while browser submissions still get full API validation.
    return ProposalDocument.model_construct(**data)


def discussion_status(labels: list[str]) -> str:
    if any(label.startswith("proposal-approved") for label in labels):
        return "approved"
    if any(label.startswith("proposal-declined") for label in labels):
        return "rejected"
    return "pending"


async def create_github_discussion(
    request: Request, user: User, document: ProposalDocument
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
                "title": document.title,
                "body": document.render_discussion(),
            }
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.github.com/graphql",
            json=payload,
            headers={
                "Authorization": f"Bearer {github_access_token(user)}",
                "Accept": "application/vnd.github+json",
            },
        )
    if response.status_code >= 400 or response.json().get("errors"):
        raise HTTPException(status_code=502, detail="GitHub could not create the proposal discussion")
    discussion = response.json()["data"]["createDiscussion"]["discussion"]
    return {
        "id": str(discussion["id"]),
        "number": int(discussion["number"]),
        "url": str(discussion["url"]),
    }


@community_router.post("/proposals", status_code=status.HTTP_201_CREATED)
async def create_proposal(
    body: ProposalDocument, request: Request, session: SessionDep, user: UserDep
) -> dict[str, object]:
    document = body.with_author(github_login=user.github_login, email=user.email)
    discussion = await create_github_discussion(request, user, document)
    item = Proposal(
        author_id=str(user.id),
        author_login=user.github_login,
        title=document.title,
        abstract=document.scientific_problem,
        domain=document.domain,
        field=document.field,
        task_slug=document.task_slug,
        evidence=document.references_and_resources,
        document=document.model_dump(mode="json"),
        status="pending",
        discussion_url=str(discussion["url"]),
        discussion_node_id=str(discussion["id"]),
        discussion_number=int(discussion["number"]),
    )
    session.add(item)
    session.commit()
    return {"id": item.id, "status": item.status, "discussion_url": item.discussion_url}


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


@community_router.post("/proposals/sync-discussions")
async def sync_proposal_discussions(
    request: Request, session: SessionDep, user: AdminDep
) -> dict[str, int]:
    """Full, non-destructive GitHub reconciliation for the configured Proposal category."""

    nodes = await fetch_all_discussions(request.app.state.settings, github_access_token(user))
    created_count = 0
    updated_count = 0
    scanned_count = 0
    for node in nodes:
        if (node.get("category") or {}).get("name") != TASK_PROPOSALS_CATEGORY:
            continue
        scanned_count += 1
        document = document_from_discussion(node)
        labels = [str(label["name"]) for label in (node.get("labels") or {}).get("nodes", [])]
        values = {
            "author_login": document.author_information.github,
            "title": document.title,
            "abstract": document.scientific_problem,
            "domain": document.domain,
            "field": document.field,
            "task_slug": document.task_slug,
            "evidence": document.references_and_resources,
            "document": document.model_dump(mode="json"),
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
    return {"scanned_count": scanned_count, "created_count": created_count, "updated_count": updated_count}


@community_router.get("/proposals/{proposal_id}/pull-request")
def pull_request_instructions(
    proposal_id: str, request: Request, session: SessionDep, user: UserDep
) -> dict[str, str]:
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


@community_router.get("/cloud-profiles")
def list_cloud_profiles(session: SessionDep, _user: AdminDep) -> dict[str, list[dict[str, object]]]:
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


@community_router.post("/cloud-profiles", status_code=status.HTTP_201_CREATED)
def create_cloud_profile(body: CloudProfileCreate, session: SessionDep, user: AdminDep) -> dict[str, object]:
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
