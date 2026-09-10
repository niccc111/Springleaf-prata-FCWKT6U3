"""Route endpoints — listing, detail, lock/unlock, status, reassignment, export."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DispatcherOrAdmin, SessionDep
from app.core.errors import ValidationFailed
from app.models.enums import RouteStatus
from app.schemas.entities import (
    ExportJobRead,
    ReassignRequest,
    ReassignResponse,
    RoutePatch,
    RouteRead,
)
from app.services.export_service import export_service
from app.services.route_service import route_service

router = APIRouter(tags=["routes"])


@router.get("/routes", response_model=list[RouteRead], summary="List routes")
async def list_routes(
    session: SessionDep,
    user: DispatcherOrAdmin,
    status_filter: Annotated[RouteStatus | None, Query(alias="status")] = None,
    vehicle_id: Annotated[uuid.UUID | None, Query()] = None,
    include_completed: Annotated[bool, Query()] = True,
) -> list[RouteRead]:
    routes = await route_service.list_routes(
        session,
        status=status_filter,
        vehicle_id=vehicle_id,
        include_completed=include_completed,
    )
    return [route_service.serialise(route) for route in routes]


@router.get("/routes/{route_id}", response_model=RouteRead, summary="Get a route with stops")
async def get_route(
    route_id: uuid.UUID, session: SessionDep, user: DispatcherOrAdmin
) -> RouteRead:
    return route_service.serialise(await route_service.get_route(session, route_id))


@router.patch(
    "/routes/{route_id}",
    response_model=RouteRead,
    summary="Lock/unlock a route or change its status",
)
async def patch_route(
    route_id: uuid.UUID,
    payload: RoutePatch,
    session: SessionDep,
    user: DispatcherOrAdmin,
) -> RouteRead:
    if payload.locked is None and payload.status is None:
        raise ValidationFailed("Provide 'locked' and/or 'status' to update the route")

    route = await route_service.get_route(session, route_id)
    if payload.status is not None:
        route = await route_service.update_route_status(
            session, route_id, payload.status, user.user_id
        )
    if payload.locked is not None:
        route = (
            await route_service.lock_route(session, route_id, user.user_id)
            if payload.locked
            else await route_service.unlock_route(session, route_id, user.user_id)
        )
    await session.commit()

    route = await route_service.get_route(session, route_id)
    await route_service.broadcast_route_updated(route, "patched")

    # Requirement 13.1 — approving a route exports it to the Delivery Platform.
    if payload.status is RouteStatus.APPROVED:
        await _export_in_background(route_id, user.user_id)
        route = await route_service.get_route(session, route_id)
    return route_service.serialise(route)


@router.post(
    "/routes/{route_id}/export",
    response_model=ExportJobRead,
    summary="Re-trigger the Delivery Platform export (Requirement 13.5)",
)
async def export_route(
    route_id: uuid.UUID, session: SessionDep, user: DispatcherOrAdmin
) -> ExportJobRead:
    job = await export_service.export_route_now(session, route_id, user.user_id)
    await session.commit()
    return ExportJobRead.model_validate(job)


@router.get(
    "/routes/{route_id}/exports",
    response_model=list[ExportJobRead],
    summary="Export attempt history for a route",
)
async def list_exports(
    route_id: uuid.UUID, session: SessionDep, user: DispatcherOrAdmin
) -> list[ExportJobRead]:
    jobs = await export_service.list_jobs(session, route_id)
    return [ExportJobRead.model_validate(job) for job in jobs]


@router.post(
    "/routes/{route_id}/recalculate",
    response_model=RouteRead,
    summary="Recalculate ETAs from current traffic (Requirement 7.2)",
)
async def recalculate(
    route_id: uuid.UUID, session: SessionDep, user: DispatcherOrAdmin
) -> RouteRead:
    route = await route_service.recalculate_etas(session, route_id, acting_user=user.user_id)
    await session.commit()
    route = await route_service.get_route(session, route_id)
    await route_service.broadcast_route_updated(route, "etas_recalculated")
    return route_service.serialise(route)


@router.post(
    "/orders/reassign",
    response_model=ReassignResponse,
    summary="Manually reassign an order between routes (Requirement 10.1)",
)
async def reassign(
    payload: ReassignRequest, session: SessionDep, user: DispatcherOrAdmin
) -> ReassignResponse:
    source, dest, late_ids = await route_service.reassign_order(
        session,
        order_id=payload.order_id,
        dest_route_id=payload.dest_route_id,
        source_route_id=payload.source_route_id,
        acting_user=user.user_id,
        position=payload.position,
        confirm_late_delivery=payload.confirm_late_delivery,
    )
    await session.commit()

    dest = await route_service.get_route(session, dest.route_id)
    await route_service.broadcast_route_updated(dest, "order_reassigned")
    source_read = None
    if source is not None:
        source = await route_service.get_route(session, source.route_id)
        await route_service.broadcast_route_updated(source, "order_reassigned")
        source_read = route_service.serialise(source)

    message = "Order reassigned."
    if late_ids:
        message = (
            f"Order reassigned. {len(late_ids)} order(s) on the destination route now fall "
            "outside their delivery window — late delivery alerts have been raised."
        )
    return ReassignResponse(
        source_route=source_read,
        dest_route=route_service.serialise(dest),
        late_stops=late_ids,
        message=message,
    )


async def _export_in_background(route_id: uuid.UUID, acting_user: uuid.UUID) -> None:
    """Run the export (with its retry sequence) on its own session."""
    import asyncio
    import contextlib

    from app.core.logging import get_logger
    from app.db.session import session_scope

    logger = get_logger(__name__)

    async def runner() -> None:
        try:
            async with session_scope() as session:
                await export_service.export_route_now(session, route_id, acting_user)
        except Exception as exc:  # pragma: no cover - background best effort
            logger.error("background_export_failed", route_id=str(route_id), error=str(exc))

    task = asyncio.create_task(runner())
    # Give a fast (mock/local) platform the chance to acknowledge synchronously
    # so the UI shows "dispatched" immediately; slow platforms continue in the
    # background and the WebSocket carries the final state.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
