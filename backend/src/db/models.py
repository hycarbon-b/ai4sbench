from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import ProposalReviewDecision, ProposalStatus, ReviewerApplicationStatus
from src.db.database import Base


def utcnow() -> datetime:
    """返回带 UTC 时区的当前时间，供模型时间字段使用。"""
    return datetime.now(UTC)


class TaskRevision(Base):
    __tablename__ = "task_revisions"
    __table_args__ = (UniqueConstraint("repo_url", "commit_sha", "task_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repo_url: Mapped[str] = mapped_column(String(500))
    commit_sha: Mapped[str] = mapped_column(String(64))
    task_path: Mapped[str] = mapped_column(String(500))
    resource_requirements: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("proposals.id", ondelete="SET NULL"), index=True
    )
    pull_request_url: Mapped[str | None] = mapped_column(String(500))
    release: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExecutionPlan(Base):
    __tablename__ = "execution_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_revision_id: Mapped[str] = mapped_column(ForeignKey("task_revisions.id", ondelete="RESTRICT"))
    state: Mapped[str] = mapped_column(String(32), default="draft")
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    task_revision: Mapped[TaskRevision] = relationship(lazy="joined")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(ForeignKey("execution_plans.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    state: Mapped[str] = mapped_column(String(32), default="queued")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    instance_id: Mapped[str | None] = mapped_column(String(64))
    instance_state: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    plan: Mapped[ExecutionPlan] = relationship(lazy="joined")


class WorkerCredential(Base):
    __tablename__ = "worker_credentials"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    bootstrap_token_hash: Mapped[str] = mapped_column(String(64))
    session_token_hash: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_created", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ControlLock(Base):
    __tablename__ = "control_locks"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class CloudProfile(Base):
    """An administrator-owned, non-secret cloud execution allocation."""

    __tablename__ = "cloud_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provider: Mapped[str] = mapped_column(String(40), default="aws")
    allocation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Proposal(Base):
    """A public task pitch that is mirrored to a GitHub Discussion."""

    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique identifier for the proposal.",
    )
    author_id: Mapped[str] = mapped_column(
        String(36), index=True, comment="Identifier of the proposal author."
    )
    author_login: Mapped[str | None] = mapped_column(
        String(100), comment="GitHub login of the proposal author."
    )
    title: Mapped[str] = mapped_column(String(160), comment="Proposal title.")
    abstract: Mapped[str] = mapped_column(Text, comment="Short summary of the proposed task.")
    domain: Mapped[str] = mapped_column(
        String(160), comment="Ordered comma-separated scientific domains; the first is primary."
    )
    field: Mapped[str] = mapped_column(
        String(80), comment="Research field slug within the first (primary) domain."
    )
    task_slug: Mapped[str] = mapped_column(
        String(100), comment="URL-friendly identifier for the proposed task."
    )
    evidence: Mapped[str] = mapped_column(Text, comment="Evidence supporting the proposal.")
    document: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, comment="Structured proposal content."
    )
    input_valid: Mapped[bool] = mapped_column(
        default=True, comment="Whether the submitted proposal input passed validation."
    )
    status: Mapped[ProposalStatus] = mapped_column(
        SAEnum(
            ProposalStatus,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ck_proposals_status",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        default=ProposalStatus.DRAFT,
        comment="Current proposal lifecycle status.",
    )
    discussion_url: Mapped[str | None] = mapped_column(
        String(500), comment="URL of the mirrored GitHub Discussion."
    )
    discussion_node_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, comment="Global node ID of the GitHub Discussion."
    )
    discussion_number: Mapped[int | None] = mapped_column(Integer, comment="Number of the GitHub Discussion.")
    discord_message_url: Mapped[str | None] = mapped_column(
        String(500), comment="URL of the related Discord message."
    )
    github_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Creation time reported by GitHub."
    )
    github_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Last update time reported by GitHub."
    )
    review_schema_version: Mapped[str | None] = mapped_column(
        String(80), comment="Schema version used for the review document."
    )
    review_decision: Mapped[ProposalReviewDecision | None] = mapped_column(
        SAEnum(
            ProposalReviewDecision,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ck_proposals_review_decision",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        comment="Decision recorded by the review.",
    )
    review_short_description: Mapped[str | None] = mapped_column(
        Text, comment="Concise description produced by the review."
    )
    review_tags: Mapped[list[str]] = mapped_column(JSON, default=list, comment="Tags assigned by the review.")
    review_difficulty: Mapped[str | None] = mapped_column(String(80), comment="Estimated task difficulty.")
    review_scientific_value: Mapped[str | None] = mapped_column(
        Text, comment="Assessment of the proposal scientific value."
    )
    review_primary_metric: Mapped[str | None] = mapped_column(
        Text, comment="Primary metric used to evaluate the task."
    )
    review_primary_metric_short: Mapped[str | None] = mapped_column(
        String(240), comment="Short display form of the primary metric."
    )
    review_secondary_metrics: Mapped[list[str]] = mapped_column(
        JSON, default=list, comment="Additional evaluation metrics."
    )
    review_verification_method: Mapped[str | None] = mapped_column(
        Text, comment="Method for verifying task results."
    )
    review_estimated_runtime: Mapped[str | None] = mapped_column(
        String(240), comment="Estimated runtime for the task."
    )
    review_compute_budget: Mapped[str | None] = mapped_column(
        String(240), comment="Estimated compute resource budget."
    )
    review_token_budget: Mapped[str | None] = mapped_column(String(240), comment="Estimated token budget.")
    review_baseline_results: Mapped[list[str]] = mapped_column(
        JSON, default=list, comment="Expected or known baseline results."
    )
    review_failure_modes: Mapped[list[str]] = mapped_column(
        JSON, default=list, comment="Known or anticipated failure modes."
    )
    review_notes: Mapped[str | None] = mapped_column(Text, comment="Additional reviewer notes.")
    review_reviewer_login: Mapped[str | None] = mapped_column(
        String(100), comment="GitHub login of the reviewer."
    )
    review_comment_node_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, comment="Global node ID of the GitHub review comment."
    )
    review_comment_url: Mapped[str | None] = mapped_column(
        String(500), comment="URL of the GitHub review comment."
    )
    review_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Creation time of the GitHub review comment."
    )
    review_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Last update time of the GitHub review comment."
    )
    review_input_valid: Mapped[bool] = mapped_column(
        default=False, comment="Whether the review input passed validation."
    )
    review_document: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, comment="Structured review content."
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, comment="Time when the proposal was soft-deleted."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, comment="Time when the proposal record was created."
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        comment="Time when the proposal record was last updated.",
    )

    @property
    def primary_domain(self) -> str:
        """First comma-separated domain, used as the primary scientific classification."""
        return self.domain.split(",", 1)[0].strip()


