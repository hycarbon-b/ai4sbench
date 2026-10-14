from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, CookieTransport, JWTStrategy
from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
    SQLAlchemyUserDatabase,
)
from fastapi_users.exceptions import UserNotExists
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.config import Settings, get_settings
from src.db.database import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    pass


class EmailStatus(StrEnum):
    UNAUTHORIZED = "unauthorized"
    FROM_GITHUB_OAUTH = "from_github_oauth"
    AUTHORIZED = "authorized"


class User(SQLAlchemyBaseUserTableUUID, Base):
    __table_args__ = (
        CheckConstraint(
            "email_status IN ('unauthorized', 'from_github_oauth', 'authorized')",
            name="ck_user_email_status",
        ),
    )

    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    github_login: Mapped[str | None] = mapped_column(String(100), unique=True)
    email_status: Mapped[str] = mapped_column(
        String(32), default=EmailStatus.UNAUTHORIZED.value, nullable=False
    )
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship("OAuthAccount", lazy="joined")


class TemporaryCredential(Base):
    __tablename__ = "temporary_credentials"
    __table_args__ = (
        CheckConstraint("kind IN ('invite', 'api')", name="ck_temporary_credentials_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    used_at: Mapped[int | None] = mapped_column(Integer)
    revoked_at: Mapped[int | None] = mapped_column(Integer)


class TemporaryBearerStrategy:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read_token(self, token: str | None, user_manager: BaseUserManager[User, UUID]) -> User | None:
        if not token or not token.startswith("tbapi_"):
            return None
        credential = await self.session.scalar(
            select(TemporaryCredential).where(
                TemporaryCredential.kind == "api",
                TemporaryCredential.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                TemporaryCredential.expires_at > int(time.time()),
                TemporaryCredential.revoked_at.is_(None),
            )
        )
        if credential is None:
            return None
        try:
            user = await user_manager.get(credential.user_id)
        except UserNotExists:
            return None
        return user if user.email.lower() == credential.email.lower() else None

    async def write_token(self, user: User) -> str:
        raise NotImplementedError("API tokens are issued by the administrator endpoint")

    async def destroy_token(self, token: str, user: User) -> None:
        raise NotImplementedError("API tokens are revoked by the administrator endpoint")


async def get_auth_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


async def get_user_db(
    session: Annotated[AsyncSession, Depends(get_auth_session)],
) -> AsyncGenerator[SQLAlchemyUserDatabase[User, UUID], None]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


class UserManager(UUIDIDMixin, BaseUserManager[User, UUID]):
    def __init__(self, user_db: SQLAlchemyUserDatabase[User, UUID], settings: Settings) -> None:
        super().__init__(user_db)
        self.settings = settings
        self.reset_password_token_secret = settings.auth_jwt_secret.get_secret_value()
        self.verification_token_secret = settings.auth_jwt_secret.get_secret_value()

    def role_for(self, *, login: str | None, email: str) -> str:
        admin_logins = {item.lower() for item in self.settings.admin_github_logins}
        admin_emails = {item.lower() for item in self.settings.admin_github_emails}
        contributor_logins = {item.lower() for item in self.settings.contributor_github_logins}
        contributor_emails = {item.lower() for item in self.settings.contributor_github_emails}
        is_admin = (login and login.lower() in admin_logins) or email.lower() in admin_emails
        is_contributor = (login and login.lower() in contributor_logins) or (
            email.lower() in contributor_emails
        )
        if is_admin:
            return "admin"
        if is_contributor:
            return "contributor"
        return "member"

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        role = self.role_for(login=None, email=user.email)
        if user.role != role:
            await self.user_db.update(user, {"role": role})

    async def oauth_callback(self, *args: object, **kwargs: object) -> User:
        user = await super().oauth_callback(*args, **kwargs)
        token = str(args[1]) if len(args) > 1 else str(kwargs["access_token"])
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        github_login = str(response.json()["login"])
        oauth_email_matches = any(
            account.oauth_name == "github" and account.account_email.lower() == user.email.lower()
            for account in user.oauth_accounts
        )
        email_status = user.email_status
        if oauth_email_matches and email_status == EmailStatus.UNAUTHORIZED.value:
            email_status = EmailStatus.FROM_GITHUB_OAUTH.value
        return await self.user_db.update(
            user,
            {
                "github_login": github_login,
                "role": self.role_for(login=github_login, email=user.email),
                "email_status": email_status,
                "is_verified": user.is_verified or oauth_email_matches,
            },
        )

    async def oauth_associate_callback(self, *args: object, **kwargs: object) -> User:
        current_user = args[0] if args else kwargs["user"]
        oauth_name = str(args[1]) if len(args) > 1 else str(kwargs["oauth_name"])
        account_id = str(args[3]) if len(args) > 3 else str(kwargs["account_id"])
        if any(
            account.oauth_name == oauth_name and account.account_id != account_id
            for account in current_user.oauth_accounts
        ):
            raise HTTPException(status_code=409, detail="This account already has a GitHub identity")
        try:
            owner = await self.get_by_oauth_account(oauth_name, account_id)
        except UserNotExists:
            owner = None
        if owner is not None and owner.id != current_user.id:
            raise HTTPException(status_code=409, detail="GitHub identity is linked to another account")
        if owner is not None:
            return owner
        token = str(args[2]) if len(args) > 2 else str(kwargs["access_token"])
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        login = str(response.json()["login"])
        login_owner = await self.user_db.session.scalar(
            select(User.id).where(func.lower(User.github_login) == login.lower())
        )
        if login_owner is not None and login_owner != current_user.id:
            raise HTTPException(status_code=409, detail="GitHub login is linked to another account")
        user = await super().oauth_associate_callback(*args, **kwargs)
        account_email = str(args[4]) if len(args) > 4 else str(kwargs["account_email"])
        return await self.user_db.update(
            user,
            {
                "github_login": login,
                "role": self.role_for(login=login, email=user.email),
                "email_status": (
                    EmailStatus.FROM_GITHUB_OAUTH.value
                    if account_email.lower() == user.email.lower()
                    and user.email_status == EmailStatus.UNAUTHORIZED.value
                    else user.email_status
                ),
                "is_verified": user.is_verified or account_email.lower() == user.email.lower(),
            },
        )


async def get_user_manager(
    request: Request,
    user_db: Annotated[SQLAlchemyUserDatabase[User, UUID], Depends(get_user_db)],
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db, request.app.state.settings)


def build_auth(settings: Settings) -> tuple[AuthenticationBackend[User, UUID], FastAPIUsers[User, UUID]]:
    transport = CookieTransport(
        cookie_name="ai4sbench_session",
        cookie_secure=settings.environment == "production" or bool(settings.cors_origins),
        cookie_httponly=True,
        # The public site can live on a different origin from the control plane.
        # Cross-origin authenticated API requests require a secure SameSite=None cookie.
        cookie_samesite="none" if settings.cors_origins else "lax",
    )

    def get_jwt_strategy() -> JWTStrategy:
        return JWTStrategy(
            secret=settings.auth_jwt_secret.get_secret_value(), lifetime_seconds=60 * 60 * 24 * 14
        )

    backend = AuthenticationBackend(name="github-cookie", transport=transport, get_strategy=get_jwt_strategy)

    async def get_temporary_strategy(
        session: Annotated[AsyncSession, Depends(get_auth_session)],
    ) -> TemporaryBearerStrategy:
        return TemporaryBearerStrategy(session)

    bearer_backend = AuthenticationBackend(
        name="temporary-bearer",
        transport=BearerTransport(tokenUrl="/api/v1/users/{user_id}/api-tokens"),
        get_strategy=get_temporary_strategy,
    )
    return backend, FastAPIUsers(get_user_manager, [backend, bearer_backend])


auth_backend, fastapi_users = build_auth(get_settings())
current_optional_user = fastapi_users.current_user(optional=True, active=True)
current_active_user = fastapi_users.current_user(active=True)
