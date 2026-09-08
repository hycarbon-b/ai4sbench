from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROPOSAL_DOMAIN_OPTIONS = (
    "Materials Science",
    "Physics",
    "Chemistry",
    "Biology",
    "AI / ML",
    "Applied Mathematics",
    "Interdisciplinary",
)


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

    @field_validator("domain")
    @classmethod
    def normalize_domain_list(cls, value: str) -> str:
        """Preserve readable domain names in one canonical comma-separated string."""

        domains = [item.strip() for item in value.split(",") if item.strip()]
        if not domains:
            raise ValueError("domain must contain at least one domain")
        return ", ".join(dict.fromkeys(domains))

    @model_validator(mode="after")
    def valid_derived_identifiers(self) -> ProposalSubmission:
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


REVIEW_SCHEMA_VERSION = "ai4sbench-proposal-review/v1"
REVIEW_COMMENT_MARKER = "<!-- ai4sbench-proposal-review:v1 -->"


class ProposalReview(BaseModel):
    """Canonical reviewer reply stored on the proposal and rendered to GitHub Markdown."""

    model_config = ConfigDict(extra="forbid")

    review_schema_version: Literal["ai4sbench-proposal-review/v1"] = REVIEW_SCHEMA_VERSION
    review_decision: Literal["approved", "changes_requested", "rejected"]
    review_short_description: str = Field(min_length=20, max_length=2_000)
    review_tags: list[str] = Field(min_length=1, max_length=30)
    review_difficulty: str = Field(min_length=2, max_length=80)
    review_scientific_value: str = Field(min_length=20, max_length=8_000)
    review_primary_metric: str = Field(min_length=2, max_length=1_000)
    review_primary_metric_short: str | None = Field(default=None, max_length=240)
    review_secondary_metrics: list[str] = Field(default_factory=list, max_length=20)
    review_verification_method: str = Field(min_length=20, max_length=8_000)
    review_estimated_runtime: str | None = Field(default=None, max_length=240)
    review_compute_budget: str | None = Field(default=None, max_length=240)
    review_token_budget: str | None = Field(default=None, max_length=240)
    review_baseline_results: list[str] = Field(default_factory=list, max_length=30)
    review_failure_modes: list[str] = Field(default_factory=list, max_length=30)
    review_notes: str | None = Field(default=None, max_length=8_000)

    @field_validator(
        "review_short_description",
        "review_difficulty",
        "review_scientific_value",
        "review_primary_metric",
        "review_primary_metric_short",
        "review_verification_method",
        "review_estimated_runtime",
        "review_compute_budget",
        "review_token_budget",
        "review_notes",
        mode="before",
    )
    @classmethod
    def strip_review_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator(
        "review_tags",
        "review_secondary_metrics",
        "review_baseline_results",
        "review_failure_modes",
        mode="before",
    )
    @classmethod
    def normalize_review_lists(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [str(item).strip() for item in value if str(item).strip()]

    def render_comment(self) -> str:
        def section(title: str, value: str | None) -> list[str]:
            return [f"### {title}", "", value or "", ""]

        def list_section(title: str, values: list[str]) -> list[str]:
            return [f"### {title}", "", *(f"- {value}" for value in values), ""]

        lines = [
            REVIEW_COMMENT_MARKER,
            "",
            "## AI4S-Bench Proposal Review",
            "",
            f"Decision: {self.review_decision}",
            "",
        ]
        lines += section("Short Description", self.review_short_description)
        lines += list_section("Tags", self.review_tags)
        lines += section("Difficulty", self.review_difficulty)
        lines += section("Scientific Value", self.review_scientific_value)
        lines += section("Primary Metric", self.review_primary_metric)
        lines += section("Primary Metric Short", self.review_primary_metric_short)
        lines += list_section("Secondary Metrics", self.review_secondary_metrics)
        lines += section("Verification Method", self.review_verification_method)
        lines += section("Estimated Runtime", self.review_estimated_runtime)
        lines += section("Compute Budget", self.review_compute_budget)
        lines += section("Token Budget", self.review_token_budget)
        lines += list_section("Baseline Results", self.review_baseline_results)
        lines += list_section("Failure Modes", self.review_failure_modes)
        lines += section("Review Notes", self.review_notes)
        return "\n".join(lines).rstrip() + "\n"


class CloudProfileCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,118}$")
    provider: Literal["aws"] = "aws"
    allocation: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


# ---- HTTP response contracts -------------------------------------------------
# Keep every public API response here so OpenAPI is a stable, readable contract
# rather than a collection of anonymous ``dict`` schemas.


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


