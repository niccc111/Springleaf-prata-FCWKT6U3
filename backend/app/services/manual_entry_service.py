"""Manual Order and Vehicle entry and editing.

Tasks 10.1, 10.2 — Requirements 3.1–3.6, 15.1, 15.5 (Properties 7, 8, 31).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, PersistenceFailure, ValidationFailed
from app.core.logging import get_logger
from app.models.entities import Order, Route, Stop, StopOrder, Vehicle
from app.models.enums import (
    EntityType,
    OrderSource,
    OrderStatus,
    RouteStatus,
    VehicleSource,
)
from app.schemas.entities import OrderCreate, OrderUpdate, VehicleCreate, VehicleUpdate
from app.schemas.geo import CoordinateValidationError, GeoPoint, validate_manual_coordinates
from app.services.audit_service import audit_service
from app.services.geocoding_service import geocoding_service

logger = get_logger(__name__)


class ManualEntryService:
    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    async def create_order(
        self,
        session: AsyncSession,
        data: OrderCreate,
        acting_user: uuid.UUID,
        *,
        source: OrderSource = OrderSource.MANUAL,
        external_ref: str | None = None,
    ) -> Order:
        order = Order(
            order_id=uuid.uuid4(),
            source=source.value,
            delivery_address=data.delivery_address.strip(),
            delivery_location=data.delivery_location,
            pickup_location=data.pickup_location,
            cargo_weight_kg=Decimal(str(data.cargo_weight_kg)),
            cargo_volume_m3=(
                Decimal(str(data.cargo_volume_m3)) if data.cargo_volume_m3 is not None else None
            ),
            time_window_start=data.time_window_start,
            time_window_end=data.time_window_end,
            priority=data.priority.value,
            status=OrderStatus.UNASSIGNED.value,
            service_duration_min=data.service_duration_min,
            external_ref=external_ref,
        )
        try:
            session.add(order)
            await session.flush()
        except SQLAlchemyError as exc:  # Requirement 3.5
            logger.error("order_persist_failed", error=str(exc))
            raise PersistenceFailure(
                "The order could not be saved. Your entries have been preserved — try again."
            ) from exc

        # Requirement 15.1 — geocode when the dispatcher did not supply coordinates.
        if order.delivery_location is None:
            await geocoding_service.geocode_order(session, order, acting_user=acting_user)

        await audit_service.record_change(
            session,
            entity_id=order.order_id,
            entity_type=EntityType.ORDER,
            action="order.created",
            acting_user=acting_user,
            old_state=None,
            new_state=order.to_dict(),
        )
        return order

    async def update_order(
        self,
        session: AsyncSession,
        order_id: uuid.UUID,
        data: OrderUpdate,
        acting_user: uuid.UUID,
    ) -> Order:
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFound(f"Order {order_id} not found")
        await self._assert_editable(session, order_id)

        old_state = order.to_dict()
        payload = data.model_dump(exclude_unset=True)
        address_changed = False

        for field, value in payload.items():
            if field == "delivery_address" and value is not None:
                address_changed = value.strip() != order.delivery_address
                order.delivery_address = value.strip()
            elif field in ("cargo_weight_kg", "cargo_volume_m3") and value is not None:
                setattr(order, field, Decimal(str(value)))
            elif field == "priority" and value is not None:
                order.priority = value.value if hasattr(value, "value") else str(value)
            elif value is not None or field in ("time_window_start", "time_window_end"):
                setattr(order, field, value)

        if (
            order.time_window_start
            and order.time_window_end
            and order.time_window_start > order.time_window_end
        ):
            raise ValidationFailed(
                "Time window is invalid",
                fields=[
                    {
                        "field": "time_window_end",
                        "message": "Must be at or after time_window_start",
                    }
                ],
            )

        if address_changed and payload.get("delivery_location") is None:
            await geocoding_service.geocode_order(session, order, acting_user=acting_user)

        order.updated_at = datetime.now(UTC)
        await session.flush()
        await audit_service.record_change(
            session,
            entity_id=order.order_id,
            entity_type=EntityType.ORDER,
            action="order.updated",
            acting_user=acting_user,
            old_state=old_state,
            new_state=order.to_dict(),
        )
        return order

    async def set_order_coordinates(
        self,
        session: AsyncSession,
        order_id: uuid.UUID,
        latitude: float,
        longitude: float,
        acting_user: uuid.UUID,
    ) -> Order:
        """Requirement 15.5 / Property 31 — dispatcher-supplied coordinates."""
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFound(f"Order {order_id} not found")
        try:
            point: GeoPoint = validate_manual_coordinates(latitude, longitude)
        except CoordinateValidationError as exc:
            raise ValidationFailed("Coordinates are out of range", fields=exc.errors) from exc

        old_state = order.to_dict()
        order.delivery_location = point
        order.geocode_review = False
        order.geocode_confidence = 1.0
        order.updated_at = datetime.now(UTC)
        await geocoding_service.resolve_queue_entry(session, order.order_id)
        await session.flush()
        await audit_service.record_change(
            session,
            entity_id=order.order_id,
            entity_type=EntityType.ORDER,
            action="order.coordinates_set",
            acting_user=acting_user,
            old_state=old_state,
            new_state=order.to_dict(),
        )
        return order

    async def delete_order(
        self,
        session: AsyncSession,
        order_id: uuid.UUID,
        acting_user: uuid.UUID,
    ) -> None:
        """Delete an order that is not committed to a dispatched/completed route.

        The same guard as editing applies (Requirement 3.6): once a route has
        been dispatched to a driver the orders on it are frozen. Any planning
        stop-links on non-frozen routes are removed first so the foreign key on
        ``stop_orders`` is satisfied; the geocoding-queue row cascades away.
        """
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFound(f"Order {order_id} not found")
        await self._assert_editable(session, order_id)

        old_state = order.to_dict()
        # Detach the order from any planning stops (routes that are still
        # editable). This leaves the stop in place; re-optimisation will tidy up.
        links = await session.scalars(
            select(StopOrder).where(StopOrder.order_id == order_id)
        )
        for link in links:
            await session.delete(link)
        await session.flush()

        try:
            await session.delete(order)
            await session.flush()
        except SQLAlchemyError as exc:
            logger.error("order_delete_failed", order_id=str(order_id), error=str(exc))
            raise PersistenceFailure(
                "The order could not be deleted. Nothing was changed — try again."
            ) from exc

        await audit_service.record_change(
            session,
            entity_id=order_id,
            entity_type=EntityType.ORDER,
            action="order.deleted",
            acting_user=acting_user,
            old_state=old_state,
            new_state=None,
        )

    async def _assert_editable(self, session: AsyncSession, order_id: uuid.UUID) -> None:
        """Requirement 3.6 — editing is blocked once the route is dispatched."""
        status = await session.scalar(
            select(Route.status)
            .join(Stop, Stop.route_id == Route.route_id)
            .join(StopOrder, StopOrder.stop_id == Stop.stop_id)
            .where(StopOrder.order_id == order_id)
            .where(Route.status.in_([RouteStatus.DISPATCHED.value, RouteStatus.COMPLETED.value]))
            .limit(1)
        )
        if status is not None:
            raise ValidationFailed(
                f"This order is on a route that is already {status} and can no longer be edited."
            )

    async def _assert_vehicle_editable(
        self, session: AsyncSession, vehicle_id: uuid.UUID
    ) -> None:
        """Requirement 3.6 — the same rule applies to vehicles.

        A dispatched route has already been communicated to its driver, so the
        vehicle it was planned against is frozen alongside it.
        """
        status = await session.scalar(
            select(Route.status)
            .where(Route.vehicle_id == vehicle_id)
            .where(Route.status.in_([RouteStatus.DISPATCHED.value, RouteStatus.COMPLETED.value]))
            .limit(1)
        )
        if status is not None:
            raise ValidationFailed(
                f"This vehicle is assigned to a route that is already {status} "
                "and can no longer be edited."
            )

    # ------------------------------------------------------------------ #
    # Vehicles
    # ------------------------------------------------------------------ #
    async def create_vehicle(
        self,
        session: AsyncSession,
        data: VehicleCreate,
        acting_user: uuid.UUID,
        *,
        source: VehicleSource = VehicleSource.MANUAL,
        external_ref: str | None = None,
    ) -> Vehicle:
        vehicle = Vehicle(
            vehicle_id=uuid.uuid4(),
            source=source.value,
            registration=data.registration.strip(),
            capacity_weight_kg=Decimal(str(data.capacity_weight_kg)),
            capacity_volume_m3=(
                Decimal(str(data.capacity_volume_m3))
                if data.capacity_volume_m3 is not None
                else None
            ),
            depot_location=data.depot_location,
            operating_hours_start=data.operating_hours_start,
            operating_hours_end=data.operating_hours_end,
            available=data.available,
            driver_id=data.driver_id,
            driver_name=data.driver_name,
            external_ref=external_ref,
        )
        try:
            session.add(vehicle)
            await session.flush()
        except SQLAlchemyError as exc:
            logger.error("vehicle_persist_failed", error=str(exc))
            raise PersistenceFailure(
                "The vehicle could not be saved. Your entries have been preserved — try again."
            ) from exc

        await audit_service.record_change(
            session,
            entity_id=vehicle.vehicle_id,
            entity_type=EntityType.VEHICLE,
            action="vehicle.created",
            acting_user=acting_user,
            old_state=None,
            new_state=vehicle.to_dict(),
        )
        return vehicle

    async def update_vehicle(
        self,
        session: AsyncSession,
        vehicle_id: uuid.UUID,
        data: VehicleUpdate,
        acting_user: uuid.UUID,
    ) -> Vehicle:
        vehicle = await session.get(Vehicle, vehicle_id)
        if vehicle is None:
            raise NotFound(f"Vehicle {vehicle_id} not found")
        await self._assert_vehicle_editable(session, vehicle_id)

        old_state = vehicle.to_dict()
        payload = data.model_dump(exclude_unset=True)
        for field, value in payload.items():
            if value is None and field not in ("capacity_volume_m3", "driver_id", "driver_name"):
                continue
            if field in ("capacity_weight_kg", "capacity_volume_m3") and value is not None:
                setattr(vehicle, field, Decimal(str(value)))
            elif field == "registration" and value is not None:
                vehicle.registration = value.strip()
            else:
                setattr(vehicle, field, value)

        if vehicle.operating_hours_start >= vehicle.operating_hours_end:
            raise ValidationFailed(
                "Operating hours are invalid",
                fields=[
                    {
                        "field": "operating_hours_end",
                        "message": "Must be later than operating_hours_start",
                    }
                ],
            )

        vehicle.updated_at = datetime.now(UTC)
        await session.flush()
        await audit_service.record_change(
            session,
            entity_id=vehicle.vehicle_id,
            entity_type=EntityType.VEHICLE,
            action="vehicle.updated",
            acting_user=acting_user,
            old_state=old_state,
            new_state=vehicle.to_dict(),
        )
        return vehicle


manual_entry_service = ManualEntryService()
