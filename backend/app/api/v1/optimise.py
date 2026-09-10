"""Optimisation endpoints — Task 15.3, Requirements 5.1, 5.9, 12.1, 12.7, 18.6."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DispatcherOrAdmin, SessionDep
from app.core import events
from app.core.errors import NotFound, OperationTimeout
from app.core.logging import get_logger
from app.models.entities import OptimisationRun
from app.schemas.entities import OptimisationRunRead, OptimiseRequest
from app.services.optimisation_service import optimisation_service
from app.services.route_service import route_service

logger = get_logger(__name__)
router = APIRouter(tags=["optimisation"])


@router.post(
    "/optimise",
    response_model=OptimisationRunRead,
    status_code=status.HTTP_200_OK,
    summary="Run (or re-run) route optimisation",
)
async def optimise(
    payload: OptimiseRequest, session: SessionDep, user: DispatcherOrAdmin
) -> OptimisationRunRead:
    run = await optimisation_service.start_run(
        session, acting_user=user.user_id, reoptimise=payload.reoptimise
    )
    await session.commit()
    run_id = run.run_id

    try:
        await optimisation_service.execute_run(
            session, run, acting_user=user.user_id, reoptimise=payload.reoptimise
        )
        await session.commit()
    except TimeoutError as exc:
        refreshed = await _reload(session, run_id)
        raise OperationTimeout(
            refreshed.error_message or "The optimisation run timed out and was aborted.",
            details={"run_id": str(run_id)},
        ) from exc

    run = await _reload(session, run_id)
    routes = await route_service.list_routes(
        session, run_id=run_id, include_completed=False
    )
    await events.publish(
        events.OPTIMISATION_COMPLETE,
        {
            "run_id": str(run.run_id),
            "route_ids": [str(r.route_id) for r in routes],
            "locked_excluded_count": run.locked_excluded_count or 0,
            "orders_assigned": run.orders_assigned or 0,
            "orders_unassigned": run.orders_unassigned or 0,
            "diff": run.diff,
        },
    )
    return OptimisationRunRead.model_validate(run)


@router.get(
    "/optimise/current",
    response_model=OptimisationRunRead | None,
    summary="The in-progress run, if any",
)
async def current_run(
    session: SessionDep, user: DispatcherOrAdmin
) -> OptimisationRunRead | None:
    run = await optimisation_service.active_run(session)
    return OptimisationRunRead.model_validate(run) if run else None


@router.get(
    "/optimise/runs",
    response_model=list[OptimisationRunRead],
    summary="Recent optimisation runs",
)
async def list_runs(session: SessionDep, user: DispatcherOrAdmin) -> list[OptimisationRunRead]:
    result = await session.execute(
        select(OptimisationRun).order_by(OptimisationRun.started_at.desc()).limit(25)
    )
    return [OptimisationRunRead.model_validate(r) for r in result.scalars().all()]


@router.get(
    "/optimise/runs/{run_id}",
    response_model=OptimisationRunRead,
    summary="A single optimisation run",
)
async def get_run(
    run_id: uuid.UUID, session: SessionDep, user: DispatcherOrAdmin
) -> OptimisationRunRead:
    return OptimisationRunRead.model_validate(await _reload(session, run_id))


async def _reload(session, run_id: uuid.UUID) -> OptimisationRun:
    session.expire_all()
    run = await session.get(OptimisationRun, run_id)
    if run is None:
        raise NotFound(f"Optimisation run {run_id} not found")
    return run
