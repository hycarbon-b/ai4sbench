from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    author_id: Mapped[str] = mapped_column(String(36), index=True)
    author_login: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(160))
    abstract: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(80))
    field: Mapped[str] = mapped_column(String(80))
    task_slug: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[str] = mapped_column(Text)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_valid: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    discussion_url: Mapped[str | None] = mapped_column(String(500))
    discussion_node_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    discussion_number: Mapped[int | None] = mapped_column(Integer)
    github_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    github_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_schema_version: Mapped[str | None] = mapped_column(String(80))
    review_decision: Mapped[str | None] = mapped_column(String(32))
    review_short_description: Mapped[str | None] = mapped_column(Text)
    review_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_difficulty: Mapped[str | None] = mapped_column(String(80))
    review_scientific_value: Mapped[str | None] = mapped_column(Text)
    review_primary_metric: Mapped[str | None] = mapped_column(Text)
    review_primary_metric_short: Mapped[str | None] = mapped_column(String(240))
    review_secondary_metrics: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_verification_method: Mapped[str | None] = mapped_column(Text)
    review_estimated_runtime: Mapped[str | None] = mapped_column(String(240))
    review_compute_budget: Mapped[str | None] = mapped_column(String(240))
    review_token_budget: Mapped[str | None] = mapped_column(String(240))
    review_baseline_results: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_failure_modes: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_notes: Mapped[str | None] = mapped_column(Text)
    review_reviewer_login: Mapped[str | None] = mapped_column(String(100))
    review_comment_node_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    review_comment_url: Mapped[str | None] = mapped_column(String(500))
    review_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_input_valid: Mapped[bool] = mapped_column(default=False)
    review_document: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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


class WebhookDelivery(Base):
    """One inspectable outbound webhook message and its delivery state."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        Index("ix_webhook_deliveries_claim", "state", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    destination_url: Mapped[str] = mapped_column(Text)
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
