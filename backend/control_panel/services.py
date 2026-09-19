from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Principal
from .config import Settings
from .job_queue import enqueue
from .models import (
    AuditEvent,
    DatabaseJob,
    ExecutionPlan,
    Run,
    RunEvent,
    TaskRevision,
    WorkerCredential,
)
from .providers import EC2Provider
from .schemas import PlanConfig

ACTIVE_STATES = {"provisioning", "running", "terminating"}
TERMINAL_STATES = {"succeeded", "failed", "timed_out", "cancelled"}


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class CapacityError(Exception):
    pass


class WorkerAuthError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


async def begin_immediate(session: AsyncSession) -> None:
    await session.execute(text("BEGIN IMMEDIATE"))


def task_dict(item: TaskRevision) -> dict[str, Any]:
    return {
        "id": item.id,
        "repo_url": item.repo_url,
        "commit_sha": item.commit_sha,
        "task_path": item.task_path,
        "resource_requirements": item.resource_requirements,
        "proposal_id": item.proposal_id,
        "pull_request_url": item.pull_request_url,
        "release": item.release,
        "created_at": item.created_at,
    }


def plan_dict(item: ExecutionPlan) -> dict[str, Any]:
    return {
        "id": item.id,
        "task_revision_id": item.task_revision_id,
        "state": item.state,
        "config": item.config,
        "lock_version": item.lock_version,
        "approved_by": item.approved_by,
        "approved_at": item.approved_at,
        "created_at": item.created_at,
        "repo_url": item.task_revision.repo_url,
        "commit_sha": item.task_revision.commit_sha,
        "task_path": item.task_revision.task_path,
    }


def run_dict(item: Run) -> dict[str, Any]:
    revision = item.plan.task_revision
    return {
        "id": item.id,
        "plan_id": item.plan_id,
        "state": item.state,
        "config": item.config_snapshot,
        "deadline_at": item.deadline_at,
        "instance_id": item.instance_id,
        "instance_state": item.instance_state,
        "result": item.result,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "repo_url": revision.repo_url,
        "commit_sha": revision.commit_sha,
        "task_path": revision.task_path,
    }


def event_dict(item: RunEvent) -> dict[str, Any]:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "event_type": item.event_type,
        "message": item.message,
        "payload": item.payload,
        "created_at": item.created_at,
    }


def add_event(
    session: AsyncSession,
    run_id: str,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        RunEvent(
            run_id=run_id,
            event_type=event_type,
            message=message,
            payload=payload or {},
        )
    )


def add_audit(
    session: AsyncSession,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor=principal.subject,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload or {},
        )
    )


async def upsert_task_revision(
    session: AsyncSession, values: dict[str, Any], principal: Principal
) -> TaskRevision:
    await begin_immediate(session)
    link_values = {
        field: values[field] for field in ("proposal_id", "pull_request_url", "release") if field in values
    }
    task_values = {
        "repo_url": values["repo_url"],
        "commit_sha": values["commit_sha"],
        "task_path": values["task_path"],
        "resource_requirements": values["resource_requirements"],
        **link_values,
    }
    item = await session.scalar(
        select(TaskRevision).where(
            TaskRevision.repo_url == task_values["repo_url"],
            TaskRevision.commit_sha == task_values["commit_sha"],
            TaskRevision.task_path == task_values["task_path"],
        )
    )
    if item is None:
        item = TaskRevision(**task_values)
        session.add(item)
        await session.flush()
        add_audit(session, principal, "task_revision.created", "task_revision", item.id)
    else:
        changed = False
        requirements = task_values["resource_requirements"]
        if item.resource_requirements != requirements:
            item.resource_requirements = requirements
            changed = True
        for field, value in link_values.items():
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed = True
        if changed:
            add_audit(session, principal, "task_revision.updated", "task_revision", item.id)
    await session.commit()
    await session.refresh(item)
    return item


