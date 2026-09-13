"""System endpoints: connectivity status and integration event webhooks."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body

from app.adapters.fms import fms_adapter
from app.adapters.oms import oms_adapter
from app.api.deps import ActorDep, SessionDep
from app.core.config import settings
from app.schemas.entities import IntegrationStatusRead
from app.services.integration_health import integration_health
from app.services.optimisation_service import optimisation_service
from app.services.route_service import route_service

router = APIRouter(prefix="/system", tags=["system"])


@router.get(
    "/connectivity",
    response_model=list[IntegrationStatusRead],
    summary="Connectivity state of every external system",
)
async def connectivity(session: SessionDep) -> list[IntegrationStatusRead]:
    statuses = await integration_health.list_statuses(session)
    return [IntegrationStatusRead.model_validate(s) for s in statuses]


@router.get("/config", summary="Which adapters are live vs. local mocks")
async def adapter_config() -> dict[str, Any]:
    return {
        "oms": settings.oms_adapter,
        "fms": settings.fms_adapter,
        "mapping": settings.mapping_adapter,
        "delivery_platform": settings.delivery_platform_adapter,
        "environment": settings.environment,
    }


@router.get("/summary", summary="Counts used by the dispatcher header")
async def summary(session: SessionDep) -> dict[str, Any]:
    return {
        "unassigned_orders": await route_service.count_unassigned_orders(session),
        "active_run": bool(await optimisation_service.active_run(session)),
    }


@router.post(
    "/integrations/oms/events",
    summary="Ingest OMS order events (webhook / mock queue drain)",
)
async def ingest_oms(
    session: SessionDep,
    actor: ActorDep,
    events_batch: Annotated[list[dict[str, Any]], Body(embed=False)],
) -> dict[str, int]:
    stats = await oms_adapter.process_batch(session, events_batch, acting_user=actor)
    await optimisation_service.notify_pending_orders(session)
    await session.commit()
    return stats


@router.post(
    "/integrations/fms/events",
    summary="Ingest FMS vehicle events (webhook / mock queue drain)",
)
async def ingest_fms(
    session: SessionDep,
    actor: ActorDep,
    events_batch: Annotated[list[dict[str, Any]], Body(embed=False)],
) -> dict[str, int]:
    stats = await fms_adapter.process_batch(session, events_batch, acting_user=actor)
    await session.commit()
    return stats