class WebhookDeliveryResponse(BaseModel):
    id: str
    event_type: str
    destination_url: str
    payload: dict[str, Any]
    dedupe_key: str
    state: str
    attempts: int
    max_attempts: int
    available_at: datetime
    last_error: str | None
    response_status: int | None
    response_body: str | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryListResponse(BaseModel):
    items: list[WebhookDeliveryResponse]


class WorkerRunReference(BaseModel):
    id: str
    config: dict[str, Any]


class WorkerClaimResponse(BaseModel):
    run: WorkerRunReference
    task_revision: TaskRevisionResponse
    session_token: str


class AuthenticatedUserResponse(BaseModel):
    id: str
    email: str
    github_login: str
    role: str
    can_review: bool


class AuthConfigResponse(BaseModel):
    github_login_enabled: bool


class ProposalListItemResponse(BaseModel):
    id: str
    title: str
    domain: str
    field: str
    task_slug: str
    status: str
    discussion_url: str | None
    discussion_number: int | None
    author_login: str | None
    input_valid: bool


class ProposalListResponse(BaseModel):
    items: list[ProposalListItemResponse]


class ProposalBoardItemResponse(BaseModel):
    """Public Website projection combining proposal, review and latest task revision."""

    id: str
    title: str
    domain: str
    field_name: str
    problem: str
    solvability: str
    references: str
    software: str
    dataset: str
    compute: str
    workflow: str
    evaluation: str
    leakage: str
    name: str
    affiliation: str
    github: str
    task_slug: str
    status: str
    discussion_url: str | None
    discussion_number: int | None
    input_valid: bool
    created_at: datetime
    updated_at: datetime

    review_schema_version: str | None
    review_decision: Literal["approved", "changes_requested", "rejected"] | None
    review_short_description: str | None
    review_tags: list[str]
    review_difficulty: str | None
    review_scientific_value: str | None
    review_primary_metric: str | None
    review_primary_metric_short: str | None
    review_secondary_metrics: list[str]
    review_verification_method: str | None
    review_estimated_runtime: str | None
    review_compute_budget: str | None
    review_token_budget: str | None
    review_baseline_results: list[str]
    review_failure_modes: list[str]
    review_notes: str | None
    review_reviewer_login: str | None
    review_comment_url: str | None
    review_created_at: datetime | None
    review_updated_at: datetime | None
    review_input_valid: bool

    revision_id: str | None
    revision_repo_url: str | None
    revision_commit_sha: str | None
    revision_task_path: str | None
    revision_resource_requirements: dict[str, int] | None
    revision_pull_request_url: str | None
    revision_release: str | None
    revision_created_at: datetime | None
    revision_agent_results: list[dict[str, Any]]


class ProposalBoardListResponse(BaseModel):
    items: list[ProposalBoardItemResponse]


class ProposalDomainListResponse(BaseModel):
    """The single public source for selectable proposal domains."""

    items: list[str]


class ProposalDerivedResponse(BaseModel):
    domain: str
    field: str
    task_slug: str


class DiscussionPreviewResponse(BaseModel):
    title: str
    body: str


class ProposalPreviewResponse(BaseModel):
    input: ProposalSubmission | None
    derived: ProposalDerivedResponse | None
    discussion: DiscussionPreviewResponse | None
    missing_fields: list[str] | None = None
    github_identity_source: Literal["form", "authenticated"] | None = None


class ProposalPublishedResponse(ProposalPreviewResponse):
    id: str
    status: str
    discussion_url: str


class ReviewCommentPreviewResponse(BaseModel):
    body: str


class ProposalReviewPreviewResponse(BaseModel):
    input: ProposalReview
    comment: ReviewCommentPreviewResponse


class ProposalReviewPublishedResponse(ProposalReviewPreviewResponse):
    proposal_id: str
    discussion_url: str
    review_comment_node_id: str
    review_comment_url: str


class ProposalSyncResponse(BaseModel):
    scanned_count: int
    created_count: int
    updated_count: int
    invalid_count: int
    reviewed_count: int
    invalid_review_count: int


class PullRequestInstructionsResponse(BaseModel):
    branch: str
    task_path: str
    proposal_url: str
    compare_url: str


class CloudProfileResponse(BaseModel):
    id: str
    name: str
    provider: Literal["aws"]
    allocation: dict[str, Any]
    enabled: bool


class CloudProfileListResponse(BaseModel):
    items: list[CloudProfileResponse]
