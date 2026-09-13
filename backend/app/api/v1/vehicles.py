"""Vehicle endpoints — manual entry, listing, editing."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import and_, func, select

from app.api.deps import ActorDep, SessionDep
from app.core.errors import NotFound
from app.models.entities import Vehicle
from app.schemas.common import Page
from app.schemas.entities import VehicleCreate, VehicleRead, VehicleUpdate
from app.services.manual_entry_service import manual_entry_service

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("", response_model=Page[VehicleRead], summary="List vehicles")
async def list_vehicles(
    session: SessionDep,
    available: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[VehicleRead]:
    conditions = []
    if available is not None:
        conditions.append(Vehicle.available.is_(available))
    if search:
        conditions.append(Vehicle.registration.ilike(f"%{search}%"))

    base = select(Vehicle)
    count_stmt = select(func.count()).select_from(Vehicle)
    if conditions:
        base = base.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    total = (await session.scalar(count_stmt)) or 0
    result = await session.execute(base.order_by(Vehicle.registration).limit(limit).offset(offset))
    return Page[VehicleRead](
        items=[VehicleRead.model_validate(v) for v in result.scalars().all()],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=VehicleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a vehicle manually (Requirement 3.2)",
)
async def create_vehicle(
    payload: VehicleCreate, session: SessionDep, actor: ActorDep
) -> VehicleRead:
    vehicle = await manual_entry_service.create_vehicle(session, payload, actor)
    await session.commit()
    return VehicleRead.model_validate(vehicle)


@router.get("/{vehicle_id}", response_model=VehicleRead, summary="Get a vehicle")
async def get_vehicle(vehicle_id: uuid.UUID, session: SessionDep) -> VehicleRead:
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise NotFound(f"Vehicle {vehicle_id} not found")
    return VehicleRead.model_validate(vehicle)


@router.patch("/{vehicle_id}", response_model=VehicleRead, summary="Edit a vehicle")
async def update_vehicle(
    vehicle_id: uuid.UUID,
    payload: VehicleUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> VehicleRead:
    vehicle = await manual_entry_service.update_vehicle(session, vehicle_id, payload, actor)
    await session.commit()
    return VehicleRead.model_validate(vehicle)
