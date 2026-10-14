"""Administrator-issued, short-lived account invitations and API credentials."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_principal
from src.core.identity import EmailStatus, TemporaryCredential, User
from src.db.database import begin_immediate, get_session
from src.db.models import ReviewerApplication
from src.mailing.notifications import render_reviewer_invite

INVITE_LIFETIME = 7 * 24 * 60 * 60
API_TOKEN_LIFETIME = 24 * 60 * 60
SessionDep = Annotated[AsyncSession, Depends(get_session)]
admin_temporary_router = APIRouter(
    prefix="/api/v1/users", dependencies=[Depends(get_principal)], tags=["users"]
)
auth_temporary_router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class InviteRedeemRequest(BaseModel):
    token: str = Field(min_length=32, max_length=160)


class InviteBatchRequest(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class InviteMessage(BaseModel):
    user_id: str
    recipient: str
    subject: str
    html: str
    login_url: str
    expires_at: int


class InviteBatchResponse(BaseModel):
    items: list[InviteMessage]


class ApiTokenResponse(BaseModel):
    token: str
    expires_at: int


class CredentialStatus(BaseModel):
    id: str
    kind: Literal["invite", "api"]
    expires_at: int
    used_at: int | None
    revoked_at: int | None


class CredentialStatusList(BaseModel):
    items: list[CredentialStatus]


async def issue_credential(
    session: AsyncSession, user: User, kind: Literal["invite", "api"]
) -> tuple[str, int]:
    now = int(time.time())
    await session.execute(
        update(TemporaryCredential)
        .where(
            TemporaryCredential.user_id == user.id,
            TemporaryCredential.kind == kind,
            TemporaryCredential.used_at.is_(None),
            TemporaryCredential.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = ("tbinvite_" if kind == "invite" else "tbapi_") + secrets.token_urlsafe(32)
    expires_at = now + (INVITE_LIFETIME if kind == "invite" else API_TOKEN_LIFETIME)
    session.add(
        TemporaryCredential(
            id=str(uuid.uuid4()),
            user_id=user.id,
            kind=kind,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            email=user.email,
            created_at=now,
            expires_at=expires_at,
        )
    )
    return token, expires_at


async def invite_message(session: AsyncSession, request: Request, user: User) -> InviteMessage:
    application = await session.scalar(
        select(ReviewerApplication)
        .where(ReviewerApplication.user_id == user.id)
        .order_by(ReviewerApplication.created_at.desc())
        .limit(1)
    )
    kind = application.status.value if application else "account"
    if kind == "rejected":
        raise HTTPException(
            status_code=409, detail="A rejected reviewer application cannot receive this invite"
        )
    token, expires_at = await issue_credential(session, user, "invite")
    recipient = next(
        (account.account_email for account in user.oauth_accounts if account.oauth_name == "github"),
        user.email,
    )
    base_url = request.app.state.settings.worker_api_base_url.rstrip("/")
    if request.app.state.settings.environment != "production":
        base_url = str(request.base_url).rstrip("/")
    login_url = f"{base_url}/auth/invite#{token}"
    rendered = render_reviewer_invite(application.name if application else user.email, login_url, kind)
    return InviteMessage(
        user_id=str(user.id),
        recipient=recipient,
        subject=rendered.subject,
        html=rendered.html,
        login_url=login_url,
        expires_at=expires_at,
    )


@admin_temporary_router.post("/invites/batch", response_model=InviteBatchResponse)
async def issue_invites_batch(
    body: InviteBatchRequest, request: Request, session: SessionDep
) -> InviteBatchResponse:
    if len(body.user_ids) != len(set(body.user_ids)):
        raise HTTPException(status_code=422, detail="Duplicate user IDs")
    await begin_immediate(session)
    users = [await session.get(User, user_id) for user_id in body.user_ids]
    if any(user is None for user in users):
        raise HTTPException(status_code=404, detail="One or more users do not exist")
    items = [await invite_message(session, request, user) for user in users if user is not None]
    await session.commit()
    return InviteBatchResponse(items=items)


@admin_temporary_router.post("/{user_id}/invite", response_model=InviteMessage)
async def issue_invite(user_id: uuid.UUID, request: Request, session: SessionDep) -> InviteMessage:
    await begin_immediate(session)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    message = await invite_message(session, request, user)
    await session.commit()
    return message


@admin_temporary_router.post("/{user_id}/api-tokens", response_model=ApiTokenResponse)
async def issue_api_token(user_id: uuid.UUID, session: SessionDep) -> ApiTokenResponse:
    await begin_immediate(session)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_verified:
        raise HTTPException(status_code=409, detail="The user must redeem their invitation first")
    token, expires_at = await issue_credential(session, user, "api")
    await session.commit()
    return ApiTokenResponse(token=token, expires_at=expires_at)


@admin_temporary_router.delete("/{user_id}/api-tokens", status_code=204)
async def revoke_api_tokens(user_id: uuid.UUID, session: SessionDep) -> Response:
    await begin_immediate(session)
    if await session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    await session.execute(
        update(TemporaryCredential)
        .where(
            TemporaryCredential.user_id == user_id,
            TemporaryCredential.kind == "api",
            TemporaryCredential.revoked_at.is_(None),
        )
        .values(revoked_at=int(time.time()))
    )
    await session.commit()
    return Response(status_code=204)


@admin_temporary_router.get("/{user_id}/temporary-credentials", response_model=CredentialStatusList)
async def list_credentials(user_id: uuid.UUID, session: SessionDep) -> CredentialStatusList:
    if await session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    items = await session.scalars(
        select(TemporaryCredential)
        .where(TemporaryCredential.user_id == user_id)
        .order_by(TemporaryCredential.created_at.desc())
    )
    return CredentialStatusList(
        items=[CredentialStatus.model_validate(item, from_attributes=True) for item in items]
    )


@auth_temporary_router.post("/invites/redeem", status_code=204)
async def redeem_invite(body: InviteRedeemRequest, request: Request, session: SessionDep) -> Response:
    if not body.token.startswith("tbinvite_"):
        raise HTTPException(status_code=401, detail="Invalid or expired invitation")
    await begin_immediate(session)
    credential = await session.scalar(
        select(TemporaryCredential).where(
            TemporaryCredential.kind == "invite",
            TemporaryCredential.token_hash == hashlib.sha256(body.token.encode()).hexdigest(),
        )
    )
    now = int(time.time())
    if credential is None or credential.expires_at <= now or credential.used_at or credential.revoked_at:
        raise HTTPException(status_code=401, detail="Invalid or expired invitation")
    user = await session.get(User, credential.user_id)
    if user is None or not user.is_active or user.email.lower() != credential.email.lower():
        raise HTTPException(status_code=401, detail="Invalid or expired invitation")
    credential.used_at = now
    user.is_verified = True
    if user.email_status == EmailStatus.UNAUTHORIZED.value:
        user.email_status = EmailStatus.AUTHORIZED.value
    await session.commit()
    backend = request.app.state.auth_backend
    return await backend.login(backend.get_strategy(), user)