async def upsert_task_revisions(
    session: AsyncSession, values_list: list[dict[str, Any]], principal: Principal
) -> tuple[list[TaskRevision], int, int]:
    """Upsert a repository snapshot in one SQLite transaction."""
    await begin_immediate(session)
    created = 0
    updated = 0
    items: list[TaskRevision] = []
    try:
        for values in values_list:
            link_values = {
                field: values[field]
                for field in ("proposal_id", "pull_request_url", "release")
                if field in values
            }
            task_values = {
                "repo_url": values["repo_url"],
                "commit_sha": values["commit_sha"],
                "task_path": values["task_path"],
                "resource_requirements": values["resource_requirements"],
                **link_values,
            }
            item = await session.scalar(
                select(TaskRevision).where(
                    TaskRevision.repo_url == task_values["repo_url"],
                    TaskRevision.commit_sha == task_values["commit_sha"],
                    TaskRevision.task_path == task_values["task_path"],
                )
            )
            if item is None:
                item = TaskRevision(**task_values)
                session.add(item)
                await session.flush()
                add_audit(session, principal, "task_revision.created", "task_revision", item.id)
                created += 1
            else:
                changed = False
                if item.resource_requirements != task_values["resource_requirements"]:
                    item.resource_requirements = task_values["resource_requirements"]
                    changed = True
                for field, value in link_values.items():
                    if getattr(item, field) != value:
                        setattr(item, field, value)
                        changed = True
                if changed:
                    add_audit(session, principal, "task_revision.updated", "task_revision", item.id)
                    updated += 1
            items.append(item)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    for item in items:
        await session.refresh(item)
    return items, created, updated


def validate_instance_resources(config: PlanConfig, revision: TaskRevision, settings: Settings) -> None:
    instance_type = str(config.instance_type or settings.ec2_instance_type)
    capacity = settings.ec2_instance_resources.get(instance_type)
    requirements = revision.resource_requirements or {}
    if not capacity or not requirements:
        return
    for key, label in (("cpus", "vCPU"), ("memory_mb", "MiB memory")):
        required = int(requirements.get(key, 0))
        available = int(capacity.get(key, 0))
        if required > available:
            raise ConflictError(
                f"Task requires {required} {label}, but {instance_type} provides only {available}"
            )

    required_storage_mb = int(requirements.get("storage_mb", 0))
    configured_storage_gb = int(config.root_volume_gb or settings.ec2_root_volume_gb)
    configured_storage_mb = configured_storage_gb * 1024
    if required_storage_mb > configured_storage_mb:
        raise ConflictError(
            f"Task requires {required_storage_mb} MiB storage, but the root volume provides only "
            f"{configured_storage_mb} MiB"
        )


