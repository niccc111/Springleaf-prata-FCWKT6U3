"""Unit tests for schedule and metric computation."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.adapters.base import Leg, TravelMatrix
from app.schemas.geo import GeoPoint
from app.services.route_math import (
    StopPlan,
    build_schedule,
    planning_day_for,
    q3,
    shift_end,
    shift_start,
    utilisation_pct,
)

DEPOT = GeoPoint(latitude=1.279, longitude=103.809)
A = GeoPoint(latitude=1.30, longitude=103.85)
B = GeoPoint(latitude=1.35, longitude=103.90)
START = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def matrix(**legs: Leg) -> TravelMatrix:
    parsed = {tuple(int(c) for c in key.split("_")): leg for key, leg in legs.items()}
    return TravelMatrix(legs=parsed)  # type: ignore[arg-type]


def uniform(n: int, seconds: float, metres: float) -> TravelMatrix:
    return TravelMatrix(
        legs={
            (i, j): Leg(0.0, 0.0) if i == j else Leg(seconds, metres)
            for i in range(n)
            for j in range(n)
        }
    )


def test_empty_route_has_zero_totals():
    schedule = build_schedule(depot=DEPOT, departure_time=START, stops=[], matrix=uniform(1, 0, 0))
    assert schedule.stops == []
    assert schedule.total_distance_km == Decimal("0.000")
    assert schedule.total_duration_min == 0
    assert schedule.return_to_depot_at == START


def test_totals_include_the_return_leg():
    stops = [StopPlan(location=A, address="a", weight_kg=Decimal("10"), service_duration_min=5)]
    schedule = build_schedule(
        depot=DEPOT, departure_time=START, stops=stops, matrix=uniform(2, 600, 5000)
    )
    # Out and back: two legs of 5 km / 10 min each.
    assert schedule.total_distance_km == Decimal("10.000")
    assert schedule.total_duration_min == 20
    assert schedule.stops[0].eta == START + timedelta(minutes=10)
    assert schedule.stops[0].departure == START + timedelta(minutes=15)


def test_eta_waits_for_the_window_to_open():
    window_start = START + timedelta(hours=2)
    stops = [
        StopPlan(
            location=A,
            address="a",
            weight_kg=Decimal("10"),
            time_window_start=window_start,
            service_duration_min=5,
        )
    ]
    schedule = build_schedule(
        depot=DEPOT, departure_time=START, stops=stops, matrix=uniform(2, 600, 5000)
    )
    # Arrival is 08:10 but the window opens at 10:00, so service starts then.
    assert schedule.stops[0].eta == window_start
    assert not schedule.late_stop_indexes


def test_late_stop_is_flagged():
    stops = [
        StopPlan(
            location=A,
            address="a",
            weight_kg=Decimal("10"),
            time_window_end=START + timedelta(minutes=5),
        )
    ]
    schedule = build_schedule(
        depot=DEPOT, departure_time=START, stops=stops, matrix=uniform(2, 600, 5000)
    )
    assert schedule.late_stop_indexes == [0]
    assert schedule.stops[0].late is True


def test_etas_are_monotonic_across_stops():
    stops = [
        StopPlan(location=A, address="a", weight_kg=Decimal("10"), service_duration_min=5),
        StopPlan(location=B, address="b", weight_kg=Decimal("20"), service_duration_min=10),
    ]
    schedule = build_schedule(
        depot=DEPOT, departure_time=START, stops=stops, matrix=uniform(3, 600, 5000)
    )
    etas = [s.eta for s in schedule.stops]
    assert etas == sorted(etas)
    assert schedule.total_weight_kg == Decimal("30.000")


def test_volume_is_none_when_no_stop_declares_one():
    stops = [StopPlan(location=A, address="a", weight_kg=Decimal("10"))]
    schedule = build_schedule(
        depot=DEPOT, departure_time=START, stops=stops, matrix=uniform(2, 60, 100)
    )
    assert schedule.total_volume_m3 is None


def test_volume_sums_when_declared():
    stops = [
        StopPlan(location=A, address="a", weight_kg=Decimal("10"), volume_m3=Decimal("1.5")),
        StopPlan(location=B, address="b", weight_kg=Decimal("10"), volume_m3=Decimal("2.25")),
    ]
    schedule = build_schedule(
        depot=DEPOT, departure_time=START, stops=stops, matrix=uniform(3, 60, 100)
    )
    assert schedule.total_volume_m3 == Decimal("3.750")


@pytest.mark.parametrize(
    ("total", "capacity", "expected"),
    [
        (0, 100, 0.0),
        (45, 100, 45.0),
        (89.94, 100, 89.9),
        (90, 100, 90.0),
        (100, 100, 100.0),
        (150, 100, 150.0),
        (1, 3, 33.3),
    ],
)
def test_utilisation_rounds_to_one_decimal(total, capacity, expected):
    assert utilisation_pct(Decimal(str(total)), Decimal(str(capacity))) == expected


def test_utilisation_returns_none_for_missing_or_zero_capacity():
    assert utilisation_pct(None, 100) is None
    assert utilisation_pct(50, None) is None
    assert utilisation_pct(50, 0) is None


def test_shift_bounds_use_the_planning_day():
    day = planning_day_for(datetime(2026, 6, 1, 23, 30, tzinfo=UTC))
    assert shift_start(day, time(8, 0)) == datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    assert shift_end(day, time(18, 0)) == datetime(2026, 6, 1, 18, 0, tzinfo=UTC)


def test_q3_quantises_to_three_places():
    assert q3(1.23456) == Decimal("1.235")
    assert q3(Decimal("2")) == Decimal("2.000")
