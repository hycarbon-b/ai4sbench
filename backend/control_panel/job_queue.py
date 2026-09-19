from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DatabaseJob


def utcnow() -> datetime:
    return datetime.now(UTC)


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str,
    *,
    max_attempts: int = 5,
) -> DatabaseJob:
    existing = await session.scalar(select(DatabaseJob).where(DatabaseJob.dedupe_key == dedupe_key))
    if existing:
        return existing
    job = DatabaseJob(kind=kind, payload=payload, dedupe_key=dedupe_key, max_attempts=max_attempts)
    session.add(job)
    await session.flush()
    return job


async def claim(session: AsyncSession, owner: str, lease_seconds: int) -> DatabaseJob | None:
    now = utcnow()
    candidate_id = await session.scalar(
        select(DatabaseJob.id)
        .where(
            DatabaseJob.available_at <= now,
            DatabaseJob.attempts < DatabaseJob.max_attempts,
            or_(
                DatabaseJob.state == "pending",
                (DatabaseJob.state == "leased") & (DatabaseJob.lease_expires_at < now),
            ),
        )
        .order_by(DatabaseJob.created_at)
        .limit(1)
    )
    if candidate_id is None:
        return None
    return await session.scalar(
        update(DatabaseJob)
        .where(
            DatabaseJob.id == candidate_id,
            or_(
                DatabaseJob.state == "pending",
                (DatabaseJob.state == "leased") & (DatabaseJob.lease_expires_at < now),
            ),
        )
        .values(
            state="leased",
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            attempts=DatabaseJob.attempts + 1,
            updated_at=now,
        )
        .returning(DatabaseJob)
    )


async def complete(session: AsyncSession, job_id: str, owner: str) -> bool:
    result = await session.execute(
        update(DatabaseJob)
        .where(DatabaseJob.id == job_id, DatabaseJob.state == "leased", DatabaseJob.lease_owner == owner)
        .values(state="completed", lease_owner=None, lease_expires_at=None, updated_at=utcnow())
    )
    return result.rowcount == 1


async def fail(session: AsyncSession, job: DatabaseJob, owner: str, error: str) -> bool:
    terminal = job.attempts >= job.max_attempts
    delay = min(300, 2 ** max(0, job.attempts - 1))
    result = await session.execute(
        update(DatabaseJob)
        .where(DatabaseJob.id == job.id, DatabaseJob.state == "leased", DatabaseJob.lease_owner == owner)
        .values(
            state="failed" if terminal else "pending",
            available_at=utcnow() + timedelta(seconds=delay),
            lease_owner=None,
            lease_expires_at=None,
            last_error=error[:2000],
            updated_at=utcnow(),
        )
    )
    return result.rowcount == 1


async def defer(session: AsyncSession, job: DatabaseJob, owner: str, delay_seconds: int = 5) -> bool:
    """Return a capacity-blocked job without consuming one of its retry attempts."""
    result = await session.execute(
        update(DatabaseJob)
        .where(DatabaseJob.id == job.id, DatabaseJob.state == "leased", DatabaseJob.lease_owner == owner)
        .values(
            state="pending",
            attempts=DatabaseJob.attempts - 1,
            available_at=utcnow() + timedelta(seconds=delay_seconds),
            lease_owner=None,
            lease_expires_at=None,
            updated_at=utcnow(),
        )
    )
    return result.rowcount == 1
