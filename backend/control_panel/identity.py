from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
    SQLAlchemyUserDatabase,
)
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import Settings, get_settings


class AuthBase(DeclarativeBase):
    pass


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, AuthBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, AuthBase):
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    github_login: Mapped[str | None] = mapped_column(String(100), unique=True)
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship("OAuthAccount", lazy="joined")


def async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    raise ValueError("GitHub user authentication currently requires SQLite")


def create_auth_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(async_database_url(settings.database_url), future=True)


async def get_auth_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.auth_session_factory
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
        return await self.user_db.update(
            user,
            {"github_login": github_login, "role": self.role_for(login=github_login, email=user.email)},
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
    return backend, FastAPIUsers(get_user_manager, [backend])


auth_backend, fastapi_users = build_auth(get_settings())
current_optional_user = fastapi_users.current_user(optional=True, active=True)
current_active_user = fastapi_users.current_user(active=True)
