from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import Settings


class Base(DeclarativeBase):
    pass


def async_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername != "sqlite":
        raise ValueError("This deployment is intentionally SQLite-only")
    return url.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False)


def create_database_engine(settings: Settings) -> AsyncEngine:
    url = make_url(settings.database_url)
    if url.drivername != "sqlite":
        raise ValueError("This deployment is intentionally SQLite-only")
    if url.database and url.database != ":memory:":
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(
        async_database_url(settings.database_url),
        connect_args={"timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
