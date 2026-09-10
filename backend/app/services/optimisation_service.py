"""Orchestrates optimisation runs: data loading, solving, persistence, diffing.

Tasks 13.3–13.5 — Requirements 5.1, 5.7–5.11, 6.2, 6.3, 12.1–12.7, 18.2, 18.6.
Properties 11, 25, 27.
"""

from __future__ import annotations

import asyncio
import contextlib
import time as clock
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.errors import Conflict, ValidationFailed
from app.core.logging import get_logger
from app.models.entities import OptimisationRun, Order, Route, Vehicle
from app.models.enums import (
    AlertEntityType,
    AlertSeverity,
    AlertType,
    EntityType,
    OptimisationRunStatus,
    OptimisationRunType,
    OrderStatus,
    RouteStatus,
)
from app.services.alert_service import alert_service
from app.services.audit_service import audit_service
from app.services.geocoding_service import geocoding_service
from app.services.optimisation_engine import (
    LegCache,
    OptimisationRequest,
    OptimisationResult,
    OptOrder,
    OptVehicle,
    solve,
)
from app.services.route_math import (
    as_utc,
    build_schedule,
    matrix_nodes_for_route,
    planning_day_for,
    shift_start,
)
from app.services.route_service import route_service

logger = get_logger(__name__)

#: Postgres advisory lock key guarding single-run-at-a-time (Requirement 12.7).
RUN_LOCK_KEY = 748_213_991


