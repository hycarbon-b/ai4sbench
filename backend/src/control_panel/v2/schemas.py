from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HarborRunCreate(BaseModel):
    task_revision_id: str
    agent: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    n_attempts: int = Field(default=1, ge=1, le=20)
    n_concurrent: int = Field(default=1, ge=1, le=16)
    instance_type: str | None = None


class HarborRunResponse(BaseModel):
    id: str
    task_revision_id: str
    state: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    config: dict[str, Any]
    job_path: str | None
    exit_code: int | None
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class HarborRunListResponse(BaseModel):
    items: list[HarborRunResponse]


class HarborRunLogResponse(BaseModel):
    text: str
    truncated: bool
