"""First GitHub connection email choice, with proof for non-GitHub addresses."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.identity import OAuthAccount, User, current_active_user, role_for_identity
from src.db.database import begin_immediate, get_session
from src.mailing.verification import (
    EmailVerificationError,
    EmailVerificationPurpose,
    consume_email_verification,
    issue_email_verification,
    verify_email_verification,
)

router = APIRouter(prefix="/api/v1/auth/email", tags=["authentication"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(current_active_user)]


class EmailAddress(BaseModel):
    email: str = Field(min_length=5, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("email must be a valid address")
        return value


class VerifyCode(EmailAddress):
    challenge_id: str = Field(min_length=36, max_length=36)
    code: str = Field(pattern=r"^\d{6}$")


class EmailChoice(BaseModel):
    choice: Literal["keep", "change"]
    email: str | None = Field(default=None, min_length=5, max_length=200)
    verification_token: str | None = Field(default=None, min_length=32, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return EmailAddress(email=value).email if value is not None else None


class ChallengeResponse(BaseModel):
    challenge_id: str
    expires_in_seconds: int


class ProofResponse(BaseModel):
    verification_token: str
    expires_in_seconds: int


def verification_error(error: EmailVerificationError) -> HTTPException:
    codes = {"mail_unavailable": 503, "rate_limited": 429, "invalid_code": 400, "invalid_proof": 403}
    return HTTPException(status_code=codes[error.reason], detail=str(error))


async def pending_account(session: AsyncSession, user_id: object) -> tuple[User, str]:
    account = await session.get(User, user_id)
    if account is None or not account.github_email_review_pending:
        raise HTTPException(status_code=409, detail="No GitHub email choice is pending")
    github_email = await session.scalar(
        select(OAuthAccount.account_email).where(
            OAuthAccount.user_id == account.id, OAuthAccount.oauth_name == "github"
        )
    )
    if not github_email:
        raise HTTPException(status_code=409, detail="Connect GitHub before confirming your email")
    return account, github_email.strip().lower()


@router.post("/code", status_code=status.HTTP_202_ACCEPTED, response_model=ChallengeResponse)
async def request_code(
    request: Request, body: EmailAddress, session: SessionDep, user: UserDep
) -> ChallengeResponse:
    _, github_email = await pending_account(session, user.id)
    if body.email == github_email:
        raise HTTPException(status_code=409, detail="Your GitHub email does not require a code")
    owner = await session.scalar(
        select(User.id).where(func.lower(User.email) == body.email, User.id != user.id)
    )
    if owner is not None:
        raise HTTPException(status_code=409, detail="This email belongs to another account")
    # The reusable verification service acquires its own SQLite write lock.
    await session.rollback()
    try:
        result = await issue_email_verification(
            session, request.app.state.settings, email=body.email,
            purpose=EmailVerificationPurpose.CHANGE_EMAIL, subject_id=str(user.id),
        )
    except EmailVerificationError as error:
        raise verification_error(error) from error
    return ChallengeResponse(
        challenge_id=result.challenge_id, expires_in_seconds=result.expires_in_seconds
    )


@router.post("/code/verify", response_model=ProofResponse)
async def verify_code(
    request: Request, body: VerifyCode, session: SessionDep, user: UserDep
) -> ProofResponse:
    await pending_account(session, user.id)
    await session.rollback()
    try:
        result = await verify_email_verification(
            session, request.app.state.settings, email=body.email,
            purpose=EmailVerificationPurpose.CHANGE_EMAIL, subject_id=str(user.id),
            challenge_id=body.challenge_id, code=body.code,
        )
    except EmailVerificationError as error:
        raise verification_error(error) from error
    return ProofResponse(
        verification_token=result.verification_token, expires_in_seconds=result.expires_in_seconds
    )


@router.post("/choice", status_code=status.HTTP_204_NO_CONTENT)
async def choose_email(body: EmailChoice, request: Request, session: SessionDep, user: UserDep) -> None:
    await begin_immediate(session)
    account, github_email = await pending_account(session, user.id)
    if body.choice == "keep":
        account.github_email_review_pending = False
        account.email_preference_set = True
        await session.commit()
        return
    if not body.email:
        raise HTTPException(status_code=422, detail="Choose an email address")
    owner = await session.scalar(
        select(User.id).where(func.lower(User.email) == body.email, User.id != account.id)
    )
    if owner is not None:
        raise HTTPException(status_code=409, detail="This email belongs to another account")
    if body.email != github_email and body.email != account.email.lower():
        if not body.verification_token:
            raise HTTPException(status_code=403, detail="Verify this email address first")
        try:
            await consume_email_verification(
                session, email=body.email, purpose=EmailVerificationPurpose.CHANGE_EMAIL,
                token=body.verification_token, subject_id=str(user.id),
            )
        except EmailVerificationError as error:
            raise verification_error(error) from error
    account.email = body.email
    account.role = role_for_identity(request.app.state.settings, login=account.github_login, email=body.email)
    if body.email == github_email:
        account.email_status = "from_github_oauth"
    elif body.email != user.email.lower():
        account.email_status = "authorized"
    account.is_verified = True
    account.github_email_review_pending = False
    account.email_preference_set = True
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="This email belongs to another account") from error
