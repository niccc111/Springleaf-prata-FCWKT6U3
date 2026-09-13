"""Order endpoints — manual entry, listing, editing, coordinate override."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import and_, func, select

from app.api.deps import ActorDep, SessionDep
from app.core.errors import NotFound
from app.models.entities import Order, Stop, StopOrder
from app.models.enums import OrderStatus, Priority
from app.schemas.common import Page
from app.schemas.entities import (
    OrderCoordinateOverride,
    OrderCreate,
    OrderRead,
    OrderUpdate,
)
from app.services.manual_entry_service import manual_entry_service
from app.services.optimisation_service import optimisation_service

router = APIRouter(prefix="/orders", tags=["orders"])


async def _route_ids_for(session, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not order_ids:
        return {}
    rows = await session.execute(
        select(StopOrder.order_id, Stop.route_id)
        .join(Stop, Stop.stop_id == StopOrder.stop_id)
        .where(StopOrder.order_id.in_(order_ids))
    )
    return dict(rows.all())


def _read(order: Order, route_id: uuid.UUID | None) -> OrderRead:
    model = OrderRead.model_validate(order)
    model.route_id = route_id
    return model


@router.get("", response_model=Page[OrderRead], summary="List orders")
async def list_orders(
    session: SessionDep,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    priority: Annotated[Priority | None, Query()] = None,
    needs_review: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[OrderRead]:
    conditions = []
    if status_filter is not None:
        conditions.append(Order.status == status_filter.value)
    if priority is not None:
        conditions.append(Order.priority == priority.value)
    if needs_review is not None:
        conditions.append(Order.geocode_review.is_(needs_review))
    if search:
        conditions.append(Order.delivery_address.ilike(f"%{search}%"))

    base = select(Order)
    count_stmt = select(func.count()).select_from(Order)
    if conditions:
        base = base.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    total = (await session.scalar(count_stmt)) or 0
    result = await session.execute(
        base.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    )
    orders = list(result.scalars().all())
    route_ids = await _route_ids_for(session, [o.order_id for o in orders])
    return Page[OrderRead](
        items=[_read(o, route_ids.get(o.order_id)) for o in orders],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an order manually (Requirement 3.1)",
)
async def create_order(payload: OrderCreate, session: SessionDep, actor: ActorDep) -> OrderRead:
    order = await manual_entry_service.create_order(session, payload, actor)
    # Requirement 6.2/6.3 — flag overlapping routes for re-optimisation.
    if order.priority == Priority.PRIORITY.value:
        await optimisation_service.flag_routes_for_priority_order(session, order, acting_user=actor)
    await optimisation_service.notify_pending_orders(session)
    await session.commit()
    return _read(order, None)


@router.get("/{order_id}", response_model=OrderRead, summary="Get an order")
async def get_order(order_id: uuid.UUID, session: SessionDep) -> OrderRead:
    order = await session.get(Order, order_id)
    if order is None:
        raise NotFound(f"Order {order_id} not found")
    route_ids = await _route_ids_for(session, [order_id])
    return _read(order, route_ids.get(order_id))


@router.patch("/{order_id}", response_model=OrderRead, summary="Edit an order (Requirement 3.6)")
async def update_order(
    order_id: uuid.UUID,
    payload: OrderUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> OrderRead:
    order = await manual_entry_service.update_order(session, order_id, payload, actor)
    if order.priority == Priority.PRIORITY.value:
        await optimisation_service.flag_routes_for_priority_order(session, order, acting_user=actor)
    await session.commit()
    route_ids = await _route_ids_for(session, [order_id])
    return _read(order, route_ids.get(order_id))


@router.post(
    "/{order_id}/coordinates",
    response_model=OrderRead,
    summary="Supply coordinates manually (Requirement 15.5)",
)
async def set_coordinates(
    order_id: uuid.UUID,
    payload: OrderCoordinateOverride,
    session: SessionDep,
    actor: ActorDep,
) -> OrderRead:
    order = await manual_entry_service.set_order_coordinates(
        session, order_id, payload.latitude, payload.longitude, actor
    )
    await session.commit()
    return _read(order, None)
