from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TaskRevisionCreate(BaseModel):
    repo_url: str = Field(max_length=500)
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    task_path: str = Field(max_length=500)
    resource_requirements: dict[str, int] = Field(default_factory=dict)
    proposal_id: str | None = None
    pull_request_url: str | None = Field(default=None, max_length=500)
    release: str | None = Field(default=None, max_length=80)

    @field_validator("task_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("task_path must be a safe relative path")
        return value

    @field_validator("resource_requirements")
    @classmethod
    def valid_resource_requirements(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {"cpus", "memory_mb", "storage_mb", "gpus"}
        if set(value) - allowed or any(amount < 0 for amount in value.values()):
            raise ValueError("resource_requirements contains an invalid resource")
        return value


class TaskRevisionSync(BaseModel):
    repo_url: str
    ref: str
    task_path: str


class TaskRepositorySync(BaseModel):
    """Import every Harbor task from the current tip of one repository branch."""

    repo_url: str
    ref: str = Field(min_length=1, max_length=200)

    @field_validator("ref")
    @classmethod
    def safe_git_ref(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value):
            raise ValueError("ref must be a safe branch or tag name")
        return value


class PlanConfig(BaseModel):
    agent: str = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    n_concurrent: int = Field(default=1, ge=1, le=16)
    n_attempts: int = Field(default=1, ge=1, le=10)
    environment: Literal["docker", "modal", "daytona", "e2b", "gke"] = "docker"
    instance_type: str | None = None
    root_volume_gb: int | None = Field(default=None, ge=20, le=500)

    @model_validator(mode="after")
    def codex_requires_model(self) -> PlanConfig:
        if self.agent == "codex" and not self.model:
            raise ValueError("model is required for the codex agent")
        return self


class PlanCreate(BaseModel):
    task_revision_id: str
    config: PlanConfig


class PlanApprove(BaseModel):
    lock_version: int = Field(ge=0)


class RunCreate(BaseModel):
    plan_id: str
    timeout_minutes: int = Field(default=180, ge=1, le=720)


class ManualRunCreate(BaseModel):
    """The operator-facing one-shot benchmark launch request."""

    task_revision_id: str
    config: PlanConfig
    timeout_minutes: int = Field(default=180, ge=1, le=720)


class ManualBatchRunCreate(BaseModel):
    """Queue exactly one run for each selected immutable task revision."""

    task_revision_ids: list[str] = Field(min_length=1, max_length=200)
    config: PlanConfig
    timeout_minutes: int = Field(default=180, ge=1, le=720)

    @field_validator("task_revision_ids")
    @classmethod
    def unique_task_revisions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("task_revision_ids must be unique")
        return value


class WorkerClaim(BaseModel):
    token: str


class WorkerEventCreate(BaseModel):
    session_token: str
    event_type: str = Field(default="worker_log", max_length=80)
    message: str = Field(default="", max_length=4000)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerComplete(BaseModel):
    session_token: str
    state: Literal["succeeded", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)


class CloudProfileCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,118}$")
    provider: Literal["aws"] = "aws"
    allocation: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class LiveHealthResponse(BaseModel):
    status: Literal["ok"]
    time: datetime


class ReadyHealthResponse(BaseModel):
    status: Literal["ready"]


class SettingsResponse(BaseModel):
    environment: Literal["development", "test", "production"]
    execution_mode: Literal["fake", "ec2"]
    aws_region: str
    ec2_ami_id: str
    ec2_instance_type: str
    ec2_allowed_instance_types: list[str]
    ec2_instance_resources: dict[str, dict[str, int]]
    ec2_root_volume_gb: int
    ec2_subnet_id: str | None
    ec2_associate_public_ip: bool
    quick_tunnel_enabled: bool
    max_active_runs: int
    github_login_enabled: bool
    github_repository: str


class TaskRevisionResponse(BaseModel):
    id: str
    repo_url: str
    commit_sha: str
    task_path: str
    resource_requirements: dict[str, int]
    proposal_id: str | None
    pull_request_url: str | None
    release: str | None
    created_at: datetime


class TaskRevisionListResponse(BaseModel):
    items: list[TaskRevisionResponse]


class TaskRepositorySyncResponse(BaseModel):
    commit_sha: str
    task_count: int
    created_count: int
    updated_count: int
    items: list[TaskRevisionResponse]


class PlanResponse(BaseModel):
    id: str
    task_revision_id: str
    state: str
    config: dict[str, Any]
    lock_version: int
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    repo_url: str
    commit_sha: str
    task_path: str


class PlanListResponse(BaseModel):
    items: list[PlanResponse]


class RunResponse(BaseModel):
    id: str
    plan_id: str
    state: str
    config: dict[str, Any]
    deadline_at: datetime
    instance_id: str | None
    instance_state: str | None
    result: dict[str, Any] | None
    version: int
    created_at: datetime
    updated_at: datetime
    repo_url: str
    commit_sha: str
    task_path: str


class CreatedRunResponse(RunResponse):
    created: bool


class RunListResponse(BaseModel):
    items: list[RunResponse]


class ManualBatchRunResponse(BaseModel):
    created_count: int
    items: list[RunResponse]


class RunEventResponse(BaseModel):
    id: str
    run_id: str
    event_type: str
    message: str
    payload: dict[str, Any]
    created_at: datetime


class RunEventListResponse(BaseModel):
    items: list[RunEventResponse]


class DashboardResponse(BaseModel):
    runs: list[RunResponse]
    plans: list[PlanResponse]
    task_revisions: list[TaskRevisionResponse]
    counts: dict[str, int]


class DatabaseSnapshotResponse(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime


class DatabaseSnapshotListResponse(BaseModel):
    items: list[DatabaseSnapshotResponse]


class DatabaseJobResponse(BaseModel):
    id: str
    kind: str
    state: str
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error: str | None


class DatabaseJobListResponse(BaseModel):
    items: list[DatabaseJobResponse]


class WorkerRunReference(BaseModel):
    id: str
    config: dict[str, Any]


class WorkerClaimResponse(BaseModel):
    run: WorkerRunReference
    task_revision: TaskRevisionResponse
    session_token: str


class CloudProfileResponse(BaseModel):
    id: str
    name: str
    provider: Literal["aws"]
    allocation: dict[str, Any]
    enabled: bool


class CloudProfileListResponse(BaseModel):
    items: list[CloudProfileResponse]
