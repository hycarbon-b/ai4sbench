"""Public reviewer-form HTTP adapter for the shared email-verification service."""

from __future__ import annotations

import re

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.mailing.verification import (
    EmailVerificationError,
    EmailVerificationPurpose,
    consume_email_verification,
    issue_email_verification,
    verify_email_verification,
)


class ReviewerEmailOtpRequest(BaseModel):
    email: str = Field(min_length=5, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("email must be a valid email address")
        return value


class ReviewerEmailOtpVerifyRequest(ReviewerEmailOtpRequest):
    challenge_id: str = Field(min_length=36, max_length=36)
    code: str = Field(pattern=r"^\d{6}$")


class ReviewerEmailOtpIssuedResponse(BaseModel):
    challenge_id: str
    expires_in_seconds: int


class ReviewerEmailOtpVerifiedResponse(BaseModel):
    verification_token: str
    expires_in_seconds: int


def _require_reviewer_otp(request: Request) -> None:
    if not request.app.state.settings.form_mail_require_otp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email verification is not enabled")


def _http_error(error: EmailVerificationError) -> HTTPException:
    codes = {"mail_unavailable": 503, "rate_limited": 429, "invalid_code": 400, "invalid_proof": 403}
    return HTTPException(status_code=codes[error.reason], detail=str(error))


async def issue_otp(
    request: Request, session: AsyncSession, body: ReviewerEmailOtpRequest, user_id: str
) -> ReviewerEmailOtpIssuedResponse:
    _require_reviewer_otp(request)
    try:
        result = await issue_email_verification(
            session,
            request.app.state.settings,
            email=body.email,
            purpose=EmailVerificationPurpose.REVIEWER_FORM,
            subject_id=user_id,
        )
    except EmailVerificationError as error:
        raise _http_error(error) from error
    return ReviewerEmailOtpIssuedResponse(
        challenge_id=result.challenge_id, expires_in_seconds=result.expires_in_seconds
    )


async def verify_otp(
    request: Request, session: AsyncSession, body: ReviewerEmailOtpVerifyRequest, user_id: str
) -> ReviewerEmailOtpVerifiedResponse:
    _require_reviewer_otp(request)
    try:
        result = await verify_email_verification(
            session,
            request.app.state.settings,
            email=body.email,
            purpose=EmailVerificationPurpose.REVIEWER_FORM,
            subject_id=user_id,
            challenge_id=body.challenge_id,
            code=body.code,
        )
    except EmailVerificationError as error:
        raise _http_error(error) from error
    return ReviewerEmailOtpVerifiedResponse(
        verification_token=result.verification_token,
        expires_in_seconds=result.expires_in_seconds,
    )


async def consume_reviewer_otp(session: AsyncSession, email: str, token: str, user_id: str) -> None:
    try:
        await consume_email_verification(
            session,
            email=email,
            purpose=EmailVerificationPurpose.REVIEWER_FORM,
            token=token,
            subject_id=user_id,
        )
    except EmailVerificationError as error:
        raise _http_error(error) from error
