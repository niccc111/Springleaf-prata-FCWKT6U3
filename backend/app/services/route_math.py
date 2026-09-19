"""Schedule and metric computation shared by the Route Service and the solver.

Central invariants (Properties 16, 18, 20):

* ETAs are monotonically non-decreasing along the stop sequence.
* An arrival earlier than ``time_window_start`` waits at the stop, so the
  recorded ETA is the service start time and never precedes the window.
* ``total_weight_kg`` / ``total_volume_m3`` always equal the sum over the
  orders assigned to the route.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.adapters.base import TravelMatrix
from app.core.config import settings
from app.schemas.geo import GeoPoint

THREE_DP = Decimal("0.001")


def q3(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(THREE_DP, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class StopPlan:
    """A planned stop, independent of persistence."""

    location: GeoPoint
    address: str
    order_ids: list[uuid.UUID] = field(default_factory=list)
    weight_kg: Decimal = Decimal("0")
    volume_m3: Decimal | None = None
    service_duration_min: int = 0
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    has_priority_order: bool = False
    stop_id: uuid.UUID | None = None


@dataclass(slots=True)
class ScheduledStop:
    plan: StopPlan
    sequence_number: int
    eta: datetime
    departure: datetime
    distance_from_previous_km: Decimal
    travel_time_from_previous_min: int
    late: bool


@dataclass(slots=True)
class RouteSchedule:
    stops: list[ScheduledStop]
    total_distance_km: Decimal
    total_duration_min: int
    total_weight_kg: Decimal
    total_volume_m3: Decimal | None
    return_to_depot_at: datetime
    late_stop_indexes: list[int] = field(default_factory=list)

    @property
    def has_late_stops(self) -> bool:
        return bool(self.late_stop_indexes)


def as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    )


def shift_start(planning_day: date, operating_hours_start: time) -> datetime:
    """Vehicle shift start on the planning day, returned as a UTC instant.

    Operating hours are stored as wall-clock times and interpreted in the
    configured business timezone (``settings.business_timezone``, default
    Asia/Singapore), then converted to UTC for the solver. So an ``08:00``
    shift start means 08:00 local time, not 08:00 UTC.
    """
    local = datetime.combine(planning_day, operating_hours_start, tzinfo=settings.business_tzinfo)
    return local.astimezone(UTC)


def shift_end(planning_day: date, operating_hours_end: time) -> datetime:
    local = datetime.combine(planning_day, operating_hours_end, tzinfo=settings.business_tzinfo)
    return local.astimezone(UTC)


def build_schedule(
    *,
    depot: GeoPoint,
    departure_time: datetime,
    stops: list[StopPlan],
    matrix: TravelMatrix,
    node_of: dict[int, int] | None = None,
    depot_node: int = 0,
) -> RouteSchedule:
    """Compute ETAs, per-leg distances, and route totals.

    ``matrix`` is indexed by node ids; ``node_of`` maps the position of each
    stop in ``stops`` to its node id. When omitted, stop *i* maps to node
    ``i + 1`` and the depot to node 0 — the layout produced by
    :func:`matrix_nodes_for_route`.
    """
    del depot  # the depot's position is carried by ``depot_node`` in the matrix
    departure_time = as_utc(departure_time)
    node_of = node_of or {i: i + 1 for i in range(len(stops))}

    scheduled: list[ScheduledStop] = []
    late_indexes: list[int] = []
    total_distance_m = 0.0
    total_travel_s = 0.0
    total_weight = Decimal("0")
    total_volume: Decimal | None = None

    current_node = depot_node
    current_time = departure_time

    for index, plan in enumerate(stops):
        target_node = node_of[index]
        travel_s = matrix.duration_seconds(current_node, target_node)
        distance_m = matrix.distance_metres(current_node, target_node)

        arrival = current_time + timedelta(seconds=travel_s)
        # Waiting is allowed: arriving early means servicing at window open.
        eta = arrival
        if plan.time_window_start is not None:
            window_start = as_utc(plan.time_window_start)
            if eta < window_start:
                eta = window_start

        late = (
            plan.time_window_end is not None and eta > as_utc(plan.time_window_end)
        )
        if late:
            late_indexes.append(index)

        departure = eta + timedelta(minutes=plan.service_duration_min)

        scheduled.append(
            ScheduledStop(
                plan=plan,
                sequence_number=index + 1,
                eta=eta,
                departure=departure,
                distance_from_previous_km=q3(distance_m / 1000.0),
                travel_time_from_previous_min=int(round(travel_s / 60.0)),
                late=late,
            )
        )

        total_distance_m += distance_m
        total_travel_s += travel_s
        total_weight += plan.weight_kg
        if plan.volume_m3 is not None:
            total_volume = (total_volume or Decimal("0")) + plan.volume_m3

        current_node = target_node
        current_time = departure

    # Return leg to the depot.
    if stops:
        return_s = matrix.duration_seconds(current_node, depot_node)
        return_m = matrix.distance_metres(current_node, depot_node)
        total_distance_m += return_m
        total_travel_s += return_s
        return_at = current_time + timedelta(seconds=return_s)
    else:
        return_at = departure_time

    return RouteSchedule(
        stops=scheduled,
        total_distance_km=q3(total_distance_m / 1000.0),
        total_duration_min=int(round(total_travel_s / 60.0)),
        total_weight_kg=q3(total_weight),
        total_volume_m3=q3(total_volume) if total_volume is not None else None,
        return_to_depot_at=return_at,
        late_stop_indexes=late_indexes,
    )


def matrix_nodes_for_route(depot: GeoPoint, stops: list[StopPlan]) -> list[GeoPoint]:
    """Waypoint list whose index 0 is the depot and 1..n are the stops."""
    return [depot, *[stop.location for stop in stops]]


def utilisation_pct(total: Decimal | float | None, capacity: Decimal | float | None) -> float | None:
    """Load utilisation as a percentage rounded to 1 d.p. (Property 18)."""
    if total is None or capacity is None:
        return None
    capacity_f = float(capacity)
    if capacity_f <= 0:
        return None
    return float(
        (Decimal(str(float(total))) / Decimal(str(capacity_f)) * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    )


def planning_day_for(reference: datetime | None = None) -> date:
    """The calendar day to plan, in the configured business timezone.

    Using the local date keeps "today" aligned with the dispatcher's day: at
    07:00 SGT it is still the same local date even though UTC may read the
    previous day.
    """
    instant = as_utc(reference or datetime.now(UTC))
    return instant.astimezone(settings.business_tzinfo).date()