class ProposalHumanReview(Base):
    """One GitHub Discussion review reply; edits update this reply, not its siblings."""

    __tablename__ = "proposal_human_reviews"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique identifier for the human review.",
    )
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("proposals.id"), index=True, comment="Identifier of the reviewed proposal."
    )
    comment_node_id: Mapped[str] = mapped_column(
        String(100), unique=True, comment="Global node ID of the GitHub review comment."
    )
    comment_url: Mapped[str] = mapped_column(String(500), comment="URL of the GitHub review comment.")
    reviewer_login: Mapped[str] = mapped_column(String(100), comment="GitHub login of the human reviewer.")
    decision: Mapped[ProposalReviewDecision | None] = mapped_column(
        SAEnum(
            ProposalReviewDecision,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ck_proposal_human_reviews_decision",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        comment="Decision recorded by the reviewer.",
    )
    input_valid: Mapped[bool] = mapped_column(
        default=False, comment="Whether the submitted review input passed validation."
    )
    document: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, comment="Structured human review content."
    )
    github_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Creation time reported by GitHub."
    )
    github_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Last update time reported by GitHub."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, comment="Time when the review record was created."
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        comment="Time when the review record was last updated.",
    )


class ReviewerApplication(Base):
    """A Website reviewer application and its administrator decision."""

    __tablename__ = "reviewer_applications"
    __table_args__ = (Index("ix_reviewer_applications_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schema_version: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    affiliation: Mapped[str] = mapped_column(String(300))
    email: Mapped[str] = mapped_column(String(200), index=True)
    github: Mapped[str | None] = mapped_column(String(100), index=True)
    role: Mapped[str | None] = mapped_column(String(200))
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    domains_display: Mapped[list[str]] = mapped_column(JSON, default=list)
    field: Mapped[str | None] = mapped_column(String(80))
    subfield: Mapped[str | None] = mapped_column(String(200))
    research_background: Mapped[str] = mapped_column(Text)
    status: Mapped[ReviewerApplicationStatus] = mapped_column(
        SAEnum(
            ReviewerApplicationStatus,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ck_reviewer_applications_status",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        default=ReviewerApplicationStatus.PENDING,
        index=True,
    )
    admin_notes: Mapped[str | None] = mapped_column(Text)
    submitted_by_login: Mapped[str | None] = mapped_column(String(100))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def domain(self) -> str:
        """Proposal-shaped domain value for API clients; legacy storage stays intact."""
        return ", ".join(self.domains_display or [])

    @property
    def field_name(self) -> str | None:
        """Proposal-shaped name for the legacy reviewer subfield value."""
        return self.subfield


class EmailVerificationChallenge(Base):
    """Short-lived email proof scoped to one purpose and optional account."""

    __tablename__ = "email_verification_challenges"
    __table_args__ = (Index("ix_email_verifications_email_created", "email", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(100), default="")
    code_hash: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer)
    verified_at: Mapped[int | None] = mapped_column(Integer)
    consumed_at: Mapped[int | None] = mapped_column(Integer)


class ContributionDashboardSnapshot(Base):
    """One immutable, schema-versioned TBS-compatible GitHub projection."""

    __tablename__ = "contribution_dashboard_snapshots"
    __table_args__ = (UniqueConstraint("repository", "source_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repository: Mapped[str] = mapped_column(String(200), index=True)
    source_revision: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetch_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    partial: Mapped[bool] = mapped_column(default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatabaseJob(Base):
    __tablename__ = "database_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        Index("ix_database_jobs_claim", "state", "available_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OutboundDelivery(Base):
    """One inspectable outbound delivery and its durable retry state."""

    __tablename__ = "outbound_deliveries"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        Index("ix_outbound_deliveries_claim", "state", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    delivery_type: Mapped[str] = mapped_column(String(32), index=True)
    destination: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(240))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AIReviewPublication(Base):
    """One bot-owned sticky publication per immutable Discussion identity."""

    __tablename__ = "ai_review_publications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    discussion_node_id: Mapped[str] = mapped_column(String(100), unique=True)
    repository: Mapped[str] = mapped_column(String(200))
    discussion_number: Mapped[int] = mapped_column(Integer)
    latest_review_id: Mapped[str | None] = mapped_column(String(36))
    github_comment_id: Mapped[str | None] = mapped_column(String(100))
    github_comment_url: Mapped[str | None] = mapped_column(String(500))
    discord_message_id: Mapped[str | None] = mapped_column(String(30))
    discord_thread_id: Mapped[str | None] = mapped_column(String(30))
    discord_message_url: Mapped[str | None] = mapped_column(String(500))
    discord_create_started: Mapped[bool] = mapped_column(default=False)
    assigned_reviewer: Mapped[str | None] = mapped_column(String(100))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProposalAIReview(Base):
    __tablename__ = "proposal_ai_reviews"
    __table_args__ = (UniqueConstraint("repository", "run_id", "run_attempt"),)
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique identifier for the AI review.",
    )
    publication_id: Mapped[str] = mapped_column(
        ForeignKey("ai_review_publications.id"),
        index=True,
        comment="Identifier of the associated review publication.",
    )
    repository: Mapped[str] = mapped_column(
        String(200), comment="GitHub repository that owns the workflow run."
    )
    run_id: Mapped[int] = mapped_column(Integer, comment="GitHub Actions workflow run ID.")
    run_attempt: Mapped[int] = mapped_column(Integer, comment="Attempt number for the workflow run.")
    request_digest: Mapped[str] = mapped_column(String(64), comment="Digest identifying the review request.")
    document: Mapped[dict[str, Any]] = mapped_column(JSON, comment="Structured AI review content.")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, comment="Time when the AI review record was created."
    )
