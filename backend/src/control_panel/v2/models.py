from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class HarborRunV2(Base):
    __tablename__ = "harbor_v2_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_revision_id: Mapped[str] = mapped_column(
        ForeignKey("task_revisions.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    owner: Mapped[str | None] = mapped_column(String(160))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_path: Mapped[str | None] = mapped_column(String(1000))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
