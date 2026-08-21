from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session
from .identity import User, current_active_user
from .models import CloudProfile, Proposal
from .schemas import CloudProfileCreate, ProposalCreate

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
                "author_login": item.author_login,
            }
            for item in items
        ]
    }


def discussion_body(body: ProposalCreate, user: User) -> str:
    return "\n".join(
        (
            "## Task proposal",
            "",
            body.abstract,
            "",
            "## Scientific domain",
            f"- Domain: `{body.domain}`",
            f"- Field: `{body.field}`",
            f"- Proposed slug: `{body.task_slug}`",
            "",
            "## Evidence and provenance",
            body.evidence,
            "",
            "## Contributor",
            f"GitHub: @{user.github_login or user.email}",
            "",
            "_Created from the ai4sbench contribution form. A maintainer must approve this proposal "
            "before a task PR is merged._",
        )
    )


async def create_github_discussion(request: Request, user: User, body: ProposalCreate) -> str:
    settings = request.app.state.settings
    if not settings.github_repository_node_id or not settings.github_discussion_category_id:
        raise HTTPException(status_code=503, detail="GitHub Discussion destination is not configured")
    github_account = next(
        (account for account in user.oauth_accounts if account.oauth_name == "github"), None
    )
    if github_account is None:
        raise HTTPException(status_code=401, detail="GitHub account is not linked")
    payload = {
        "query": (
            "mutation($input: CreateDiscussionInput!) { "
            "createDiscussion(input: $input) { discussion { url } } }"
        ),
        "variables": {
            "input": {
                "repositoryId": settings.github_repository_node_id,
                "categoryId": settings.github_discussion_category_id,
                "title": body.title,
                "body": discussion_body(body, user),
            }
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.github.com/graphql",
            json=payload,
            headers={
                "Authorization": f"Bearer {github_account.access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if response.status_code >= 400 or response.json().get("errors"):
        raise HTTPException(status_code=502, detail="GitHub could not create the proposal discussion")
    return str(response.json()["data"]["createDiscussion"]["discussion"]["url"])


@community_router.post("/proposals", status_code=status.HTTP_201_CREATED)
async def create_proposal(
    body: ProposalCreate, request: Request, session: SessionDep, user: UserDep
) -> dict[str, object]:
    discussion_url = await create_github_discussion(request, user, body)
    item = Proposal(
        author_id=str(user.id),
        author_login=user.github_login,
        title=body.title,
        abstract=body.abstract,
        domain=body.domain,
        field=body.field,
        task_slug=body.task_slug,
        evidence=body.evidence,
        status="discussion_open",
        discussion_url=discussion_url,
    )
    session.add(item)
    session.commit()
    return {"id": item.id, "status": item.status, "discussion_url": item.discussion_url}


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
