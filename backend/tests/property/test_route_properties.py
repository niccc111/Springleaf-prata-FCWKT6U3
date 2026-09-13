"""Property tests for the Route Service (Tasks 5.2, 5.4, 5.6-5.9, 5.11, 5.12)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select

from app.adapters.base import Leg, TravelMatrix
from app.core.errors import (
    CapacityViolation,
    InvalidStateTransition,
    RouteLockedError,
)
from app.models.entities import Alert, AuditLog, Route
from app.models.enums import AlertType, RouteStatus
from app.services.route_math import as_utc, build_schedule
from app.services.route_service import route_service
from tests.conftest import build_order, build_vehicle, make_actor
from tests.property.strategies import geo_points, weights

pytestmark = pytest.mark.property

DAY = datetime(2026, 6, 1, tzinfo=UTC)


async def _make_route(
    session,
    *,
    user_id: uuid.UUID,
    orders_spec: list[dict],
    capacity_weight: Decimal = Decimal("5000"),
    capacity_volume: Decimal | None = Decimal("50"),
    locked: bool = False,
    status: RouteStatus = RouteStatus.DRAFT,
) -> Route:
    """Build a persisted route with one stop per order spec."""
    from app.models.entities import OptimisationRun
    from app.models.enums import OptimisationRunStatus
    from app.services.route_math import StopPlan

    vehicle = build_vehicle(capacity_weight_kg=capacity_weight, capacity_volume_m3=capacity_volume)
    session.add(vehicle)
    run = OptimisationRun(
        run_id=uuid.uuid4(),
        status=OptimisationRunStatus.COMPLETED.value,
        initiated_by=user_id,
    )
    session.add(run)
    await session.flush()

    plans: list[StopPlan] = []
    for index, spec in enumerate(orders_spec):
        order = build_order(
            cargo_weight_kg=spec["weight"],
            cargo_volume_m3=spec.get("volume"),
            delivery_location=spec["location"],
            delivery_address=f"stop {index}",
            time_window_start=spec.get("tw_start"),
            time_window_end=spec.get("tw_end"),
            priority=spec.get("priority", "standard"),
            service_duration_min=spec.get("service", 5),
        )
        session.add(order)
        await session.flush()
        plans.append(
            StopPlan(
                location=spec["location"],
                address=f"stop {index}",
                order_ids=[order.order_id],
                weight_kg=spec["weight"],
                volume_m3=spec.get("volume"),
                service_duration_min=spec.get("service", 5),
                time_window_start=spec.get("tw_start"),
                time_window_end=spec.get("tw_end"),
                has_priority_order=spec.get("priority") == "priority",
            )
        )

    matrix = _matrix([vehicle.depot_location, *[p.location for p in plans]])
    schedule = build_schedule(
        depot=vehicle.depot_location,
        departure_time=DAY.replace(hour=8),
        stops=plans,
        matrix=matrix,
    )
    route = await route_service.create_route(
        session, vehicle=vehicle, run_id=run.run_id, schedule=schedule, acting_user=user_id
    )
    if locked:
        route.locked = True
    route.status = status.value
    await session.flush()
    return route


def _snapshot(route: Route) -> dict:
    """Materialise everything the assertions need while the session is open."""
    ordered = sorted(route.stops, key=lambda s: s.sequence_number)
    return {
        "route_id": route.route_id,
        "total_weight_kg": float(route.total_weight_kg),
        "stop_weight_sum": float(
            sum(
                link.order.cargo_weight_kg
                for stop in ordered
                for link in stop.stop_orders
                if link.order is not None
            )
        ),
        "etas": [as_utc(stop.eta) for stop in ordered],
        "sequences": [stop.sequence_number for stop in ordered],
        "order_ids": [link.order_id for stop in ordered for link in stop.stop_orders],
    }


def _matrix(points, *, speed_kmh: float = 32.0, scale: float = 1.0) -> TravelMatrix:
    legs: dict[tuple[int, int], Leg] = {}
    for i, a in enumerate(points):
        for j, b in enumerate(points):
            if i == j:
                legs[(i, j)] = Leg(0.0, 0.0)
                continue
            km = a.haversine_km(b) * 1.32
            legs[(i, j)] = Leg(
                duration_seconds=km / speed_kmh * 3600.0 * scale, distance_metres=km * 1000.0
            )
    return TravelMatrix(legs=legs)


# Feature: route-optimisation-engine, Property 26: Dispatched routes are automatically locked
@given(point=geo_points(), weight=weights)
def test_property_26_dispatched_routes_are_locked(
    sessionmaker_, truncate, run_async, point, weight
):
    """Any route that transitions to dispatched is locked immediately.

    Validates: Requirements 11.6
    """
    truncate()

    async def scenario() -> Route:
        async with sessionmaker_() as session:
            actor = make_actor()
            route = await _make_route(
                session,
                user_id=actor,
                orders_spec=[{"weight": weight, "location": point}],
                status=RouteStatus.APPROVED,
            )
            await session.commit()
            updated = await route_service.update_route_status(
                session, route.route_id, RouteStatus.DISPATCHED, actor
            )
            await session.commit()
            return updated

    route = run_async(scenario())
    assert route.status == RouteStatus.DISPATCHED.value
    assert route.locked is True


# Feature: route-optimisation-engine, Property 24: Route locking state machine
@given(
    status=st.sampled_from(list(RouteStatus)),
    action=st.sampled_from(["lock", "unlock"]),
    point=geo_points(),
)
def test_property_24_locking_state_machine(
    sessionmaker_, truncate, run_async, status, action, point
):
    """locked=true is permitted only from draft/approved; locked=false only when
    the status is not dispatched or completed.

    Validates: Requirements 11.1, 11.2, 11.4, 11.5
    """
    truncate()
    must_be_locked = status in (RouteStatus.DISPATCHED, RouteStatus.COMPLETED)

    async def scenario() -> tuple[bool, Route | None]:
        async with sessionmaker_() as session:
            actor = make_actor()
            route = await _make_route(
                session,
                user_id=actor,
                orders_spec=[{"weight": Decimal("10"), "location": point}],
                status=status,
                locked=must_be_locked or action == "unlock",
            )
            await session.commit()
            route_id = route.route_id
            try:
                if action == "lock":
                    result = await route_service.lock_route(session, route_id, actor)
                else:
                    result = await route_service.unlock_route(session, route_id, actor)
                await session.commit()
                return True, result.locked
            except InvalidStateTransition:
                await session.rollback()
                return False, None

    permitted, locked = run_async(scenario())

    if action == "lock":
        expected = status in (RouteStatus.DRAFT, RouteStatus.APPROVED)
    else:
        expected = status not in (RouteStatus.DISPATCHED, RouteStatus.COMPLETED)

    assert permitted is expected, (
        f"{action} on a {status.value} route should be " f"{'permitted' if expected else 'denied'}"
    )
    if permitted:
        assert locked is (action == "lock")


# Feature: route-optimisation-engine, Property 20: Manual reassignment recalculation is consistent
# Feature: route-optimisation-engine, Property 23: Manual reassignment audit completeness
@given(
    src_weights=st.lists(weights, min_size=1, max_size=4),
    dst_weights=st.lists(weights, min_size=1, max_size=4),
    points=st.lists(geo_points(), min_size=8, max_size=8),
)
def test_property_20_and_23_reassignment_consistency_and_audit(
    sessionmaker_, truncate, run_async, src_weights, dst_weights, points
):
    """A valid reassignment leaves both routes internally consistent — totals
    equal the sum of assigned orders and ETAs increase monotonically — and
    writes an audit entry naming the user, both routes, and the order.

    Validates: Requirements 10.2, 10.8
    """
    truncate()

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            src = await _make_route(
                session,
                user_id=actor,
                orders_spec=[
                    {"weight": w, "volume": Decimal("0.10"), "location": points[i]}
                    for i, w in enumerate(src_weights)
                ],
            )
            dst = await _make_route(
                session,
                user_id=actor,
                orders_spec=[
                    {"weight": w, "volume": Decimal("0.10"), "location": points[4 + i]}
                    for i, w in enumerate(dst_weights)
                ],
            )
            await session.commit()

            # Plain values, captured while the session is open: a later
            # rollback would expire these instances and reading an attribute
            # then triggers a synchronous refresh.
            src_id = src.route_id
            moved_order = src.stops[0].stop_orders[0].order_id
            source, dest, _ = await route_service.reassign_order(
                session,
                order_id=moved_order,
                dest_route_id=dst.route_id,
                source_route_id=src.route_id,
                acting_user=actor,
                confirm_late_delivery=True,
            )
            await session.commit()

            source = await route_service.get_route(session, source.route_id)
            dest = await route_service.get_route(session, dest.route_id)
            audit = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.entity_id == moved_order,
                            AuditLog.action == "order.reassigned",
                        )
                    )
                )
                .scalars()
                .all()
            )
            # Snapshot everything while the session is still open — the
            # assertions below must not trigger a lazy load.
            return {
                "routes": [_snapshot(source), _snapshot(dest)],
                "source": _snapshot(source),
                "dest": _snapshot(dest),
                "moved_order": moved_order,
                "user_id": actor,
                "src_id": src_id,
                "audit": [
                    {
                        "acting_user": entry.acting_user,
                        "old_state": entry.old_state,
                        "new_state": entry.new_state,
                        "created_at": entry.created_at,
                    }
                    for entry in audit
                ],
            }

    data = run_async(scenario())
    moved_order = data["moved_order"]

    for route in data["routes"]:
        assert route["total_weight_kg"] == pytest.approx(
            route["stop_weight_sum"], abs=0.01
        ), "total_weight_kg must equal the sum of assigned order weights"
        assert route["etas"] == sorted(
            route["etas"]
        ), "ETAs must increase monotonically along the sequence"
        assert route["sequences"] == list(
            range(1, len(route["sequences"]) + 1)
        ), "stop sequence must be 1..n"

    assert moved_order in data["dest"]["order_ids"]
    assert moved_order not in data["source"]["order_ids"]

    assert len(data["audit"]) == 1, "exactly one reassignment audit entry is expected"
    entry = data["audit"][0]
    assert entry["acting_user"] == data["user_id"]
    assert entry["new_state"]["order_id"] == str(moved_order)
    assert entry["new_state"]["source_route_id"] == str(data["src_id"])
    assert entry["new_state"]["dest_route_id"] == str(data["dest"]["route_id"])
    assert entry["old_state"] is not None
    assert entry["created_at"] is not None


# Feature: route-optimisation-engine, Property 21: Manual reassignment capacity rejection
@given(
    capacity=st.decimals(min_value=Decimal("10"), max_value=Decimal("100"), places=2),
    existing=st.decimals(min_value=Decimal("5"), max_value=Decimal("95"), places=2),
    moving=st.decimals(min_value=Decimal("5"), max_value=Decimal("95"), places=2),
    points=st.lists(geo_points(), min_size=4, max_size=4),
    volume_case=st.booleans(),
)
def test_property_21_capacity_rejection(
    sessionmaker_, truncate, run_async, capacity, existing, moving, points, volume_case
):
    """A reassignment that would breach the destination's weight or volume
    capacity is rejected and leaves both routes unchanged.

    Validates: Requirements 10.3, 10.4
    """
    truncate()
    if volume_case:
        # Keep weight comfortably inside capacity so volume is the binding limit.
        weight_capacity = Decimal("100000")
        volume_capacity = Decimal("1.00")
        existing_volume = Decimal("0.80")
        moving_volume = Decimal("0.80")
    else:
        weight_capacity = capacity
        volume_capacity = None
        existing_volume = moving_volume = None

    breaches = (
        (existing_volume + moving_volume) > volume_capacity
        if volume_case
        else (existing + moving) > weight_capacity
    )

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            src = await _make_route(
                session,
                user_id=actor,
                orders_spec=[
                    {
                        "weight": moving if not volume_case else Decimal("1"),
                        "volume": moving_volume,
                        "location": points[0],
                    }
                ],
            )
            dst = await _make_route(
                session,
                user_id=actor,
                orders_spec=[
                    {
                        "weight": existing if not volume_case else Decimal("1"),
                        "volume": existing_volume,
                        "location": points[1],
                    }
                ],
                capacity_weight=weight_capacity,
                capacity_volume=volume_capacity,
            )
            await session.commit()
            # Captured as plain values before any rollback expires the rows.
            src_id, dst_id = src.route_id, dst.route_id
            before = (dst.total_weight_kg, dst.total_volume_m3, len(dst.stops))
            src_before = (src.total_weight_kg, len(src.stops))
            moved = src.stops[0].stop_orders[0].order_id

            rejected = False
            try:
                await route_service.reassign_order(
                    session,
                    order_id=moved,
                    dest_route_id=dst_id,
                    source_route_id=src_id,
                    acting_user=actor,
                    confirm_late_delivery=True,
                )
                await session.commit()
            except CapacityViolation:
                rejected = True
                await session.rollback()

        async with sessionmaker_() as verify:
            dst_after = await route_service.get_route(verify, dst_id)
            src_after = await route_service.get_route(verify, src_id)
            return (
                rejected,
                before,
                src_before,
                (dst_after.total_weight_kg, dst_after.total_volume_m3, len(dst_after.stops)),
                (src_after.total_weight_kg, len(src_after.stops)),
            )

    rejected, before, src_before, dst_after, src_after = run_async(scenario())

    assert rejected is breaches, f"expected rejection={breaches} for the capacity configuration"
    if rejected:
        assert dst_after == before, "the destination route must be untouched"
        assert src_after == src_before, "the source route must be untouched"


# Feature: route-optimisation-engine, Property 22: Locked route reassignment rejection
@given(points=st.lists(geo_points(), min_size=4, max_size=4))
def test_property_22_locked_destination_rejected(sessionmaker_, truncate, run_async, points):
    """A reassignment into a locked destination route is rejected and leaves
    both routes unchanged.

    Validates: Requirements 10.6
    """
    truncate()

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            src = await _make_route(
                session,
                user_id=actor,
                orders_spec=[{"weight": Decimal("10"), "location": points[0]}],
            )
            dst = await _make_route(
                session,
                user_id=actor,
                orders_spec=[{"weight": Decimal("10"), "location": points[1]}],
                locked=True,
            )
            await session.commit()
            src_id, dst_id = src.route_id, dst.route_id
            before = (dst.total_weight_kg, len(dst.stops), src.total_weight_kg, len(src.stops))
            moved = src.stops[0].stop_orders[0].order_id

            rejected = False
            try:
                await route_service.reassign_order(
                    session,
                    order_id=moved,
                    dest_route_id=dst_id,
                    source_route_id=src_id,
                    acting_user=actor,
                    confirm_late_delivery=True,
                )
                await session.commit()
            except RouteLockedError:
                rejected = True
                await session.rollback()

        async with sessionmaker_() as verify:
            dst_after = await route_service.get_route(verify, dst_id)
            src_after = await route_service.get_route(verify, src_id)
            return (
                rejected,
                before,
                (
                    dst_after.total_weight_kg,
                    len(dst_after.stops),
                    src_after.total_weight_kg,
                    len(src_after.stops),
                ),
            )

    rejected, before, after = run_async(scenario())
    assert rejected, "a locked destination must reject the reassignment"
    assert after == before, "both routes must be untouched"


# Feature: route-optimisation-engine, Property 16: ETA recalculation propagates downstream consistently
# Feature: route-optimisation-engine, Property 17: Late delivery alerts raised for all time window violations
@given(
    stop_count=st.integers(min_value=2, max_value=5),
    slowdown=st.floats(min_value=1.0, max_value=8.0, allow_nan=False),
    points=st.lists(geo_points(), min_size=6, max_size=6),
    window_hours=st.integers(min_value=1, max_value=6),
)
def test_property_16_and_17_eta_recalculation(
    sessionmaker_, truncate, run_async, stop_count, slowdown, points, window_hours
):
    """Recomputing with slower travel times pushes every downstream ETA out to
    match the cumulative travel time from the depot, and any stop whose new ETA
    passes its window end raises a warning-severity Late Delivery Alert.

    Validates: Requirements 7.2, 7.3, 14.2
    """
    truncate()
    window_end = DAY.replace(hour=8) + timedelta(hours=window_hours)

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            route = await _make_route(
                session,
                user_id=actor,
                orders_spec=[
                    {
                        "weight": Decimal("10"),
                        "location": points[i],
                        "tw_end": window_end,
                        "service": 5,
                    }
                    for i in range(stop_count)
                ],
            )
            await session.commit()
            route_id = route.route_id

            ordered = sorted(route.stops, key=lambda s: s.sequence_number)
            waypoints = [route.vehicle.depot_location, *[s.location for s in ordered]]
            slow = _matrix(waypoints, scale=slowdown)

            await route_service.recalculate_etas(session, route_id, matrix=slow, acting_user=actor)
            await session.commit()

            refreshed = await route_service.get_route(session, route_id)
            alerts = (
                (
                    await session.execute(
                        select(Alert).where(Alert.alert_type == AlertType.LATE_DELIVERY.value)
                    )
                )
                .scalars()
                .all()
            )
            ordered_stops = [
                {
                    "stop_id": stop.stop_id,
                    "sequence_number": stop.sequence_number,
                    "eta": as_utc(stop.eta),
                    "time_window_start": stop.time_window_start,
                    "time_window_end": stop.time_window_end,
                    "service_duration_min": stop.service_duration_min,
                }
                for stop in sorted(refreshed.stops, key=lambda s: s.sequence_number)
            ]
            return (
                ordered_stops,
                slow,
                [{"severity": a.severity, "context": a.context} for a in alerts],
            )

    ordered, slow, alerts = run_async(scenario())

    # Recompute expected ETAs independently from the depot forward.
    expected: list[datetime] = []
    current = DAY.replace(hour=8)
    for index, stop in enumerate(ordered):
        current = current + timedelta(seconds=slow.duration_seconds(index, index + 1))
        window_start = stop["time_window_start"]
        if window_start is not None and current < as_utc(window_start):
            current = as_utc(window_start)
        expected.append(current)
        current = current + timedelta(minutes=stop["service_duration_min"])

    for stop, want in zip(ordered, expected, strict=True):
        assert (
            abs((stop["eta"] - want).total_seconds()) < 2
        ), "every downstream ETA must reflect cumulative travel time from the depot"

    etas = [stop["eta"] for stop in ordered]
    assert etas == sorted(etas)

    late_stop_ids = {
        stop["stop_id"]
        for stop in ordered
        if stop["time_window_end"] is not None and stop["eta"] > as_utc(stop["time_window_end"])
    }
    alerted_stop_ids = {
        uuid.UUID(a["context"]["stop_id"]) for a in alerts if a["context"].get("stop_id")
    }
    assert (
        late_stop_ids <= alerted_stop_ids
    ), f"missing late delivery alerts for {late_stop_ids - alerted_stop_ids}"
    for alert in alerts:
        assert alert["severity"] == "warning"
