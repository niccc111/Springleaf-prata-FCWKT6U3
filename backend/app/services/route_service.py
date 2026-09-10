"""Route lifecycle: status machine, locking, reassignment, ETA recalculation.

Tasks 5.1–5.10 — Requirements 7.2, 7.3, 8.4, 9.x, 10.x, 11.x, 13.2, 16.1.
Properties 16, 17, 18, 19, 20, 21, 22, 23, 24, 26.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import TravelMatrix
from app.core import events
from app.core.config import settings
from app.core.errors import (
    CapacityViolation,
    ConfirmationRequired,
    InvalidStateTransition,
    NotFound,
    OperationTimeout,
    RouteLockedError,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.models.entities import Order, Route, Stop, StopOrder, Vehicle
from app.models.enums import (
    AlertEntityType,
    AlertSeverity,
    AlertType,
    EntityType,
    OrderStatus,
    RouteStatus,
)
from app.schemas.entities import RouteRead, StopOrderSummary, StopRead
from app.services.alert_service import alert_service
from app.services.audit_service import audit_service
from app.services.geocoding_service import geocoding_service
from app.services.route_math import (
    RouteSchedule,
    StopPlan,
    as_utc,
    build_schedule,
    matrix_nodes_for_route,
    planning_day_for,
    q3,
    shift_start,
    utilisation_pct,
)

logger = get_logger(__name__)

#: Allowed forward transitions of the route status state machine.
ALLOWED_TRANSITIONS: dict[RouteStatus, set[RouteStatus]] = {
    RouteStatus.DRAFT: {RouteStatus.DRAFT, RouteStatus.APPROVED},
    RouteStatus.APPROVED: {RouteStatus.APPROVED, RouteStatus.DISPATCHED, RouteStatus.DRAFT},
    RouteStatus.DISPATCHED: {RouteStatus.DISPATCHED, RouteStatus.COMPLETED},
    RouteStatus.COMPLETED: {RouteStatus.COMPLETED},
}

#: Sequence numbers are parked above this while a route is being resequenced,
#: keeping both the >= 1 CHECK and the (route_id, sequence_number) UNIQUE
#: constraint satisfied at every intermediate step.
RESEQUENCE_OFFSET = 1_000_000

LOCKABLE_STATUSES = {RouteStatus.DRAFT, RouteStatus.APPROVED}
UNLOCKABLE_STATUSES = {RouteStatus.DRAFT, RouteStatus.APPROVED}


def route_query() -> Select[tuple[Route]]:
    return select(Route).options(
        selectinload(Route.stops).selectinload(Stop.stop_orders).selectinload(StopOrder.order),
        selectinload(Route.vehicle),
    )


class RouteService:
    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    async def get_route(self, session: AsyncSession, route_id: uuid.UUID) -> Route:
        route = await session.scalar(route_query().where(Route.route_id == route_id))
        if route is None:
            raise NotFound(f"Route {route_id} not found")
        return route

    async def list_routes(
        self,
        session: AsyncSession,
        *,
        status: RouteStatus | None = None,
        vehicle_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        include_completed: bool = True,
    ) -> list[Route]:
        stmt = route_query()
        conditions = []
        if status is not None:
            conditions.append(Route.status == status.value)
        if vehicle_id is not None:
            conditions.append(Route.vehicle_id == vehicle_id)
        if run_id is not None:
            conditions.append(Route.optimisation_run_id == run_id)
        if not include_completed:
            conditions.append(Route.status != RouteStatus.COMPLETED.value)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        result = await session.execute(stmt.order_by(Route.created_at.desc()))
        return list(result.scalars().unique().all())

    # ------------------------------------------------------------------ #
    # Serialisation (Property 18 — displayed metrics match stored values)
    # ------------------------------------------------------------------ #
    def serialise(self, route: Route) -> RouteRead:
        vehicle = route.vehicle
        stops: list[StopRead] = []
        for stop in sorted(route.stops, key=lambda s: s.sequence_number):
            orders = [link.order for link in stop.stop_orders if link.order is not None]
            stop_weight = sum((o.cargo_weight_kg or Decimal("0")) for o in orders)
            volumes = [o.cargo_volume_m3 for o in orders if o.cargo_volume_m3 is not None]
            stops.append(
                StopRead(
                    stop_id=stop.stop_id,
                    route_id=stop.route_id,
                    order_ids=[link.order_id for link in stop.stop_orders],
                    orders=[
                        StopOrderSummary(
                            order_id=o.order_id,
                            delivery_address=o.delivery_address,
                            cargo_weight_kg=float(o.cargo_weight_kg),
                            cargo_volume_m3=(
                                float(o.cargo_volume_m3) if o.cargo_volume_m3 is not None else None
                            ),
                            priority=o.priority,
                            time_window_start=o.time_window_start,
                            time_window_end=o.time_window_end,
                            external_ref=o.external_ref,
                        )
                        for o in orders
                    ],
                    location=stop.location,
                    address=stop.address,
                    sequence_number=stop.sequence_number,
                    eta=stop.eta,
                    departure=stop.departure,
                    time_window_start=stop.time_window_start,
                    time_window_end=stop.time_window_end,
                    service_duration_min=stop.service_duration_min,
                    distance_from_previous_km=float(stop.distance_from_previous_km),
                    travel_time_from_previous_min=stop.travel_time_from_previous_min,
                    has_priority_order=stop.has_priority_order,
                    total_weight_kg=float(stop_weight),
                    total_volume_m3=float(sum(volumes)) if volumes else None,
                    late=(
                        stop.time_window_end is not None
                        and as_utc(stop.eta) > as_utc(stop.time_window_end)
                    ),
                )
            )

        return RouteRead(
            route_id=route.route_id,
            vehicle_id=route.vehicle_id,
            optimisation_run_id=route.optimisation_run_id,
            driver_id=route.driver_id,
            vehicle_registration=vehicle.registration if vehicle else None,
            vehicle_capacity_weight_kg=(
                float(vehicle.capacity_weight_kg) if vehicle else None
            ),
            vehicle_capacity_volume_m3=(
                float(vehicle.capacity_volume_m3)
                if vehicle and vehicle.capacity_volume_m3 is not None
                else None
            ),
            depot_location=vehicle.depot_location if vehicle else None,
            total_distance_km=float(route.total_distance_km),
            total_duration_min=route.total_duration_min,
            total_weight_kg=float(route.total_weight_kg),
            total_volume_m3=(
                float(route.total_volume_m3) if route.total_volume_m3 is not None else None
            ),
            weight_utilisation_pct=utilisation_pct(
                route.total_weight_kg, vehicle.capacity_weight_kg if vehicle else None
            ),
            volume_utilisation_pct=utilisation_pct(
                route.total_volume_m3,
                vehicle.capacity_volume_m3 if vehicle else None,
            ),
            stop_count=len(route.stops),
            locked=route.locked,
            status=RouteStatus(route.status),
            data_quality_warning=route.data_quality_warning,
            data_quality_message=route.data_quality_message,
            needs_reoptimisation=route.needs_reoptimisation,
            priority_relaxed=route.priority_relaxed,
            travel_times_updated_at=route.travel_times_updated_at,
            completed_at=route.completed_at,
            created_at=route.created_at,
            updated_at=route.updated_at,
            stops=stops,
        )

    async def broadcast_route_updated(self, route: Route, change: str) -> None:
        await events.publish(
            events.ROUTE_UPDATED,
            {
                "route_id": str(route.route_id),
                "changes": change,
                "route": self.serialise(route).model_dump(mode="json"),
            },
        )

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    async def create_route(
        self,
        session: AsyncSession,
        *,
        vehicle: Vehicle,
        run_id: uuid.UUID,
        schedule: RouteSchedule,
        acting_user: uuid.UUID | None,
        data_quality_warning: bool = False,
        data_quality_message: str | None = None,
        priority_relaxed: bool = False,
    ) -> Route:
        route = Route(
            route_id=uuid.uuid4(),
            vehicle_id=vehicle.vehicle_id,
            optimisation_run_id=run_id,
            driver_id=vehicle.driver_id,
            total_distance_km=schedule.total_distance_km,
            total_duration_min=schedule.total_duration_min,
            total_weight_kg=schedule.total_weight_kg,
            total_volume_m3=schedule.total_volume_m3,
            locked=False,
            status=RouteStatus.DRAFT.value,
            data_quality_warning=data_quality_warning,
            data_quality_message=data_quality_message,
            priority_relaxed=priority_relaxed,
        )
        # Attach the vehicle object, not just its id, for the same reason the
        # stops are linked below: the caller can read route.vehicle off the
        # returned route without provoking a lazy load.
        route.vehicle = vehicle
        session.add(route)

        for scheduled in schedule.stops:
            stop = Stop(
                stop_id=uuid.uuid4(),
                location=scheduled.plan.location,
                address=scheduled.plan.address,
                sequence_number=scheduled.sequence_number,
                eta=scheduled.eta,
                departure=scheduled.departure,
                time_window_start=scheduled.plan.time_window_start,
                time_window_end=scheduled.plan.time_window_end,
                service_duration_min=scheduled.plan.service_duration_min,
                distance_from_previous_km=scheduled.distance_from_previous_km,
                travel_time_from_previous_min=scheduled.travel_time_from_previous_min,
                has_priority_order=scheduled.plan.has_priority_order,
            )
            # Link through the relationships rather than setting the foreign keys.
            # That marks both collections loaded, so the route this returns can be
            # read without emitting a lazy load -- which on an async session is
            # synchronous IO from the wrong context and raises MissingGreenlet.
            stop.stop_orders = [
                StopOrder(order_id=order_id) for order_id in scheduled.plan.order_ids
            ]
            route.stops.append(stop)
        await session.flush()

        await audit_service.record_change(
            session,
            entity_id=route.route_id,
            entity_type=EntityType.ROUTE,
            action="route.created",
            acting_user=acting_user,
            old_state=None,
            new_state=route.to_dict(),
        )
        return route

    # ------------------------------------------------------------------ #
    # Status machine (Requirements 11.6, 13.2; Property 26)
    # ------------------------------------------------------------------ #
    async def update_route_status(
        self,
        session: AsyncSession,
        route_id: uuid.UUID,
        new_status: RouteStatus,
        acting_user: uuid.UUID | None,
    ) -> Route:
        route = await self.get_route(session, route_id)
        current = RouteStatus(route.status)
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidStateTransition(
                f"Route cannot move from '{current.value}' to '{new_status.value}'"
            )

        old_state = route.to_dict()
        route.status = new_status.value
        if new_status is RouteStatus.DISPATCHED:
            # Requirement 11.6 / Property 26 — dispatched routes are always locked.
            route.locked = True
        if new_status is RouteStatus.COMPLETED:
            route.locked = True
            route.completed_at = datetime.now(UTC)
        if new_status is RouteStatus.APPROVED:
            await self._set_order_status(session, route, OrderStatus.ASSIGNED)
        route.updated_at = datetime.now(UTC)
        await session.flush()

        await audit_service.record_change(
            session,
            entity_id=route.route_id,
            entity_type=EntityType.ROUTE,
            action=f"route.status.{new_status.value}",
            acting_user=acting_user,
            old_state=old_state,
            new_state=route.to_dict(),
        )
        return route

    async def _set_order_status(
        self, session: AsyncSession, route: Route, status: OrderStatus
    ) -> None:
        order_ids = [link.order_id for stop in route.stops for link in stop.stop_orders]
        if not order_ids:
            return
        orders = (
            await session.execute(select(Order).where(Order.order_id.in_(order_ids)))
        ).scalars()
        for order in orders:
            order.status = status.value

    # ------------------------------------------------------------------ #
    # Locking (Requirements 11.1–11.5; Property 24)
    # ------------------------------------------------------------------ #
    async def lock_route(
        self, session: AsyncSession, route_id: uuid.UUID, acting_user: uuid.UUID | None
    ) -> Route:
        route = await self.get_route(session, route_id)
        status = RouteStatus(route.status)
        if status not in LOCKABLE_STATUSES:
            raise InvalidStateTransition(
                f"Route cannot be locked while its status is '{status.value}'. "
                "Only draft or approved routes can be locked."
            )
        return await self._set_lock(session, route, True, acting_user)

    async def unlock_route(
        self, session: AsyncSession, route_id: uuid.UUID, acting_user: uuid.UUID | None
    ) -> Route:
        route = await self.get_route(session, route_id)
        status = RouteStatus(route.status)
        if status not in UNLOCKABLE_STATUSES:
            raise InvalidStateTransition(
                f"Route cannot be unlocked while its status is '{status.value}'. "
                "Dispatched and completed routes stay locked."
            )
        return await self._set_lock(session, route, False, acting_user)

    async def _set_lock(
        self, session: AsyncSession, route: Route, locked: bool, acting_user: uuid.UUID | None
    ) -> Route:
        old_state = route.to_dict()
        route.locked = locked
        route.updated_at = datetime.now(UTC)
        await session.flush()
        await audit_service.record_change(
            session,
            entity_id=route.route_id,
            entity_type=EntityType.ROUTE,
            action="route.locked" if locked else "route.unlocked",
            acting_user=acting_user,
            old_state=old_state,
            new_state=route.to_dict(),
        )
        return route

    # ------------------------------------------------------------------ #
    # Metrics, alerts
    # ------------------------------------------------------------------ #
    async def check_capacity_alerts(
        self, session: AsyncSession, route: Route, acting_user: uuid.UUID | None = None
    ) -> None:
        """Overload alert at >= 100% of capacity (Requirements 9.3, 14.1; Property 19)."""
        vehicle = route.vehicle or await session.get(Vehicle, route.vehicle_id)
        if vehicle is None:
            return
        overloaded_weight = route.total_weight_kg >= vehicle.capacity_weight_kg
        overloaded_volume = (
            vehicle.capacity_volume_m3 is not None
            and route.total_volume_m3 is not None
            and route.total_volume_m3 >= vehicle.capacity_volume_m3
        )
        if overloaded_weight or overloaded_volume:
            parts = []
            if overloaded_weight:
                parts.append(
                    f"weight {float(route.total_weight_kg):.1f} kg of "
                    f"{float(vehicle.capacity_weight_kg):.1f} kg"
                )
            if overloaded_volume:
                parts.append(
                    f"volume {float(route.total_volume_m3):.2f} m³ of "
                    f"{float(vehicle.capacity_volume_m3):.2f} m³"
                )
            await alert_service.raise_alert(
                session,
                alert_type=AlertType.OVERLOAD,
                severity=AlertSeverity.CRITICAL,
                entity_type=AlertEntityType.VEHICLE,
                entity_id=vehicle.vehicle_id,
                message=(
                    f"Vehicle {vehicle.registration} is at or over capacity on route "
                    f"{route.route_id}: " + "; ".join(parts)
                ),
                context={
                    "route_id": str(route.route_id),
                    "vehicle_id": str(vehicle.vehicle_id),
                    "total_weight_kg": float(route.total_weight_kg),
                    "capacity_weight_kg": float(vehicle.capacity_weight_kg),
                },
                acting_user=acting_user,
            )
        else:
            await alert_service.clear_alerts_for_entity(
                session,
                entity_id=vehicle.vehicle_id,
                alert_type=AlertType.OVERLOAD,
                acting_user=acting_user,
            )

    async def raise_late_delivery_alerts(
        self,
        session: AsyncSession,
        route: Route,
        late_stops: Sequence[Stop],
        acting_user: uuid.UUID | None = None,
    ) -> None:
        """Requirements 7.3, 14.2 (Property 17)."""
        for stop in late_stops:
            order_ids = [str(link.order_id) for link in stop.stop_orders]
            await alert_service.raise_alert(
                session,
                alert_type=AlertType.LATE_DELIVERY,
                severity=AlertSeverity.WARNING,
                entity_type=AlertEntityType.ORDER,
                entity_id=(
                    stop.stop_orders[0].order_id if stop.stop_orders else route.route_id
                ),
                message=(
                    f"Stop {stop.sequence_number} ({stop.address}) has ETA "
                    f"{as_utc(stop.eta).isoformat()} which is after its time window end "
                    f"{as_utc(stop.time_window_end).isoformat() if stop.time_window_end else 'n/a'}"
                ),
                context={
                    "stop_id": str(stop.stop_id),
                    "route_id": str(route.route_id),
                    "order_ids": order_ids,
                    "eta": as_utc(stop.eta).isoformat(),
                    "time_window_end": (
                        as_utc(stop.time_window_end).isoformat()
                        if stop.time_window_end
                        else None
                    ),
                },
                acting_user=acting_user,
            )

    # ------------------------------------------------------------------ #
    # Schedule recomputation
    # ------------------------------------------------------------------ #
    def _plans_from_route(self, route: Route) -> list[StopPlan]:
        plans: list[StopPlan] = []
        for stop in sorted(route.stops, key=lambda s: s.sequence_number):
            orders = [link.order for link in stop.stop_orders if link.order is not None]
            weight = sum((o.cargo_weight_kg or Decimal("0")) for o in orders) or Decimal("0")
            volumes = [o.cargo_volume_m3 for o in orders if o.cargo_volume_m3 is not None]
            window_starts = [o.time_window_start for o in orders if o.time_window_start]
            window_ends = [o.time_window_end for o in orders if o.time_window_end]
            plans.append(
                StopPlan(
                    location=stop.location,
                    address=stop.address,
                    order_ids=[link.order_id for link in stop.stop_orders],
                    weight_kg=Decimal(str(weight)),
                    volume_m3=sum(volumes) if volumes else None,
                    service_duration_min=stop.service_duration_min,
                    time_window_start=max(window_starts) if window_starts else None,
                    time_window_end=min(window_ends) if window_ends else None,
                    has_priority_order=any(o.priority == "priority" for o in orders),
                    stop_id=stop.stop_id,
                )
            )
        return plans

    async def recompute_route(
        self,
        session: AsyncSession,
        route: Route,
        *,
        matrix: TravelMatrix | None = None,
        plans: list[StopPlan] | None = None,
        acting_user: uuid.UUID | None = None,
        persist: bool = True,
    ) -> RouteSchedule:
        """Recompute the whole schedule and totals for a route from its stops."""
        vehicle = route.vehicle or await session.get(Vehicle, route.vehicle_id)
        if vehicle is None:
            raise NotFound(f"Vehicle {route.vehicle_id} not found")

        plans = plans if plans is not None else self._plans_from_route(route)
        waypoints = matrix_nodes_for_route(vehicle.depot_location, plans)
        # A route is already committed to a planning day, so recomputing it must
        # not silently move it to another one: prefer the day its current
        # schedule runs on, then the earliest delivery window among the plans
        # (a route with no stops yet), and only then the creation date.
        planning_day = planning_day_for(
            min((as_utc(stop.eta) for stop in route.stops if stop.eta), default=None)
            or min(
                (as_utc(p.time_window_start) for p in plans if p.time_window_start),
                default=None,
            )
            or route.created_at
        )
        departure = shift_start(planning_day, vehicle.operating_hours_start)
        if matrix is None:
            matrix = await geocoding_service.get_travel_time_matrix(
                session, waypoints, departure=departure
            )

        schedule = build_schedule(
            depot=vehicle.depot_location,
            departure_time=departure,
            stops=plans,
            matrix=matrix,
        )

        if persist:
            await self._persist_schedule(session, route, schedule, matrix.degraded)
        return schedule

    @staticmethod
    def _sync_stop_orders(stop: Stop, order_ids: Sequence[uuid.UUID]) -> None:
        """Make ``stop.stop_orders`` match ``order_ids`` exactly.

        Works through the relationship so the in-memory collection stays
        truthful; ``delete-orphan`` turns a removal into a DELETE on flush.
        """
        wanted = list(dict.fromkeys(order_ids))
        current = {link.order_id: link for link in stop.stop_orders}
        for order_id in wanted:
            if order_id not in current:
                stop.stop_orders.append(StopOrder(order_id=order_id))
        for order_id, link in current.items():
            if order_id not in wanted:
                stop.stop_orders.remove(link)

    async def _persist_schedule(
        self,
        session: AsyncSession,
        route: Route,
        schedule: RouteSchedule,
        degraded: bool,
    ) -> None:
        """Rewrite the route's stops to match ``schedule``."""
        existing = {stop.stop_id: stop for stop in route.stops}
        keep_ids: set[uuid.UUID] = set()

        # Two passes so the (route_id, sequence_number) unique constraint never
        # collides mid-update: park every stop above any real sequence number
        # first, then write the final values.
        for scheduled in schedule.stops:
            stop_id = scheduled.plan.stop_id
            stop = existing.get(stop_id) if stop_id else None
            if stop is None:
                stop = Stop(
                    stop_id=uuid.uuid4(),
                    location=scheduled.plan.location,
                    address=scheduled.plan.address,
                    # Park it in the offset range like every other stop below, so
                    # two new stops in one call cannot collide with each other on
                    # (route_id, sequence_number) during the insert pass.
                    sequence_number=RESEQUENCE_OFFSET + scheduled.sequence_number,
                    eta=scheduled.eta,
                    service_duration_min=scheduled.plan.service_duration_min,
                )
                # Append through the relationship so the route's in-memory stop
                # collection keeps matching what is written. The order links are
                # filled in by the reconciliation below, which covers new and
                # existing stops alike.
                route.stops.append(stop)
                scheduled.plan.stop_id = stop.stop_id
            stop.sequence_number = RESEQUENCE_OFFSET + scheduled.sequence_number
            # Reconcile the order links for *every* stop, not just new ones: a
            # reassignment into an existing co-located stop grows that stop's
            # plan, and without this the order would be counted in the route
            # totals while never appearing among its stop's orders.
            self._sync_stop_orders(stop, scheduled.plan.order_ids)
            keep_ids.add(stop.stop_id)

        for stop_id, stop in existing.items():
            if stop_id not in keep_ids:
                await session.delete(stop)
        await session.flush()

        for scheduled in schedule.stops:
            stop = await session.get(Stop, scheduled.plan.stop_id)
            if stop is None:  # pragma: no cover - defensive
                continue
            stop.sequence_number = scheduled.sequence_number
            stop.location = scheduled.plan.location
            stop.address = scheduled.plan.address
            stop.eta = scheduled.eta
            stop.departure = scheduled.departure
            stop.time_window_start = scheduled.plan.time_window_start
            stop.time_window_end = scheduled.plan.time_window_end
            stop.service_duration_min = scheduled.plan.service_duration_min
            stop.distance_from_previous_km = scheduled.distance_from_previous_km
            stop.travel_time_from_previous_min = scheduled.travel_time_from_previous_min
            stop.has_priority_order = scheduled.plan.has_priority_order

        route.total_distance_km = schedule.total_distance_km
        route.total_duration_min = schedule.total_duration_min
        route.total_weight_kg = schedule.total_weight_kg
        route.total_volume_m3 = schedule.total_volume_m3
        route.travel_times_updated_at = datetime.now(UTC)
        route.updated_at = datetime.now(UTC)
        route.data_quality_warning = degraded
        route.data_quality_message = (
            "Travel times estimated from historical averages — Mapping Service unavailable"
            if degraded
            else None
        )
        await session.flush()
        await session.refresh(route, ["stops"])

    # ------------------------------------------------------------------ #
    # ETA recalculation (Task 5.10 — Requirements 7.2, 7.3)
    # ------------------------------------------------------------------ #
    async def recalculate_etas(
        self,
        session: AsyncSession,
        route_id: uuid.UUID,
        *,
        matrix: TravelMatrix | None = None,
        acting_user: uuid.UUID | None = None,
    ) -> Route:
        route = await self.get_route(session, route_id)
        old_state = route.to_dict()
        schedule = await self.recompute_route(
            session, route, matrix=matrix, acting_user=acting_user
        )
        await session.refresh(route, ["stops"])

        late_stops = [
            stop
            for stop in route.stops
            if stop.time_window_end is not None and as_utc(stop.eta) > as_utc(stop.time_window_end)
        ]
        await self.raise_late_delivery_alerts(session, route, late_stops, acting_user)
        await self.check_capacity_alerts(session, route, acting_user)

        await audit_service.record_change(
            session,
            entity_id=route.route_id,
            entity_type=EntityType.ROUTE,
            action="route.etas_recalculated",
            acting_user=acting_user,
            old_state=old_state,
            new_state=route.to_dict(),
        )
        logger.info(
            "etas_recalculated",
            route_id=str(route.route_id),
            late_stops=len(late_stops),
            degraded=schedule is not None and route.data_quality_warning,
        )
        return route

    async def recalculate_all_active_routes(
        self, session: AsyncSession, *, acting_user: uuid.UUID | None = None
    ) -> int:
        routes = await self.list_routes(session, include_completed=False)
        count = 0
        for route in routes:
            if RouteStatus(route.status) in (RouteStatus.DRAFT, RouteStatus.APPROVED):
                await self.recalculate_etas(session, route.route_id, acting_user=acting_user)
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # Manual reassignment (Task 5.5 — Requirements 10.1–10.8)
    # ------------------------------------------------------------------ #
    async def reassign_order(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        dest_route_id: uuid.UUID,
        source_route_id: uuid.UUID | None = None,
        acting_user: uuid.UUID,
        position: int | None = None,
        confirm_late_delivery: bool = False,
    ) -> tuple[Route | None, Route, list[uuid.UUID]]:
        """Move an order onto another route.

        Rejections leave **both** routes untouched (Properties 21, 22): all
        validation runs against in-memory plans before anything is written.
        """
        try:
            return await asyncio.wait_for(
                self._reassign_order_inner(
                    session,
                    order_id=order_id,
                    dest_route_id=dest_route_id,
                    source_route_id=source_route_id,
                    acting_user=acting_user,
                    position=position,
                    confirm_late_delivery=confirm_late_delivery,
                ),
                timeout=settings.reassignment_timeout_seconds,
            )
        except TimeoutError as exc:
            # Requirement 10.7 — revert both routes by rolling the transaction back.
            await session.rollback()
            raise OperationTimeout(
                "Reassignment recalculation exceeded "
                f"{settings.reassignment_timeout_seconds:.0f}s; both routes were left unchanged."
            ) from exc

    async def _reassign_order_inner(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        dest_route_id: uuid.UUID,
        source_route_id: uuid.UUID | None,
        acting_user: uuid.UUID,
        position: int | None,
        confirm_late_delivery: bool,
    ) -> tuple[Route | None, Route, list[uuid.UUID]]:
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFound(f"Order {order_id} not found")
        if order.delivery_location is None:
            raise ValidationFailed(
                "Order has no resolved delivery location and cannot be routed",
                fields=[{"field": "delivery_location", "message": "Coordinates are required"}],
            )

        dest_route = await self.get_route(session, dest_route_id)
        # Requirement 10.6 / Property 22 — locked destination is rejected outright.
        if dest_route.locked:
            raise RouteLockedError(
                f"Destination route {dest_route_id} is locked and cannot be modified."
            )
        if RouteStatus(dest_route.status) in (RouteStatus.DISPATCHED, RouteStatus.COMPLETED):
            raise InvalidStateTransition(
                f"Destination route is {dest_route.status} and can no longer be modified."
            )

        source_route: Route | None = None
        resolved_source_id = source_route_id or await self._find_route_for_order(session, order_id)
        if resolved_source_id is not None and resolved_source_id != dest_route_id:
            source_route = await self.get_route(session, resolved_source_id)
            if source_route.locked:
                raise RouteLockedError(
                    f"Source route {source_route.route_id} is locked and cannot be modified."
                )
        elif resolved_source_id == dest_route_id:
            raise ValidationFailed("Order is already assigned to the destination route")

        dest_vehicle = dest_route.vehicle or await session.get(Vehicle, dest_route.vehicle_id)
        assert dest_vehicle is not None

        # ---- Build the prospective destination plan (no writes yet) -------
        dest_plans = self._plans_from_route(dest_route)
        merged_index = next(
            (
                i
                for i, plan in enumerate(dest_plans)
                if plan.location.latitude == order.delivery_location.latitude
                and plan.location.longitude == order.delivery_location.longitude
            ),
            None,
        )
        if merged_index is not None:
            target = dest_plans[merged_index]
            target.order_ids = [*target.order_ids, order.order_id]
            target.weight_kg += order.cargo_weight_kg
            if order.cargo_volume_m3 is not None:
                target.volume_m3 = (target.volume_m3 or Decimal("0")) + order.cargo_volume_m3
            target.time_window_start = _max_opt(target.time_window_start, order.time_window_start)
            target.time_window_end = _min_opt(target.time_window_end, order.time_window_end)
            target.has_priority_order = target.has_priority_order or order.priority == "priority"
            target.service_duration_min = max(
                target.service_duration_min, order.service_duration_min
            )
        else:
            new_plan = StopPlan(
                location=order.delivery_location,
                address=order.delivery_address,
                order_ids=[order.order_id],
                weight_kg=order.cargo_weight_kg,
                volume_m3=order.cargo_volume_m3,
                service_duration_min=order.service_duration_min,
                time_window_start=order.time_window_start,
                time_window_end=order.time_window_end,
                has_priority_order=order.priority == "priority",
            )
            insert_at = (
                min(max(position - 1, 0), len(dest_plans))
                if position is not None
                else len(dest_plans)
            )
            dest_plans.insert(insert_at, new_plan)

        # ---- Capacity checks (Requirements 10.3, 10.4; Property 21) -------
        prospective_weight = q3(sum(p.weight_kg for p in dest_plans))
        volumes = [p.volume_m3 for p in dest_plans if p.volume_m3 is not None]
        prospective_volume = q3(sum(volumes)) if volumes else None

        if prospective_weight > dest_vehicle.capacity_weight_kg:
            raise CapacityViolation(
                f"Reassignment rejected: {dest_vehicle.registration} would carry "
                f"{float(prospective_weight):.1f} kg, exceeding its "
                f"{float(dest_vehicle.capacity_weight_kg):.1f} kg capacity.",
                details={
                    "prospective_weight_kg": float(prospective_weight),
                    "capacity_weight_kg": float(dest_vehicle.capacity_weight_kg),
                },
            )
        if (
            dest_vehicle.capacity_volume_m3 is not None
            and prospective_volume is not None
            and prospective_volume > dest_vehicle.capacity_volume_m3
        ):
            raise CapacityViolation(
                f"Reassignment rejected: {dest_vehicle.registration} would carry "
                f"{float(prospective_volume):.2f} m³, exceeding its "
                f"{float(dest_vehicle.capacity_volume_m3):.2f} m³ capacity.",
                details={
                    "prospective_volume_m3": float(prospective_volume),
                    "capacity_volume_m3": float(dest_vehicle.capacity_volume_m3),
                },
            )

        # ---- Time-window check requires confirmation (Requirement 10.5) ---
        preview = await self.recompute_route(
            session, dest_route, plans=dest_plans, persist=False, acting_user=acting_user
        )
        late_order_ids: list[uuid.UUID] = []
        for index in preview.late_stop_indexes:
            late_order_ids.extend(dest_plans[index].order_ids)
        if late_order_ids and not confirm_late_delivery:
            raise ConfirmationRequired(
                "This reassignment makes "
                f"{len(preview.late_stop_indexes)} stop(s) miss their delivery window. "
                "Confirm to apply it anyway.",
                details={
                    "late_order_ids": [str(o) for o in late_order_ids],
                    "late_stop_sequences": [i + 1 for i in preview.late_stop_indexes],
                },
            )

        # ---- Apply -------------------------------------------------------
        dest_old_state = dest_route.to_dict()
        source_old_state = source_route.to_dict() if source_route else None

        if source_route is not None:
            await self._detach_order(session, source_route, order_id)
            await session.refresh(source_route, ["stops"])
            source_plans = self._plans_from_route(source_route)
            await self.recompute_route(
                session, source_route, plans=source_plans, acting_user=acting_user
            )
            await session.refresh(source_route, ["stops"])

        await self.recompute_route(
            session, dest_route, plans=dest_plans, acting_user=acting_user
        )
        await session.refresh(dest_route, ["stops"])

        order.status = OrderStatus.ASSIGNED.value

        late_stops = [
            stop
            for stop in dest_route.stops
            if stop.time_window_end is not None and as_utc(stop.eta) > as_utc(stop.time_window_end)
        ]
        await self.raise_late_delivery_alerts(session, dest_route, late_stops, acting_user)
        await self.check_capacity_alerts(session, dest_route, acting_user)
        if source_route is not None:
            await self.check_capacity_alerts(session, source_route, acting_user)

        # Requirement 10.8 / Property 23 — full reassignment audit entry.
        await audit_service.record_change(
            session,
            entity_id=order.order_id,
            entity_type=EntityType.ORDER,
            action="order.reassigned",
            acting_user=acting_user,
            old_state={
                "order_id": str(order.order_id),
                "source_route_id": str(source_route.route_id) if source_route else None,
                "dest_route_id": str(dest_route_id),
                "source_route": source_old_state,
                "dest_route": dest_old_state,
            },
            new_state={
                "order_id": str(order.order_id),
                "source_route_id": str(source_route.route_id) if source_route else None,
                "dest_route_id": str(dest_route.route_id),
                "source_route": source_route.to_dict() if source_route else None,
                "dest_route": dest_route.to_dict(),
                "acting_user": str(acting_user),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        logger.info(
            "order_reassigned",
            order_id=str(order_id),
            source_route_id=str(source_route.route_id) if source_route else None,
            dest_route_id=str(dest_route.route_id),
        )
        return source_route, dest_route, late_order_ids

    async def _find_route_for_order(
        self, session: AsyncSession, order_id: uuid.UUID
    ) -> uuid.UUID | None:
        return await session.scalar(
            select(Stop.route_id)
            .join(StopOrder, StopOrder.stop_id == Stop.stop_id)
            .where(StopOrder.order_id == order_id)
            .limit(1)
        )

    async def _detach_order(
        self, session: AsyncSession, route: Route, order_id: uuid.UUID
    ) -> None:
        # Detach through the ORM collections rather than a bulk DELETE. A bulk
        # statement leaves the already-loaded stop_orders collections stale, and
        # the unit of work then re-issues a DELETE for a row that is already gone.
        for stop in list(route.stops):
            for link in list(stop.stop_orders):
                if link.order_id == order_id:
                    stop.stop_orders.remove(link)
            # A stop with no orders left is no longer a stop.
            if not stop.stop_orders:
                route.stops.remove(stop)
        await session.flush()

    async def unassign_route_orders(
        self, session: AsyncSession, route: Route
    ) -> list[uuid.UUID]:
        """Release every order on a route back to the unassigned pool."""
        order_ids = [link.order_id for stop in route.stops for link in stop.stop_orders]
        if order_ids:
            orders = (
                await session.execute(select(Order).where(Order.order_id.in_(order_ids)))
            ).scalars()
            for order in orders:
                if order.status in (OrderStatus.ASSIGNED.value, OrderStatus.UNASSIGNED.value):
                    order.status = OrderStatus.UNASSIGNED.value
        return order_ids

    async def count_unassigned_orders(self, session: AsyncSession) -> int:
        return (
            await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.status == OrderStatus.UNASSIGNED.value)
            )
        ) or 0


def _max_opt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(as_utc(a), as_utc(b))


def _min_opt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(as_utc(a), as_utc(b))


route_service = RouteService()
