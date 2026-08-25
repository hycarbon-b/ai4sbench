from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


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


class ProposalAuthorInformation(BaseModel):
    """Author data embedded in the published Discussion, never trusted from the browser."""

    author: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role: str | None = Field(default=None, max_length=300)
    professional_profile: str | None = Field(default=None, max_length=1000)
    academic_profile: str | None = Field(default=None, max_length=1000)
    github: str | None = Field(default=None, max_length=100)
    discord: str | None = Field(default=None, max_length=100)
    recommended_reviewers: str | None = Field(default=None, max_length=1000)
    relevant_experience: str | None = Field(default=None, max_length=6000)
    conflicts_of_interest: str = Field(default="None", max_length=6000)


class ProposalDocument(BaseModel):
    """Canonical proposal payload shared by the form, SQLite, and GitHub Discussion renderer."""

    schema_version: str = "tb-science-proposal/v1"
    title: str = Field(min_length=12, max_length=160)
    domain: str = Field(pattern=r"^[a-z][a-z-]{1,78}$")
    field: str = Field(pattern=r"^[a-z][a-z-]{1,78}$")
    subfield: str = Field(min_length=2, max_length=160)
    task_slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    scientific_problem: str = Field(
        min_length=80,
        max_length=12000,
        validation_alias=AliasChoices("scientific_problem", "abstract"),
    )
    workflow_details: str = Field(min_length=20, max_length=12000)
    dependencies_and_system_requirements: str = Field(min_length=10, max_length=6000)
    dataset: str = Field(min_length=10, max_length=12000)
    evaluation_strategy: str = Field(min_length=20, max_length=12000)
    complexity: str = Field(min_length=20, max_length=12000)
    references_and_resources: str = Field(
        min_length=40,
        max_length=12000,
        validation_alias=AliasChoices("references_and_resources", "evidence"),
    )
    additional_information: str = Field(default="None provided", max_length=6000)
    author_information: ProposalAuthorInformation = Field(default_factory=ProposalAuthorInformation)

    @field_validator(
        "scientific_problem",
        "workflow_details",
        "dependencies_and_system_requirements",
        "dataset",
        "evaluation_strategy",
        "complexity",
        "references_and_resources",
        "additional_information",
        mode="before",
    )
    @classmethod
    def strip_content(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @staticmethod
    def display_name(value: str) -> str:
        return " ".join(part.capitalize() for part in value.split("-"))

    def with_author(self, *, github_login: str | None, email: str) -> ProposalDocument:
        """Bind identity from GitHub OAuth instead of accepting a browser-supplied identity."""

        login = github_login or email
        return self.model_copy(
            update={
                "author_information": self.author_information.model_copy(
                    update={
                        "author": self.author_information.author or login,
                        "email": self.author_information.email or email,
                        "github": login,
                    }
                )
            }
        )

    def render_discussion(self) -> str:
        """Render the public, Dashboard-compatible Task Proposal Discussion body."""

        author = self.author_information

        def line(label: str, value: str | None) -> str:
            return f"{label}: {value or 'None provided'}"

        return "\n".join(
            (
                "## Scientific Domain",
                "",
                f"{self.display_name(self.domain)} > {self.display_name(self.field)} > {self.subfield}",
                "",
                "## Scientific Problem",
                self.scientific_problem,
                "",
                "## Workflow Details",
                self.workflow_details,
                "",
                "## Dependencies & System Requirements",
                self.dependencies_and_system_requirements,
                "",
                "## Dataset",
                self.dataset,
                "",
                "## Evaluation Strategy",
                self.evaluation_strategy,
                "",
                "## Complexity",
                self.complexity,
                "",
                "## References & Resources",
                self.references_and_resources,
                "",
                "## Additional Information",
                self.additional_information,
                "",
                "## Task Metadata",
                f"Proposed task slug: `{self.task_slug}`",
                "",
                "## Author Information",
                line("Author", author.author),
                line("Email", author.email),
                line("Role", author.role),
                line("Professional Profile", author.professional_profile),
                line("Academic Profile", author.academic_profile),
                f"GitHub: https://github.com/{author.github}" if author.github else "GitHub: None provided",
                line("Discord", author.discord),
                line("Recommended Reviewers", author.recommended_reviewers),
                line("Relevant Experience", author.relevant_experience),
                "Commercial Affiliation & Conflicts of Interest:",
                author.conflicts_of_interest,
                "",
                "---",
                "Submitted via ai4sbench contribution form",
            )
        )


# Compatibility name for integrations that still import the old request model.
ProposalCreate = ProposalDocument


class CloudProfileCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,118}$")
    provider: Literal["aws"] = "aws"
    allocation: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
