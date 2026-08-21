from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .auth import Principal, get_principal
from .config import Settings
from .database import get_session
from .github_source import GitHubTaskSource
from .models import DatabaseJob, ExecutionPlan, Run, RunEvent, TaskRevision
from .schemas import (
    ManualBatchRunCreate,
    ManualRunCreate,
    PlanApprove,
    PlanCreate,
    RunCreate,
    TaskRepositorySync,
    TaskRevisionCreate,
    TaskRevisionSync,
    WorkerClaim,
    WorkerComplete,
    WorkerEventCreate,
)
from .services import (
    ConflictError,
    NotFoundError,
    WorkerAuthError,
    approve_plan,
    cancel_run,
    claim_worker,
    complete_worker,
    create_manual_batch,
    create_manual_run,
    create_plan,
    create_run,
    create_worker_event,
    event_dict,
    plan_dict,
    run_dict,
    task_dict,
    upsert_task_revision,
    upsert_task_revisions,
)

SessionDep = Annotated[Session, Depends(get_session)]
AdminDep = Annotated[Principal, Depends(get_principal)]

public_router = APIRouter(tags=["health"])
admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_principal)])
worker_router = APIRouter(prefix="/api/v1/worker", tags=["worker"])


@public_router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now().astimezone().isoformat()}