class OptimisationService:
    #: Orders placed by the most recent persistence pass, used for run stats.
    _last_assigned_count: int = 0

    # ------------------------------------------------------------------ #
    # Concurrency guard (Requirement 12.7 / Property 27)
    # ------------------------------------------------------------------ #
    async def active_run(self, session: AsyncSession) -> OptimisationRun | None:
        """The genuinely-running run, if any.

        A run cannot outlive the wall-clock limit, so anything older is an
        orphan left by a crashed or restarted worker: it is retired here rather
        than blocking the dispatcher forever.
        """
        candidate = await session.scalar(
            select(OptimisationRun)
            .where(OptimisationRun.status == OptimisationRunStatus.IN_PROGRESS.value)
            .order_by(OptimisationRun.started_at.desc())
            .limit(1)
        )
        if candidate is None:
            return None
        age = datetime.now(UTC) - as_utc(candidate.started_at)
        if age.total_seconds() > settings.optimisation_wall_clock_limit_seconds + 30:
            await self.reap_orphaned_runs(session)
            return None
        return candidate

    async def reap_orphaned_runs(self, session: AsyncSession) -> int:
        """Abort in-progress runs that no live worker can still be executing."""
        cutoff = datetime.now(UTC) - timedelta(
            seconds=settings.optimisation_wall_clock_limit_seconds + 30
        )
        result = await session.execute(
            select(OptimisationRun).where(
                and_(
                    OptimisationRun.status == OptimisationRunStatus.IN_PROGRESS.value,
                    OptimisationRun.started_at < cutoff,
                )
            )
        )
        orphans = list(result.scalars().all())
        for run in orphans:
            run.status = OptimisationRunStatus.ABORTED.value
            run.ended_at = datetime.now(UTC)
            run.error_message = (
                "Run was abandoned (the worker restarted before it finished). "
                "No routes were changed."
            )
            run.progress_message = "Aborted"
        if orphans:
            await session.flush()
            logger.warning("orphaned_runs_reaped", count=len(orphans))
        return len(orphans)

    async def abort_all_in_progress(self, session: AsyncSession) -> int:
        """Startup reconciliation: no run survives a process restart."""
        result = await session.execute(
            select(OptimisationRun).where(
                OptimisationRun.status == OptimisationRunStatus.IN_PROGRESS.value
            )
        )
        runs = list(result.scalars().all())
        for run in runs:
            run.status = OptimisationRunStatus.ABORTED.value
            run.ended_at = datetime.now(UTC)
            run.error_message = (
                "Run was interrupted by an API restart. No routes were changed — "
                "start a new optimisation run."
            )
            run.progress_message = "Aborted"
        if runs:
            await session.flush()
            logger.warning("in_progress_runs_aborted_on_startup", count=len(runs))
        return len(runs)

    async def start_run(
        self,
        session: AsyncSession,
        *,
        acting_user: uuid.UUID,
        reoptimise: bool,
    ) -> OptimisationRun:
        """Claim the single run slot, or raise 409 if one is already running."""
        await session.execute(select(func.pg_advisory_xact_lock(RUN_LOCK_KEY)))
        existing = await self.active_run(session)
        if existing is not None:
            raise Conflict(
                "An optimisation run is already in progress. "
                "Wait for it to finish before starting another.",
                details={"run_id": str(existing.run_id)},
            )
        run = OptimisationRun(
            run_id=uuid.uuid4(),
            status=OptimisationRunStatus.IN_PROGRESS.value,
            run_type=(
                OptimisationRunType.REOPTIMISE if reoptimise else OptimisationRunType.INITIAL
            ).value,
            started_at=datetime.now(UTC),
            initiated_by=acting_user,
            progress_pct=0,
            progress_message="Queued",
        )
        session.add(run)
        await session.flush()
        return run

    # ------------------------------------------------------------------ #
    # Data loading
    # ------------------------------------------------------------------ #
    async def available_vehicles(self, session: AsyncSession) -> list[Vehicle]:
        """Requirement 5.11 — available = ``available`` AND capacity > 0."""
        result = await session.execute(
            select(Vehicle)
            .where(and_(Vehicle.available.is_(True), Vehicle.capacity_weight_kg > 0))
            .order_by(Vehicle.registration)
        )
        return list(result.scalars().all())

    async def _snapshot_routes(self, session: AsyncSession) -> dict[str, dict[str, Any]]:
        routes = await route_service.list_routes(session, include_completed=False)
        return {
            str(route.route_id): {
                "vehicle_id": str(route.vehicle_id),
                "driver_id": str(route.driver_id) if route.driver_id else None,
                "locked": route.locked,
                "order_ids": [
                    str(link.order_id)
                    for stop in sorted(route.stops, key=lambda s: s.sequence_number)
                    for link in stop.stop_orders
                ],
            }
            for route in routes
        }

    # ------------------------------------------------------------------ #
    # Run execution
    # ------------------------------------------------------------------ #
    async def execute_run(
        self,
        session: AsyncSession,
        run: OptimisationRun,
        *,
        acting_user: uuid.UUID,
        reoptimise: bool,
    ) -> OptimisationRun:
        """Run the solver and persist its output, honouring the 120s ceiling."""
        started = clock.monotonic()
        # Captured up front: a rollback expires the instance, and re-reading an
        # attribute afterwards would trigger a synchronous refresh.
        run_id = run.run_id
        before = await self._snapshot_routes(session)

        try:
            await asyncio.wait_for(
                self._execute_inner(
                    session, run, acting_user=acting_user, reoptimise=reoptimise, before=before
                ),
                timeout=settings.optimisation_wall_clock_limit_seconds,
            )
        except TimeoutError:
            # Requirements 5.9 / 12.5 / 18.5 — abort, preserve prior state. The
            # rollback discards every partial change, so the run row is then
            # marked on the same (now clean) session.
            await session.rollback()
            await self._mark_timed_out(session, run_id, acting_user)
            await session.commit()
            raise
        except Exception as exc:
            await session.rollback()
            await self._mark_failed(session, run_id, acting_user, str(exc))
            await session.commit()
            raise

        run.duration_seconds = clock.monotonic() - started
        await session.flush()
        return run

    async def _execute_inner(
        self,
        session: AsyncSession,
        run: OptimisationRun,
        *,
        acting_user: uuid.UUID,
        reoptimise: bool,
        before: dict[str, dict[str, Any]],
    ) -> None:
        vehicles = await self.available_vehicles(session)

        # Requirement 5.10 — abort when no vehicle can carry anything.
        if not vehicles:
            run.status = OptimisationRunStatus.ABORTED.value
            run.ended_at = datetime.now(UTC)
            run.error_message = (
                "No vehicles are available for assignment. Mark at least one vehicle available "
                "with a positive weight capacity, then run optimisation again."
            )
            run.solver_status = "NO_VEHICLES"
            await session.flush()
            await events.publish(
                events.OPTIMISATION_FAILED,
                {"run_id": str(run.run_id), "reason": run.error_message},
            )
            raise ValidationFailed(run.error_message)

        all_routes = await route_service.list_routes(session, include_completed=False)
        locked_routes = [r for r in all_routes if r.locked]
        unlocked_routes = [
            r
            for r in all_routes
            if not r.locked and RouteStatus(r.status) in (RouteStatus.DRAFT, RouteStatus.APPROVED)
        ]
        locked_vehicle_ids = {r.vehicle_id for r in locked_routes}

        await self._publish_progress(session, run, 3, "Collecting orders and vehicles")

        # Requirement 12.3 — pool orders from unlocked routes back in.
        if reoptimise:
            for route in unlocked_routes:
                await route_service.unassign_route_orders(session, route)
                await session.delete(route)
            await session.flush()

        orders_result = await session.execute(
            select(Order).where(
                and_(
                    Order.status == OrderStatus.UNASSIGNED.value,
                    Order.delivery_location.isnot(None),
                )
            )
        )
        orders = list(orders_result.scalars().all())

        # Requirement 11.3 / Property 25 — a locked route's vehicle is off limits.
        solver_vehicles = [v for v in vehicles if v.vehicle_id not in locked_vehicle_ids]

        run.orders_considered = len(orders)
        run.locked_excluded_count = len(locked_routes)
        await session.flush()

        if not orders:
            self._last_assigned_count = 0
            await self._finish_run(
                session,
                run,
                acting_user=acting_user,
                result=OptimisationResult(locked_excluded_count=len(locked_routes)),
                before=before,
                created_routes=[],
            )
            return

        planning_day = planning_day_for(
            min((o.time_window_start for o in orders if o.time_window_start), default=None)
        )

        await self._publish_progress(session, run, 8, "Fetching traffic-aware travel times")
        legs = await self._load_legs(session, solver_vehicles, orders, planning_day)

        request = OptimisationRequest(
            orders=[self._to_opt_order(o) for o in orders],
            vehicles=[self._to_opt_vehicle(v) for v in solver_vehicles],
            planning_day=planning_day,
            locked_route_count=len(locked_routes),
            time_limit_seconds=settings.optimisation_solver_limit_seconds,
        )

        loop = asyncio.get_running_loop()
        progress_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()

        def on_progress(pct: int, message: str) -> None:
            loop.call_soon_threadsafe(progress_queue.put_nowait, (pct, message))

        request.progress_callback = on_progress
        solve_task = asyncio.create_task(asyncio.to_thread(solve, request, legs))
        pump = asyncio.create_task(self._pump_progress(run, progress_queue, solve_task))
        try:
            result: OptimisationResult = await solve_task
        finally:
            pump.cancel()

        if result.timed_out:
            raise TimeoutError("solver exhausted its wall-clock budget")

        await self._publish_progress(session, run, 92, "Persisting routes")
        created = await self._persist_result(
            session, run, result, solver_vehicles, orders, planning_day, legs, acting_user
        )
        await self._finish_run(
            session,
            run,
            acting_user=acting_user,
            result=result,
            before=before,
            created_routes=created,
        )

    async def _pump_progress(
        self,
        run: OptimisationRun,
        queue: asyncio.Queue[tuple[int, str]],
        solve_task: asyncio.Task,
    ) -> None:
        """Broadcast progress at <= 5s intervals (Requirement 18.6)."""
        last_sent = clock.monotonic()
        latest = (10, "Solving")
        while not solve_task.done():
            with contextlib.suppress(TimeoutError):
                latest = await asyncio.wait_for(
                    queue.get(), timeout=settings.optimisation_progress_interval_seconds
                )
            if clock.monotonic() - last_sent >= settings.optimisation_progress_interval_seconds:
                await events.publish(
                    events.OPTIMISATION_PROGRESS,
                    {
                        "run_id": str(run.run_id),
                        "progress_pct": latest[0],
                        "message": latest[1],
                    },
                )
                last_sent = clock.monotonic()

    async def _publish_progress(
        self, session: AsyncSession, run: OptimisationRun, pct: int, message: str
    ) -> None:
        run.progress_pct = pct
        run.progress_message = message
        await session.flush()
        await events.publish(
            events.OPTIMISATION_PROGRESS,
            {"run_id": str(run.run_id), "progress_pct": pct, "message": message},
        )

    async def _load_legs(
        self,
        session: AsyncSession,
        vehicles: list[Vehicle],
        orders: list[Order],
        planning_day,
    ) -> LegCache:
        """Fetch the travel-time matrix over depots + delivery points."""
        waypoints = [v.depot_location for v in vehicles] + [
            o.delivery_location for o in orders if o.delivery_location is not None
        ]
        departure = (
            shift_start(planning_day, min(v.operating_hours_start for v in vehicles))
            if vehicles
            else None
        )
        # The fleet-wide matrix is O(n^2) over every depot and delivery point;
        # only the legs of the routes that come out of the solve are worth
        # folding into the historical averages.
        matrix = await geocoding_service.get_travel_time_matrix(
            session, waypoints, departure=departure, record_history=False
        )
        legs = LegCache(degraded=matrix.degraded)
        legs.ingest(waypoints, matrix)
        return legs

    @staticmethod
    def _to_opt_order(order: Order) -> OptOrder:
        return OptOrder(
            order_id=order.order_id,
            location=order.delivery_location,
            address=order.delivery_address,
            weight_kg=order.cargo_weight_kg,
            volume_m3=order.cargo_volume_m3,
            time_window_start=order.time_window_start,
            time_window_end=order.time_window_end,
            priority=order.priority == "priority",
            service_duration_min=order.service_duration_min,
        )

    @staticmethod
    def _to_opt_vehicle(vehicle: Vehicle) -> OptVehicle:
        return OptVehicle(
            vehicle_id=vehicle.vehicle_id,
            registration=vehicle.registration,
            depot=vehicle.depot_location,
            capacity_weight_kg=vehicle.capacity_weight_kg,
            capacity_volume_m3=vehicle.capacity_volume_m3,
            operating_hours_start=vehicle.operating_hours_start,
            operating_hours_end=vehicle.operating_hours_end,
            driver_id=vehicle.driver_id,
        )

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def _persist_result(
        self,
        session: AsyncSession,
        run: OptimisationRun,
        result: OptimisationResult,
        vehicles: list[Vehicle],
        orders: list[Order],
        planning_day,
        legs: LegCache,
        acting_user: uuid.UUID,
    ) -> list[Route]:
        vehicles_by_id = {v.vehicle_id: v for v in vehicles}
        orders_by_id = {o.order_id: o for o in orders}
        created: list[Route] = []
        assigned_ids: set[uuid.UUID] = set()
        stops_created = 0

        for solved in result.routes:
            vehicle = vehicles_by_id[solved.vehicle_id]
            waypoints = matrix_nodes_for_route(vehicle.depot_location, solved.stops)
            schedule = build_schedule(
                depot=vehicle.depot_location,
                departure_time=shift_start(planning_day, vehicle.operating_hours_start),
                stops=solved.stops,
                matrix=legs.as_route_matrix(waypoints),
            )

            # Final safety net: never persist a route that breaks a hard
            # constraint (Properties 12, 13, 14).
            if schedule.total_weight_kg > vehicle.capacity_weight_kg or (
                vehicle.capacity_volume_m3 is not None
                and schedule.total_volume_m3 is not None
                and schedule.total_volume_m3 > vehicle.capacity_volume_m3
            ):  # pragma: no cover - solver guarantees this
                logger.error(
                    "capacity_invariant_violated_post_solve",
                    vehicle_id=str(vehicle.vehicle_id),
                )
                result.unassigned_order_ids.extend(
                    oid for stop in solved.stops for oid in stop.order_ids
                )
                continue

            # A stop that would be serviced outside its window is dropped and
            # its orders reported infeasible, rather than persisted late
            # (Requirement 5.4 / Property 14). Removing a stop only pulls later
            # ETAs earlier, so this converges immediately in practice; the loop
            # makes that a guarantee rather than an argument.
            while schedule.late_stop_indexes and solved.stops:
                late = set(schedule.late_stop_indexes)
                for index in late:
                    result.infeasible.extend(
                        _make_infeasible(order_id)
                        for order_id in solved.stops[index].order_ids
                    )
                solved.stops = [
                    stop for index, stop in enumerate(solved.stops) if index not in late
                ]
                if not solved.stops:
                    break
                waypoints = matrix_nodes_for_route(vehicle.depot_location, solved.stops)
                schedule = build_schedule(
                    depot=vehicle.depot_location,
                    departure_time=shift_start(planning_day, vehicle.operating_hours_start),
                    stops=solved.stops,
                    matrix=legs.as_route_matrix(waypoints),
                )
            if not solved.stops:
                continue

            route = await route_service.create_route(
                session,
                vehicle=vehicle,
                run_id=run.run_id,
                schedule=schedule,
                acting_user=acting_user,
                data_quality_warning=result.degraded_travel_times,
                data_quality_message=(
                    "Travel times estimated from historical averages — Mapping Service unavailable"
                    if result.degraded_travel_times
                    else None
                ),
                priority_relaxed=solved.priority_relaxed,
            )
            created.append(route)

            # Requirement 7.4's fallback learns from the legs actually driven.
            if not result.degraded_travel_times:
                await geocoding_service.record_samples(
                    session, waypoints, legs.as_route_matrix(waypoints)
                )
            stops_created += len(solved.stops)

            for stop in solved.stops:
                for order_id in stop.order_ids:
                    assigned_ids.add(order_id)
                    order = orders_by_id.get(order_id)
                    if order is not None:
                        order.status = OrderStatus.ASSIGNED.value
            await route_service.check_capacity_alerts(session, route, acting_user)

        # Requirement 5.7 / Property 11 — every unplaced order gets an alert.
        alerted: set[uuid.UUID] = set()
        for entry in result.infeasible:
            if entry.order_id in assigned_ids or entry.order_id in alerted:
                continue
            alerted.add(entry.order_id)
            await self._raise_impossible(session, entry.order_id, entry.reason, acting_user)

        for order in orders:
            if order.order_id in assigned_ids:
                continue
            order.status = OrderStatus.UNASSIGNED.value
            if order.order_id not in alerted:
                alerted.add(order.order_id)
                await self._raise_impossible(
                    session,
                    order.order_id,
                    "No feasible assignment exists for this order given the available vehicles "
                    "and their capacity, time-window, and operating-hour constraints",
                    acting_user,
                )
        await session.flush()
        self._last_assigned_count = len(assigned_ids)
        return created

    async def _raise_impossible(
        self,
        session: AsyncSession,
        order_id: uuid.UUID,
        reason: str,
        acting_user: uuid.UUID | None,
    ) -> None:
        order = await session.get(Order, order_id)
        label = order.delivery_address if order else str(order_id)
        await alert_service.raise_alert(
            session,
            alert_type=AlertType.IMPOSSIBLE_ORDER,
            severity=AlertSeverity.CRITICAL,
            entity_type=AlertEntityType.ORDER,
            entity_id=order_id,
            message=f"Order for '{label}' could not be assigned: {reason}",
            context={"reason": reason},
            acting_user=acting_user,
        )

    # ------------------------------------------------------------------ #
    # Completion, diffing, failure handling
    # ------------------------------------------------------------------ #
    async def _finish_run(
        self,
        session: AsyncSession,
        run: OptimisationRun,
        *,
        acting_user: uuid.UUID,
        result: OptimisationResult,
        before: dict[str, dict[str, Any]],
        created_routes: list[Route],
    ) -> None:
        after = await self._snapshot_routes(session)
        diff = self._build_diff(before, after)

        run.status = OptimisationRunStatus.COMPLETED.value
        run.ended_at = datetime.now(UTC)
        run.routes_created = len(created_routes)
        run.orders_assigned = getattr(self, "_last_assigned_count", 0)
        run.orders_unassigned = await route_service.count_unassigned_orders(session)
        run.locked_excluded_count = result.locked_excluded_count
        run.solver_status = result.solver_status
        run.progress_pct = 100
        run.progress_message = "Complete"
        run.diff = diff
        await session.flush()

        await audit_service.record_change(
            session,
            entity_id=run.run_id,
            entity_type=EntityType.ROUTE,
            action="optimisation.completed",
            acting_user=acting_user,
            old_state=None,
            new_state={
                "run_id": str(run.run_id),
                "routes_created": run.routes_created,
                "orders_assigned": run.orders_assigned,
                "orders_unassigned": run.orders_unassigned,
                "locked_excluded_count": run.locked_excluded_count,
                "solver_status": run.solver_status,
            },
        )
        logger.info(
            "optimisation_complete",
            run_id=str(run.run_id),
            routes=run.routes_created,
            assigned=run.orders_assigned,
            unassigned=run.orders_unassigned,
            locked_excluded=run.locked_excluded_count,
        )

    @staticmethod
    def _build_diff(
        before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Requirement 12.4 — surface what changed for dispatcher review."""
        entries: list[dict[str, Any]] = []
        for route_id, state in after.items():
            previous = before.get(route_id)
            if previous is None:
                entries.append(
                    {
                        "route_id": route_id,
                        "vehicle_id": state["vehicle_id"],
                        "change": "created",
                        "added_order_ids": state["order_ids"],
                        "removed_order_ids": [],
                        "sequence_changed": False,
                        "driver_changed": False,
                        "new_driver_id": state["driver_id"],
                    }
                )
                continue
            added = [o for o in state["order_ids"] if o not in previous["order_ids"]]
            removed = [o for o in previous["order_ids"] if o not in state["order_ids"]]
            sequence_changed = state["order_ids"] != previous["order_ids"] and not (
                added or removed
            )
            driver_changed = state["driver_id"] != previous["driver_id"]
            if added or removed or sequence_changed or driver_changed:
                entries.append(
                    {
                        "route_id": route_id,
                        "vehicle_id": state["vehicle_id"],
                        "change": "updated",
                        "added_order_ids": added,
                        "removed_order_ids": removed,
                        "sequence_changed": sequence_changed,
                        "driver_changed": driver_changed,
                        "previous_driver_id": previous["driver_id"],
                        "new_driver_id": state["driver_id"],
                    }
                )
        for route_id, previous in before.items():
            if route_id not in after:
                entries.append(
                    {
                        "route_id": route_id,
                        "vehicle_id": previous["vehicle_id"],
                        "change": "removed",
                        "added_order_ids": [],
                        "removed_order_ids": previous["order_ids"],
                        "sequence_changed": False,
                        "driver_changed": False,
                    }
                )
        return {"routes": entries, "changed_count": len(entries)}

    async def _mark_timed_out(
        self, session: AsyncSession, run_id: uuid.UUID, acting_user: uuid.UUID
    ) -> None:
        run = await session.get(OptimisationRun, run_id)
        if run is None:  # pragma: no cover
            return
        run.status = OptimisationRunStatus.TIMED_OUT.value
        run.ended_at = datetime.now(UTC)
        run.error_message = (
            f"Optimisation run exceeded the "
            f"{settings.optimisation_wall_clock_limit_seconds:.0f}s limit and was aborted. "
            "All orders were left unassigned and existing routes are unchanged."
        )
        run.progress_message = "Timed out"
        await audit_service.record_change(
            session,
            entity_id=run.run_id,
            entity_type=EntityType.ROUTE,
            action="optimisation.timed_out",
            acting_user=acting_user,
            old_state=None,
            new_state={"run_id": str(run.run_id), "error": run.error_message},
        )
        await events.publish(
            events.OPTIMISATION_FAILED, {"run_id": str(run_id), "reason": "timeout"}
        )

    async def _mark_failed(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        acting_user: uuid.UUID,
        message: str,
    ) -> None:
        run = await session.get(OptimisationRun, run_id)
        if run is not None:
            if run.status == OptimisationRunStatus.IN_PROGRESS.value:
                run.status = OptimisationRunStatus.ABORTED.value
            run.ended_at = datetime.now(UTC)
            run.error_message = message
            run.progress_message = "Failed"
            await session.flush()
        await events.publish(
            events.OPTIMISATION_FAILED, {"run_id": str(run_id), "reason": message}
        )

    # ------------------------------------------------------------------ #
    # Priority re-optimisation flagging (Task 13.5 — Requirements 6.2, 6.3)
    # ------------------------------------------------------------------ #
    async def flag_routes_for_priority_order(
        self, session: AsyncSession, order: Order, *, acting_user: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        """Flag routes whose vehicle overlaps the new priority order.

        Overlap means the vehicle still has headroom for the order's weight
        (capacity overlap) or the route's stops share the order's time window.
        """
        if order.priority != "priority":
            return []
        routes = await route_service.list_routes(session, include_completed=False)
        affected: list[uuid.UUID] = []
        for route in routes:
            if route.locked or RouteStatus(route.status) in (
                RouteStatus.DISPATCHED,
                RouteStatus.COMPLETED,
            ):
                continue
            vehicle = route.vehicle or await session.get(Vehicle, route.vehicle_id)
            if vehicle is None:
                continue
            capacity_overlap = (
                route.total_weight_kg + order.cargo_weight_kg <= vehicle.capacity_weight_kg
            )
            window_overlap = False
            if order.time_window_start and order.time_window_end:
                for stop in route.stops:
                    if (
                        stop.time_window_start
                        and stop.time_window_end
                        and as_utc(stop.time_window_start) <= as_utc(order.time_window_end)
                        and as_utc(order.time_window_start) <= as_utc(stop.time_window_end)
                    ):
                        window_overlap = True
                        break
            if capacity_overlap or window_overlap:
                route.needs_reoptimisation = True
                affected.append(route.route_id)
        await session.flush()

        if affected:
            await events.publish(
                events.REOPTIMISATION_SUGGESTED,
                {
                    "reason": "priority_order_created",
                    "order_id": str(order.order_id),
                    "route_ids": [str(r) for r in affected],
                    "message": (
                        f"New priority order for '{order.delivery_address}' affects "
                        f"{len(affected)} route(s). Re-optimise to schedule it."
                    ),
                },
            )
            logger.info(
                "priority_reoptimisation_flagged",
                order_id=str(order.order_id),
                routes=len(affected),
            )
        return affected

    async def notify_pending_orders(self, session: AsyncSession) -> None:
        """Requirement 12.6 — nudge dispatchers when unassigned orders exist."""
        pending = await route_service.count_unassigned_orders(session)
        route_count = await session.scalar(select(func.count()).select_from(Route)) or 0
        if pending and route_count:
            await events.publish(
                events.ORDERS_PENDING,
                {
                    "pending_count": pending,
                    "message": (
                        f"{pending} unassigned order(s) are waiting. "
                        "Re-optimise to fold them into the plan."
                    ),
                },
            )


def _make_infeasible(order_id: uuid.UUID):
    from app.services.optimisation_engine import InfeasibleOrder

    return InfeasibleOrder(
        order_id,
        "Scheduled ETA fell outside the order's delivery window; the order was left unassigned",
    )


optimisation_service = OptimisationService()