def resolve_instance_config(config: PlanConfig, revision: TaskRevision, settings: Settings) -> PlanConfig:
    """Choose the smallest configured x86 instance that meets a task's declared requirements."""
    requirements = revision.resource_requirements or {}
    required_storage_gb = max(1, (int(requirements.get("storage_mb", 0)) + 1023) // 1024)
    resolved_root_volume_gb = max(
        int(config.root_volume_gb or settings.ec2_root_volume_gb), required_storage_gb
    )
    if config.instance_type:
        resolved = config.model_copy(update={"root_volume_gb": resolved_root_volume_gb})
        validate_instance_resources(resolved, revision, settings)
        return resolved
    required_cpus = int(requirements.get("cpus", 0))
    required_memory = int(requirements.get("memory_mb", 0))
    candidates = [
        instance
        for instance in settings.ec2_allowed_instance_types
        if not instance.startswith("t4g")
        and settings.ec2_instance_resources.get(instance, {}).get("cpus", 0) >= required_cpus
        and settings.ec2_instance_resources.get(instance, {}).get("memory_mb", 0) >= required_memory
    ]
    if not candidates:
        raise ConflictError(f"No allowlisted instance satisfies {revision.task_path}")
    resolved = config.model_copy(
        update={"instance_type": candidates[0], "root_volume_gb": resolved_root_volume_gb}
    )
    validate_instance_resources(resolved, revision, settings)
    return resolved


async def create_plan(
    session: AsyncSession,
    task_revision_id: str,
    config: PlanConfig,
    principal: Principal,
    settings: Settings,
) -> ExecutionPlan:
    if config.instance_type and config.instance_type not in settings.ec2_allowed_instance_types:
        raise ConflictError("Instance type is not allowlisted")
    revision = await session.get(TaskRevision, task_revision_id)
    if revision is None:
        raise NotFoundError("Task revision not found")
    config = resolve_instance_config(config, revision, settings)
    item = ExecutionPlan(task_revision_id=revision.id, config=config.model_dump(mode="json"))
    session.add(item)
    await session.flush()
    add_audit(session, principal, "plan.created", "plan", item.id)
    await session.commit()
    return await session.scalar(select(ExecutionPlan).where(ExecutionPlan.id == item.id))  # type: ignore[return-value]


async def approve_plan(
    session: AsyncSession,
    plan_id: str,
    lock_version: int,
    principal: Principal,
) -> ExecutionPlan:
    await begin_immediate(session)
    plan = await session.scalar(select(ExecutionPlan).where(ExecutionPlan.id == plan_id))
    if plan is None:
        raise NotFoundError("Plan not found")
    result = await session.execute(
        update(ExecutionPlan)
        .where(
            ExecutionPlan.id == plan_id,
            ExecutionPlan.state == "draft",
            ExecutionPlan.lock_version == lock_version,
        )
        .values(
            state="approved",
            approved_by=principal.subject,
            approved_at=utcnow(),
            lock_version=ExecutionPlan.lock_version + 1,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise ConflictError("Plan changed or is already approved")
    add_audit(session, principal, "plan.approved", "plan", plan_id)
    await session.commit()
    return await session.scalar(select(ExecutionPlan).where(ExecutionPlan.id == plan_id))  # type: ignore[return-value]


async def create_run(
    session: AsyncSession,
    plan_id: str,
    timeout_minutes: int,
    idempotency_key: str,
    principal: Principal,
    settings: Settings,
) -> tuple[Run, bool]:
    if not idempotency_key or len(idempotency_key) > 200:
        raise ConflictError("A valid Idempotency-Key header is required")
    await begin_immediate(session)
    existing = await session.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
    if existing:
        await session.commit()
        return existing, False
    plan = await session.scalar(select(ExecutionPlan).where(ExecutionPlan.id == plan_id))
    if plan is None:
        await session.rollback()
        raise NotFoundError("Plan not found")
    if plan.state != "approved":
        await session.rollback()
        raise ConflictError("Only approved plans can start a run")
    revision_snapshot = task_dict(plan.task_revision)
    revision_snapshot["created_at"] = plan.task_revision.created_at.isoformat()
    snapshot = {
        "plan_config": plan.config,
        "task_revision": revision_snapshot,
        "approved_by": plan.approved_by,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
    }
    item = Run(
        plan_id=plan.id,
        idempotency_key=idempotency_key,
        state="queued",
        config_snapshot=snapshot,
        deadline_at=utcnow() + timedelta(minutes=timeout_minutes),
    )
    session.add(item)
    await session.flush()
    await enqueue(session, "launch_run", {"run_id": item.id}, f"launch:{item.id}")
    add_event(session, item.id, "run_queued", "Run was committed to the database queue")
    add_audit(session, principal, "run.created", "run", item.id, {"idempotency_key": idempotency_key})
    await session.commit()
    return await session.scalar(select(Run).where(Run.id == item.id)), True  # type: ignore[return-value]


async def create_manual_run(
    session: AsyncSession,
    task_revision_id: str,
    config: PlanConfig,
    timeout_minutes: int,
    idempotency_key: str,
    principal: Principal,
    settings: Settings,
) -> tuple[Run, bool]:
    """Create, approve, and queue a single operator-requested run atomically.

    A manual launch is still represented by an immutable approved plan.  This
    preserves the worker contract and audit trail while removing a distracting
    three-click workflow from the operations console.
    """

    if not idempotency_key or len(idempotency_key) > 200:
        raise ConflictError("A valid Idempotency-Key header is required")
    if config.instance_type and config.instance_type not in settings.ec2_allowed_instance_types:
        raise ConflictError("Instance type is not allowlisted")

    await begin_immediate(session)
    existing = await session.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
    if existing:
        await session.commit()
        return existing, False
    revision = await session.get(TaskRevision, task_revision_id)
    if revision is None:
        await session.rollback()
        raise NotFoundError("Task revision not found")
    config = resolve_instance_config(config, revision, settings)
    approved_at = utcnow()
    plan = ExecutionPlan(
        task_revision_id=revision.id,
        state="approved",
        config=config.model_dump(mode="json"),
        lock_version=1,
        approved_by=principal.subject,
        approved_at=approved_at,
    )
    session.add(plan)
    await session.flush()
    revision_snapshot = task_dict(revision)
    revision_snapshot["created_at"] = revision.created_at.isoformat()
    snapshot = {
        "plan_config": plan.config,
        "task_revision": revision_snapshot,
        "approved_by": plan.approved_by,
        "approved_at": approved_at.isoformat(),
        "launch_source": "manual_console",
    }
    run = Run(
        plan_id=plan.id,
        idempotency_key=idempotency_key,
        state="queued",
        config_snapshot=snapshot,
        deadline_at=approved_at + timedelta(minutes=timeout_minutes),
    )
    session.add(run)
    await session.flush()
    await enqueue(session, "launch_run", {"run_id": run.id}, f"launch:{run.id}")
    add_event(session, run.id, "run_queued", "Manual run was committed to the database queue")
    add_audit(session, principal, "plan.manual_approved", "plan", plan.id)
    add_audit(session, principal, "run.manual_created", "run", run.id, {"idempotency_key": idempotency_key})
    await session.commit()
    return await session.scalar(select(Run).where(Run.id == run.id)), True  # type: ignore[return-value]


async def create_manual_batch(
    session: AsyncSession,
    task_revision_ids: list[str],
    config: PlanConfig,
    timeout_minutes: int,
    idempotency_key: str,
    principal: Principal,
    settings: Settings,
) -> tuple[list[Run], int]:
    """Atomically enqueue one immutable manual run per requested task revision."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise ConflictError("A valid Idempotency-Key header is required")
    if config.agent != "oracle":
        raise ConflictError("Batch execution currently supports only the oracle agent")
    if config.instance_type and config.instance_type not in settings.ec2_allowed_instance_types:
        raise ConflictError("Instance type is not allowlisted")

    await begin_immediate(session)
    revisions = list(
        await session.scalars(select(TaskRevision).where(TaskRevision.id.in_(task_revision_ids)))
    )
    revision_by_id = {item.id: item for item in revisions}
    missing = [item for item in task_revision_ids if item not in revision_by_id]
    if missing:
        await session.rollback()
        raise NotFoundError("One or more task revisions were not found")

    approved_at = utcnow()
    created = 0
    runs: list[Run] = []
    try:
        for revision_id in task_revision_ids:
            revision = revision_by_id[revision_id]
            per_task_config = resolve_instance_config(config, revision, settings)
            run_key = "batch-" + hashlib.sha256(f"{idempotency_key}:{revision.id}".encode()).hexdigest()
            existing = await session.scalar(select(Run).where(Run.idempotency_key == run_key))
            if existing:
                runs.append(existing)
                continue

            plan = ExecutionPlan(
                task_revision_id=revision.id,
                state="approved",
                config=per_task_config.model_dump(mode="json"),
                lock_version=1,
                approved_by=principal.subject,
                approved_at=approved_at,
            )
            session.add(plan)
            await session.flush()
            revision_snapshot = task_dict(revision)
            revision_snapshot["created_at"] = revision.created_at.isoformat()
            snapshot = {
                "plan_config": plan.config,
                "task_revision": revision_snapshot,
                "approved_by": principal.subject,
                "approved_at": approved_at.isoformat(),
                "launch_source": "manual_batch",
            }
            run = Run(
                plan_id=plan.id,
                idempotency_key=run_key,
                state="queued",
                config_snapshot=snapshot,
                deadline_at=approved_at + timedelta(minutes=timeout_minutes),
            )
            session.add(run)
            await session.flush()
            await enqueue(session, "launch_run", {"run_id": run.id}, f"launch:{run.id}")
            add_event(session, run.id, "run_queued", "Batch run was committed to the database queue")
            add_audit(session, principal, "plan.manual_approved", "plan", plan.id)
            add_audit(session, principal, "run.batch_created", "run", run.id)
            runs.append(run)
            created += 1
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return [await session.scalar(select(Run).where(Run.id == run.id)) for run in runs], created  # type: ignore[return-value]


async def claim_worker(
    session: AsyncSession, run_id: str, token: str, settings: Settings | None = None
) -> dict[str, Any]:
    await begin_immediate(session)
    credential = await session.get(WorkerCredential, run_id)
    run = await session.get(Run, run_id)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (
        credential is None
        or run is None
        or credential.claimed_at is not None
        or run.state != "provisioning"
        or not secrets.compare_digest(credential.bootstrap_token_hash, digest)
    ):
        await session.rollback()
        raise WorkerAuthError("Job token is invalid, already used, or run is not claimable")
    session_token = secrets.token_urlsafe(32)
    credential.session_token_hash = hashlib.sha256(session_token.encode()).hexdigest()
    credential.claimed_at = utcnow()
    run.state = "running"
    run.instance_state = "running"
    run.version += 1
    # Record the worker image the run started from.  Because the worker source
    # is baked into the AMI, this is what makes a control-plane/worker version
    # mismatch diagnosable after the instance is gone.
    claimed_payload: dict[str, Any] = {"instance_id": run.instance_id}
    if settings is not None:
        claimed_payload |= {
            "bootstrap_mode": settings.ec2_bootstrap_mode,
            "worker_ami": settings.ec2_ami_id,
            "worker_ami_commit": settings.ec2_worker_ami_commit,
        }
    add_event(session, run_id, "worker_claimed", "Worker claimed the one-time job", claimed_payload)
    response = {
        "run": {"id": run.id, "config": run.config_snapshot},
        "task_revision": run.config_snapshot["task_revision"],
        "session_token": session_token,
    }
    await session.commit()
    return response


async def validate_worker_session(session: AsyncSession, run_id: str, token: str) -> Run:
    credential = await session.get(WorkerCredential, run_id)
    run = await session.get(Run, run_id)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (
        credential is None
        or run is None
        or not credential.session_token_hash
        or not secrets.compare_digest(credential.session_token_hash, digest)
    ):
        raise WorkerAuthError("Invalid worker session")
    return run


async def create_worker_event(
    session: AsyncSession,
    run_id: str,
    token: str,
    event_type: str,
    message: str,
    payload: dict[str, Any],
) -> RunEvent:
    await validate_worker_session(session, run_id, token)
    item = RunEvent(run_id=run_id, event_type=event_type, message=message, payload=payload)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def complete_worker(
    session: AsyncSession,
    run_id: str,
    token: str,
    state: str,
    result: dict[str, Any],
) -> Run:
    await begin_immediate(session)
    run = await validate_worker_session(session, run_id, token)
    if run.state != "running":
        await session.rollback()
        raise ConflictError("Run is not running")
    run.state = state
    run.result = result
    run.instance_state = "terminating" if run.instance_id else "terminated"
    run.version += 1
    add_event(session, run_id, "runner_completed", f"Harbor runner exited as {state}", result)
    if run.instance_id:
        await enqueue(session, "terminate_run", {"run_id": run.id}, f"terminate:{run.id}")
    await session.commit()
    return await session.scalar(select(Run).where(Run.id == run.id))  # type: ignore[return-value]


async def cancel_run(session: AsyncSession, run_id: str, principal: Principal) -> Run:
    await begin_immediate(session)
    run = await session.get(Run, run_id)
    if run is None:
        await session.rollback()
        raise NotFoundError("Run not found")
    if run.state in TERMINAL_STATES:
        await session.commit()
        return run
    run.state = "cancelled"
    run.instance_state = "terminating" if run.instance_id else "terminated"
    run.version += 1
    add_event(session, run.id, "run_cancelled", "Run was cancelled by an administrator")
    add_audit(session, principal, "run.cancelled", "run", run.id)
    if run.instance_id:
        await enqueue(session, "terminate_run", {"run_id": run.id}, f"terminate:{run.id}")
    await session.commit()
    return await session.scalar(select(Run).where(Run.id == run.id))  # type: ignore[return-value]


def deterministic_bootstrap_token(run_id: str, settings: Settings) -> str:
    digest = hmac.new(
        settings.job_token_secret.get_secret_value().encode(),
        run_id.encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def handle_launch(
    session: AsyncSession, run_id: str, provider: EC2Provider, settings: Settings
) -> None:
    await begin_immediate(session)
    run = await session.get(Run, run_id)
    if run is None:
        await session.rollback()
        raise NotFoundError("Run not found")
    if run.instance_id or run.state not in {"queued", "provisioning"}:
        await session.commit()
        return
    active = (
        await session.scalar(
            select(func.count()).select_from(Run).where(Run.id != run_id, Run.state.in_(ACTIVE_STATES))
        )
        or 0
    )
    if active >= settings.max_active_runs:
        await session.rollback()
        raise CapacityError("Worker capacity is full")
    token = deterministic_bootstrap_token(run_id, settings)
    credential = await session.get(WorkerCredential, run_id)
    if credential is None:
        session.add(
            WorkerCredential(
                run_id=run_id,
                bootstrap_token_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
        )
    run.state = "provisioning"
    run.instance_state = "pending"
    add_event(session, run_id, "launch_started", "EC2 launch request started")
    config = dict(run.config_snapshot["plan_config"])
    await session.commit()

    instance_id = await asyncio.to_thread(provider.launch, run_id, token, config)

    await begin_immediate(session)
    run = await session.get(Run, run_id)
    if run is None:
        await session.rollback()
        await asyncio.to_thread(provider.terminate, instance_id)
        return
    run.instance_id = instance_id
    run.instance_state = "pending"
    run.version += 1
    add_event(
        session,
        run_id,
        "instance_launched",
        "Worker instance was created",
        {"instance_id": instance_id},
    )
    await session.commit()


async def handle_terminate(session: AsyncSession, run_id: str, provider: EC2Provider) -> None:
    await begin_immediate(session)
    run = await session.get(Run, run_id)
    if run is None:
        await session.rollback()
        raise NotFoundError("Run not found")
    instance_id = run.instance_id
    await session.commit()
    if instance_id:
        await asyncio.to_thread(provider.terminate, instance_id)
    await begin_immediate(session)
    run = await session.get(Run, run_id)
    if run is None:
        await session.rollback()
        return
    run.instance_state = "terminated"
    run.version += 1
    add_event(session, run_id, "instance_terminated", "Worker instance was terminated")
    await session.commit()


async def reconcile(session: AsyncSession) -> int:
    now = utcnow()
    await begin_immediate(session)
    expired = list(
        await session.scalars(
            select(Run).where(Run.state.in_({"queued", "provisioning", "running"}), Run.deadline_at <= now)
        )
    )
    if not expired:
        await session.commit()
        return 0
    for run in expired:
        run.state = "timed_out"
        run.instance_state = "terminating" if run.instance_id else "terminated"
        run.version += 1
        add_event(session, run.id, "run_timed_out", "Run exceeded its approved deadline")
        if run.instance_id:
            await enqueue(session, "terminate_run", {"run_id": run.id}, f"terminate:{run.id}")
    await session.commit()
    return len(expired)


async def mark_job_exhausted(session: AsyncSession, job: DatabaseJob) -> None:
    run_id = str(job.payload.get("run_id", ""))
    run = await session.get(Run, run_id)
    if run is None:
        return
    if job.kind == "launch_run" and run.state in {"queued", "provisioning"}:
        run.state = "failed"
        run.instance_state = "launch_failed"
        run.result = {"error": "EC2 launch retries exhausted", "detail": job.last_error}
        add_event(session, run.id, "launch_failed", "EC2 launch retries were exhausted")
    elif job.kind == "terminate_run":
        run.instance_state = "termination_failed"
        add_event(session, run.id, "terminate_failed", "EC2 termination retries were exhausted")
    await session.commit()
