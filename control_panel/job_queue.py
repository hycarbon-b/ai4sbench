from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models import DatabaseJob


def utcnow() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str,
    *,
    max_attempts: int = 5,
) -> DatabaseJob:
    existing = session.scalar(select(DatabaseJob).where(DatabaseJob.dedupe_key == dedupe_key))
    if existing:
        return existing
    job = DatabaseJob(kind=kind, payload=payload, dedupe_key=dedupe_key, max_attempts=max_attempts)
    session.add(job)
    session.flush()
    return job


def claim(session: Session, owner: str, lease_seconds: int) -> DatabaseJob | None:
    now = utcnow()
    candidate_id = session.scalar(
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
    return session.scalar(
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


def complete(session: Session, job_id: str, owner: str) -> bool:
    result = session.execute(
        update(DatabaseJob)
        .where(DatabaseJob.id == job_id, DatabaseJob.state == "leased", DatabaseJob.lease_owner == owner)
        .values(state="completed", lease_owner=None, lease_expires_at=None, updated_at=utcnow())
    )
    return result.rowcount == 1


def fail(session: Session, job: DatabaseJob, owner: str, error: str) -> bool:
    terminal = job.attempts >= job.max_attempts
    delay = min(300, 2 ** max(0, job.attempts - 1))
    result = session.execute(
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


def defer(session: Session, job: DatabaseJob, owner: str, delay_seconds: int = 5) -> bool:
    """Return a capacity-blocked job without consuming one of its retry attempts."""
    result = session.execute(
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
