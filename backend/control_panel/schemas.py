from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskRevisionCreate(BaseModel):
    repo_url: str = Field(max_length=500)
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    task_path: str = Field(max_length=500)
    resource_requirements: dict[str, int] = Field(default_factory=dict)

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


class ProposalSubmission(BaseModel):
    """The sole task-proposal request contract, matching the Website wizard."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=12, max_length=160)
    domain: str = Field(min_length=2, max_length=160)
    field_name: str = Field(min_length=2, max_length=160)
    problem: str = Field(min_length=80, max_length=12000)
    solvability: str = Field(min_length=20, max_length=12000)
    references: str = Field(min_length=40, max_length=12000)
    software: str = Field(min_length=10, max_length=5000)
    dataset: str = Field(min_length=10, max_length=12000)
    compute: str = Field(min_length=5, max_length=900)
    workflow: str = Field(min_length=20, max_length=12000)
    evaluation: str = Field(min_length=20, max_length=11000)
    leakage: str = Field(min_length=10, max_length=900)
    name: str = Field(min_length=2, max_length=200)
    affiliation: str = Field(default="", max_length=300)
    github: str = Field(min_length=1, max_length=100)

    @field_validator(
        "title",
        "domain",
        "field_name",
        "problem",
        "solvability",
        "references",
        "software",
        "dataset",
        "compute",
        "workflow",
        "evaluation",
        "leakage",
        "name",
        "affiliation",
        "github",
        mode="before",
    )
    @classmethod
    def strip_content(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("github")
    @classmethod
    def valid_github_login(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}", value, re.IGNORECASE):
            raise ValueError("github must be a GitHub username without @")
        return value

    @model_validator(mode="after")
    def valid_derived_identifiers(self) -> ProposalSubmission:
        if len(self.domain_slug) < 2:
            raise ValueError("domain must contain at least two ASCII letters")
        if len(self.field_slug) < 2:
            raise ValueError("field_name must contain at least two ASCII letters")
        if len(self.task_slug) < 3:
            raise ValueError("title must produce a task identifier of at least three characters")
        return self

    @staticmethod
    def slug_alpha(value: str) -> str:
        return re.sub(r"^-+|-+$", "", re.sub(r"-{2,}", "-", re.sub(r"[^a-z]+", "-", value.lower())))[:79]

    @staticmethod
    def slugify(value: str) -> str:
        return re.sub(r"^-+|-+$", "", re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())))[:80]

    @property
    def domain_slug(self) -> str:
        return self.slug_alpha(self.domain)

    @property
    def field_slug(self) -> str:
        return self.slug_alpha(self.field_name)

    @property
    def task_slug(self) -> str:
        return self.slugify(self.title)

    def with_github_identity(self, github_login: str | None) -> ProposalSubmission:
        return self.model_copy(update={"github": github_login or self.github})

    def render_discussion(self) -> str:
        return "\n".join(
            (
                "## Scientific Domain",
                "",
                f"{self.domain} > {self.field_name}",
                "",
                "## Scientific Problem",
                self.problem,
                "",
                "## Solvability",
                self.solvability,
                "",
                "## References & Resources",
                self.references,
                "",
                "## Environment",
                "",
                "### Software and tools",
                self.software,
                "",
                "### Dataset & artifacts",
                self.dataset,
                "",
                "### Computation resources (time and device)",
                self.compute,
                "",
                "### Expected workflow & outputs",
                self.workflow,
                "",
                "## Evaluation",
                "",
                "### How will this task be evaluated?",
                self.evaluation,
                "",
                "### Is there risk of cheating and leakage?",
                self.leakage,
                "",
                "## Contributor",
                "",
                f"Name: {self.name}",
                f"Institution / affiliation: {self.affiliation or 'None provided'}",
                f"GitHub: https://github.com/{self.github}",
                "",
                "## Task Metadata",
                f"Proposed task slug: `{self.task_slug}`",
                "",
                "---",
                "Submitted via ai4sbench contribution form",
            )
        )


class CloudProfileCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,118}$")
    provider: Literal["aws"] = "aws"
    allocation: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
