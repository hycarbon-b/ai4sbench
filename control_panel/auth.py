from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status

from .identity import User, current_active_user


@dataclass(frozen=True)
class Principal:
    subject: str


def get_principal(user: Annotated[User, Depends(current_active_user)]) -> Principal:
    """Authorize a control-plane operation for the two-role GitHub identity model."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return Principal(subject=f"github:{user.github_login or user.id}")
