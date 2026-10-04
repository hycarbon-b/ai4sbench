from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.control_panel.v2.models import HarborRunV2
from src.control_panel.v2.schemas import (
    HarborRunCreate,
    HarborRunListResponse,
    HarborRunLogResponse,
    HarborRunResponse,
)
from src.control_panel.v2.service import V2ConfigurationError, enqueue_run, read_log, run_dict
from src.core.auth import get_principal
from src.db.database import get_session

router = APIRouter(
    prefix="/api/v2/control-panel", tags=["Harbor EC2 v2"], dependencies=[Depends(get_principal)]
)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/runs", response_model=HarborRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: HarborRunCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> HarborRunResponse:
    try:
        run, created = await enqueue_run(session, request.app.state.settings, body, idempotency_key)
    except V2ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        code = 409 if "Idempotency-Key" in str(exc) else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return run_dict(run)


@router.get("/runs", response_model=HarborRunListResponse)
async def list_runs(session: SessionDep) -> HarborRunListResponse:
    runs = await session.scalars(select(HarborRunV2).order_by(HarborRunV2.created_at.desc()).limit(100))
    return {"items": [run_dict(run) for run in runs]}


@router.get("/runs/{run_id}", response_model=HarborRunResponse)
async def get_run(run_id: str, session: SessionDep) -> HarborRunResponse:
    run = await session.get(HarborRunV2, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Harbor v2 run not found")
    return run_dict(run)


@router.get("/runs/{run_id}/log", response_model=HarborRunLogResponse)
async def get_run_log(run_id: str, request: Request, session: SessionDep) -> HarborRunLogResponse:
    if await session.get(HarborRunV2, run_id) is None:
        raise HTTPException(status_code=404, detail="Harbor v2 run not found")
    text, truncated = read_log(request.app.state.settings, run_id)
    return HarborRunLogResponse(text=text, truncated=truncated)
