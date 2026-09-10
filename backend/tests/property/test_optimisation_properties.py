"""Property tests for the Optimisation Engine (Tasks 13.6-13.12).

These drive the solver directly (no database) so a full Hypothesis run stays
fast while still covering the hard-constraint invariants.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.adapters.mapping import MockMappingAdapter
from app.services.optimisation_engine import (
    LegCache,
    OptimisationRequest,
    OptOrder,
    OptVehicle,
    solve,
)
from app.services.route_math import as_utc, build_schedule, matrix_nodes_for_route, shift_start
from tests.property.strategies import geo_points

pytestmark = pytest.mark.property

DAY = date(2026, 6, 1)
SHIFT_START = time(8, 0)
SHIFT_END = time(18, 0)


@st.composite
def solver_inputs(draw, *, max_orders: int = 8, max_vehicles: int = 3, with_volume: bool = True):
    order_count = draw(st.integers(min_value=1, max_value=max_orders))
    vehicle_count = draw(st.integers(min_value=1, max_value=max_vehicles))

    vehicles = []
    for _ in range(vehicle_count):
        capacity = draw(st.decimals(min_value=Decimal("50"), max_value=Decimal("2000"), places=1))
        volume = (
            draw(st.decimals(min_value=Decimal("1"), max_value=Decimal("40"), places=1))
            if with_volume and draw(st.booleans())
            else None
        )
        vehicles.append(
            OptVehicle(
                vehicle_id=uuid.uuid4(),
                registration=f"SG{uuid.uuid4().hex[:5].upper()}",
                depot=draw(geo_points()),
                capacity_weight_kg=capacity,
                capacity_volume_m3=volume,
                operating_hours_start=SHIFT_START,
                operating_hours_end=SHIFT_END,
            )
        )

    orders = []
    for index in range(order_count):
        window = None
        if draw(st.booleans()):
            start_hour = draw(st.integers(min_value=8, max_value=14))
            length = draw(st.integers(min_value=1, max_value=8))
            start = datetime.combine(DAY, time(start_hour, 0), tzinfo=UTC)
            window = (start, start + timedelta(hours=length))
        orders.append(
            OptOrder(
                order_id=uuid.uuid4(),
                location=draw(geo_points()),
                address=f"stop {index}",
                weight_kg=draw(
                    st.decimals(min_value=Decimal("1"), max_value=Decimal("600"), places=1)
                ),
                volume_m3=draw(
                    st.decimals(min_value=Decimal("0.1"), max_value=Decimal("6"), places=1)
                ),
                time_window_start=window[0] if window else None,
                time_window_end=window[1] if window else None,
                priority=draw(st.booleans()),
                service_duration_min=draw(st.integers(min_value=0, max_value=20)),
            )
        )
    return orders, vehicles


def _run(orders, vehicles, *, time_limit: float = 4.0):
    """Solve a small instance.

    The budget is deliberately short: these tests assert invariants, not solve
    quality, and the instances are tiny (<= 8 orders, <= 3 vehicles).
    """
    import asyncio

    adapter = MockMappingAdapter()
    points = [v.depot for v in vehicles] + [o.location for o in orders]
    matrix = asyncio.get_event_loop().run_until_complete(adapter.travel_matrix(points))
    legs = LegCache()
    legs.ingest(points, matrix)
    request = OptimisationRequest(
        orders=orders, vehicles=vehicles, planning_day=DAY, time_limit_seconds=time_limit
    )
    return solve(request, legs), legs


def _schedule(route, vehicles_by_id, legs):
    vehicle = vehicles_by_id[route.vehicle_id]
    waypoints = matrix_nodes_for_route(vehicle.depot, route.stops)
    return build_schedule(
        depot=vehicle.depot,
        departure_time=shift_start(DAY, vehicle.operating_hours_start),
        stops=route.stops,
        matrix=legs.as_route_matrix(waypoints),
    )


# Feature: route-optimisation-engine, Property 12: Weight capacity invariant
# Feature: route-optimisation-engine, Property 13: Volume capacity invariant
@given(inputs=solver_inputs())
def test_property_12_and_13_capacity_invariants(event_loop_instance, inputs):
    """No produced route exceeds its vehicle's weight capacity, nor its volume
    capacity where one is defined.

    Validates: Requirements 5.2, 5.3
    """
    orders, vehicles = inputs
    result, legs = _run(orders, vehicles)
    by_id = {v.vehicle_id: v for v in vehicles}

    for route in result.routes:
        vehicle = by_id[route.vehicle_id]
        total_weight = sum(stop.weight_kg for stop in route.stops)
        assert total_weight <= vehicle.capacity_weight_kg, (
            f"route weight {total_weight} exceeds capacity {vehicle.capacity_weight_kg}"
        )
        if vehicle.capacity_volume_m3 is not None:
            total_volume = sum((stop.volume_m3 or Decimal("0")) for stop in route.stops)
            assert total_volume <= vehicle.capacity_volume_m3, (
                f"route volume {total_volume} exceeds capacity {vehicle.capacity_volume_m3}"
            )


# Feature: route-optimisation-engine, Property 14: Time window compliance invariant
@given(inputs=solver_inputs())
def test_property_14_time_window_compliance(event_loop_instance, inputs):
    """Every scheduled stop with a time window is serviced inside it; any order
    that cannot be is reported infeasible rather than scheduled late.

    Validates: Requirements 5.4
    """
    orders, vehicles = inputs
    result, legs = _run(orders, vehicles)
    by_id = {v.vehicle_id: v for v in vehicles}
    infeasible_ids = {entry.order_id for entry in result.infeasible}

    for route in result.routes:
        schedule = _schedule(route, by_id, legs)
        for scheduled in schedule.stops:
            plan = scheduled.plan
            if plan.time_window_end is None:
                continue
            within = as_utc(scheduled.eta) <= as_utc(plan.time_window_end)
            if not within:
                assert set(plan.order_ids) <= infeasible_ids, (
                    "a stop outside its window must have an Impossible Order Alert raised "
                    f"for its orders; eta={scheduled.eta} end={plan.time_window_end}"
                )
            if plan.time_window_start is not None and within:
                assert as_utc(scheduled.eta) >= as_utc(plan.time_window_start)


# Feature: route-optimisation-engine, Property 15: Priority ordering invariant
@given(inputs=solver_inputs(max_orders=7, max_vehicles=2))
def test_property_15_priority_ordering(event_loop_instance, inputs):
    """On any route carrying both priority and standard stops, no standard stop
    precedes a priority stop unless a hard constraint forced the relaxation.

    Validates: Requirements 6.1
    """
    orders, vehicles = inputs
    result, _ = _run(orders, vehicles)

    for route in result.routes:
        if route.priority_relaxed:
            # The hard-priority model was infeasible: a capacity or time-window
            # constraint required the inversion, which the property allows.
            continue
        seen_standard = False
        for stop in route.stops:
            if stop.has_priority_order:
                assert not seen_standard, (
                    "a standard stop preceded a priority stop on a route that was not "
                    "flagged as priority-relaxed"
                )
            else:
                seen_standard = True


# Feature: route-optimisation-engine, Property 11: Optimisation run produces complete assignment coverage
@given(inputs=solver_inputs())
def test_property_11_complete_coverage(event_loop_instance, inputs):
    """Every submitted order ends up either assigned to a route or reported as
    infeasible — none are silently dropped.

    Validates: Requirements 5.1, 5.7
    """
    orders, vehicles = inputs
    result, _ = _run(orders, vehicles)

    assigned = {
        order_id for route in result.routes for stop in route.stops for order_id in stop.order_ids
    }
    reported = assigned | {e.order_id for e in result.infeasible} | set(
        result.unassigned_order_ids
    )
    submitted = {o.order_id for o in orders}
    assert submitted <= reported, f"orders silently unprocessed: {submitted - reported}"

    # Anything not assigned must carry a reason the dispatcher can act on.
    for order_id in submitted - assigned:
        assert order_id in {e.order_id for e in result.infeasible} or order_id in set(
            result.unassigned_order_ids
        )


@given(inputs=solver_inputs(max_orders=5, max_vehicles=2))
def test_infeasible_orders_carry_a_reason(event_loop_instance, inputs):
    """Requirement 5.7 — every infeasibility names why the order cannot be served."""
    orders, vehicles = inputs
    result, _ = _run(orders, vehicles)
    for entry in result.infeasible:
        assert entry.reason and len(entry.reason) > 10


def test_no_vehicles_aborts(event_loop_instance):
    """Requirement 5.10 — with no available vehicles the run aborts with no routes."""
    from app.schemas.geo import GeoPoint

    orders = [
        OptOrder(
            order_id=uuid.uuid4(),
            location=GeoPoint(latitude=1.30, longitude=103.85),
            address="somewhere",
            weight_kg=Decimal("10"),
        )
    ]
    result, _ = _run(orders, [])
    assert result.routes == []
    assert result.solver_status == "NO_VEHICLES"
    assert set(result.unassigned_order_ids) == {o.order_id for o in orders}