@public_router.get("/health/ready")
def ready(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ready"}


@admin_router.get("/settings", tags=["settings"])
def settings_view(request: Request) -> dict[str, object]:
    return request.app.state.settings.public()


@admin_router.get("/dashboard", tags=["dashboard"])
def dashboard(session: SessionDep) -> dict:
    runs = list(session.scalars(select(Run).order_by(Run.created_at.desc()).limit(8)))
    plans = list(session.scalars(select(ExecutionPlan).order_by(ExecutionPlan.created_at.desc())))
    revisions = list(session.scalars(select(TaskRevision).order_by(TaskRevision.created_at.desc())))
    states = ("queued", "provisioning", "running", "succeeded", "failed", "timed_out", "cancelled")
    counts = {
        name: session.scalar(select(func.count()).select_from(Run).where(Run.state == name)) or 0
        for name in states
    }
    return {
        "runs": [run_dict(item) for item in runs],
        "plans": [plan_dict(item) for item in plans],
        "task_revisions": [task_dict(item) for item in revisions],
        "counts": counts,
    }


@admin_router.get("/task-revisions", tags=["tasks"])
def list_task_revisions(session: SessionDep) -> dict:
    items = session.scalars(select(TaskRevision).order_by(TaskRevision.created_at.desc()))
    return {"items": [task_dict(item) for item in items]}


@admin_router.post("/task-revisions", status_code=status.HTTP_201_CREATED, tags=["tasks"])
def create_task_revision(
    body: TaskRevisionCreate,
    session: SessionDep,
    principal: AdminDep,
) -> dict:
    return task_dict(upsert_task_revision(session, body.model_dump(), principal))


@admin_router.post("/task-revisions/sync", status_code=status.HTTP_201_CREATED, tags=["tasks"])
async def sync_task_revision(
    body: TaskRevisionSync,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict:
    settings: Settings = request.app.state.settings
    token = settings.github_token.get_secret_value() if settings.github_token else None
    source = GitHubTaskSource(token, git_https_proxy=settings.git_https_proxy)
    try:
        values = await source.sync_revision(body.repo_url, body.ref, body.task_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task_dict(upsert_task_revision(session, values, principal))


@admin_router.post("/task-revisions/sync-repository", status_code=status.HTTP_201_CREATED, tags=["tasks"])
async def sync_task_repository(
    body: TaskRepositorySync,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    token = settings.github_token.get_secret_value() if settings.github_token else None
    source = GitHubTaskSource(token, git_https_proxy=settings.git_https_proxy)
    try:
        snapshot, values = await source.sync_repository(body.repo_url, body.ref)
        items, created, updated = upsert_task_revisions(session, values, principal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **snapshot,
        "created_count": created,
        "updated_count": updated,
        "items": [task_dict(item) for item in items],
    }


@admin_router.get("/plans", tags=["plans"])
def list_plans(session: SessionDep) -> dict:
    items = session.scalars(select(ExecutionPlan).order_by(ExecutionPlan.created_at.desc()))
    return {"items": [plan_dict(item) for item in items]}


@admin_router.post("/plans", status_code=status.HTTP_201_CREATED, tags=["plans"])
def post_plan(
    body: PlanCreate,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
) -> dict:
    try:
        item = create_plan(session, body.task_revision_id, body.config, principal, request.app.state.settings)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return plan_dict(item)


@admin_router.post("/plans/{plan_id}/approve", tags=["plans"])
def post_approve(plan_id: str, body: PlanApprove, session: SessionDep, principal: AdminDep) -> dict:
    try:
        return plan_dict(approve_plan(session, plan_id, body.lock_version, principal))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@admin_router.get("/runs", tags=["runs"])
def list_runs(session: SessionDep) -> dict:
    items = session.scalars(select(Run).order_by(Run.created_at.desc()))
    return {"items": [run_dict(item) for item in items]}


@admin_router.post("/runs", tags=["runs"])
def post_run(
    body: RunCreate,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    try:
        item, created = create_run(
            session,
            body.plan_id,
            body.timeout_minutes,
            idempotency_key or "",
            principal,
            request.app.state.settings,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = run_dict(item)
    response["created"] = created
    return response


@admin_router.post("/runs/manual", tags=["runs"])
def post_manual_run(
    body: ManualRunCreate,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    try:
        item, created = create_manual_run(
            session,
            body.task_revision_id,
            body.config,
            body.timeout_minutes,
            idempotency_key or "",
            principal,
            request.app.state.settings,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = run_dict(item)
    response["created"] = created
    return response


@admin_router.post("/runs/manual-batch", tags=["runs"])
def post_manual_batch(
    body: ManualBatchRunCreate,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    try:
        runs, created = create_manual_batch(
            session,
            body.task_revision_ids,
            body.config,
            body.timeout_minutes,
            idempotency_key or "",
            principal,
            request.app.state.settings,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"created_count": created, "items": [run_dict(item) for item in runs]}


@admin_router.post("/runs/{run_id}/cancel", tags=["runs"])
def post_cancel(run_id: str, session: SessionDep, principal: AdminDep) -> dict:
    try:
        return run_dict(cancel_run(session, run_id, principal))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@admin_router.get("/runs/{run_id}/events", tags=["runs"])
def get_events(run_id: str, session: SessionDep) -> dict:
    if session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    items = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at))
    return {"items": [event_dict(item) for item in items]}


@admin_router.get("/jobs", tags=["operations"])
def list_jobs(session: SessionDep) -> dict:
    items = session.scalars(select(DatabaseJob).order_by(DatabaseJob.created_at.desc()).limit(100))
    return {
        "items": [
            {
                "id": item.id,
                "kind": item.kind,
                "state": item.state,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts,
                "available_at": item.available_at,
                "lease_owner": item.lease_owner,
                "lease_expires_at": item.lease_expires_at,
                "last_error": item.last_error,
            }
            for item in items
        ]
    }


@worker_router.post("/runs/{run_id}/claim")
def worker_claim(run_id: str, body: WorkerClaim, session: SessionDep) -> dict:
    try:
        return claim_worker(session, run_id, body.token)
    except WorkerAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@worker_router.post("/runs/{run_id}/events", status_code=status.HTTP_201_CREATED)
def worker_event(run_id: str, body: WorkerEventCreate, session: SessionDep) -> dict:
    try:
        item = create_worker_event(
            session, run_id, body.session_token, body.event_type, body.message, body.payload
        )
    except WorkerAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return event_dict(item)


@worker_router.post("/runs/{run_id}/complete")
def worker_complete(run_id: str, body: WorkerComplete, session: SessionDep) -> dict:
    try:
        return run_dict(complete_worker(session, run_id, body.session_token, body.state, body.result))
    except WorkerAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
