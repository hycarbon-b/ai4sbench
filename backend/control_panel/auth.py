from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .identity import User, current_active_user


@dataclass(frozen=True)
class Principal:
    subject: str


def get_principal(user: Annotated[User, Depends(current_active_user)]) -> Principal:
    """Authorize a control-plane operation for the two-role GitHub identity model."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return Principal(subject=f"github:{user.github_login or user.id}")


async def require_ai_review_service(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))],
) -> None:
    key = request.app.state.settings.ai_review_service_key
    if not key or len(key.get_secret_value()) < 32:
        raise HTTPException(status_code=503, detail="AI review ingestion is not configured")
    if not credentials or not secrets.compare_digest(
        credentials.credentials.encode(), key.get_secret_value().encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid AI review service credential")
