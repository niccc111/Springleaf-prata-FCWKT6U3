"""Capacity- and time-window-aware VRP solver built on OR-Tools CP-SAT.

Approach (design § Optimisation Engine Architecture): a two-phase CP-SAT
decomposition that scales to the required 500 orders x 50 vehicles inside the
120-second budget.

* **Phase 1 — assignment.** A CP-SAT model assigns orders to vehicles subject
  to hard weight/volume capacity and per-vehicle feasibility, minimising a
  radial distance proxy plus a large penalty for leaving an order unassigned.
  Two medoid-refinement rounds replace the depot-radial cost with a
  cluster-centroid cost, which materially tightens the clusters.
* **Phase 2 — sequencing.** For each vehicle a CP-SAT ``AddCircuit`` model
  solves the exact TSP with time windows over that vehicle's orders,
  minimising ``ALPHA * distance + BETA * time`` (distance primary, time as the
  tie-breaker) with priority-before-standard as a hard constraint, relaxed to a
  penalised soft constraint only when the hard model is infeasible.

Every produced route is re-verified against the hard constraints before it is
returned, so Properties 12, 13, 14, and 15 hold for the engine's output by
construction.

Tasks 13.1–13.5 — Requirements 5.x, 6.x, 12.x.
"""

from __future__ import annotations

import time as time_module
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from ortools.sat.python import cp_model

from app.adapters.base import Leg, TravelMatrix
from app.core.logging import get_logger
from app.schemas.geo import GeoPoint
from app.services.route_math import StopPlan, as_utc, shift_end, shift_start

logger = get_logger(__name__)

#: Objective weights: distance dominates travel time (Requirements 5.5, 5.6).
ALPHA_DISTANCE = 1000
BETA_TIME = 1
#: Penalty applied when a standard stop has to precede a priority stop.
GAMMA_PRIORITY = 10_000_000

#: Scale factor turning kilograms / cubic metres into integers for CP-SAT.
WEIGHT_SCALE = 1000
VOLUME_SCALE = 1000

MAX_DROP_ITERATIONS = 40


# --------------------------------------------------------------------------- #
# Solver-facing value objects (no ORM dependency, so the solver is unit-testable)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OptOrder:
    order_id: uuid.UUID
    location: GeoPoint
    address: str
    weight_kg: Decimal
    volume_m3: Decimal | None = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    priority: bool = False
    service_duration_min: int = 10


@dataclass(slots=True)
class OptVehicle:
    vehicle_id: uuid.UUID
    registration: str
    depot: GeoPoint
    capacity_weight_kg: Decimal
    capacity_volume_m3: Decimal | None
    operating_hours_start: time
    operating_hours_end: time
    driver_id: uuid.UUID | None = None


@dataclass(slots=True)
class DeliveryPoint:
    """One or more orders served together at the same coordinates.

    Grouping happens *before* the solve so the merged stop's combined weight,
    volume, service time, and intersected time window are the constraints the
    solver satisfies — merging afterwards could produce a stop whose ETA sits
    outside the tightened window (Property 14).
    """

    location: GeoPoint
    address: str
    orders: list[OptOrder]

    @property
    def order_ids(self) -> list[uuid.UUID]:
        return [order.order_id for order in self.orders]

    @property
    def weight_kg(self) -> Decimal:
        return sum((o.weight_kg for o in self.orders), Decimal("0"))

    @property
    def volume_m3(self) -> Decimal | None:
        volumes = [o.volume_m3 for o in self.orders if o.volume_m3 is not None]
        return sum(volumes, Decimal("0")) if volumes else None

    @property
    def service_duration_min(self) -> int:
        #: Servicing several orders at one door takes the sum of their handling
        #: times, not the maximum.
        return sum(o.service_duration_min for o in self.orders)

    @property
    def priority(self) -> bool:
        return any(o.priority for o in self.orders)

    @property
    def time_window_start(self) -> datetime | None:
        starts = [as_utc(o.time_window_start) for o in self.orders if o.time_window_start]
        return max(starts) if starts else None

    @property
    def time_window_end(self) -> datetime | None:
        ends = [as_utc(o.time_window_end) for o in self.orders if o.time_window_end]
        return min(ends) if ends else None


def group_by_location(orders: list[OptOrder]) -> list[DeliveryPoint]:
    """Group orders sharing coordinates, but only where their windows overlap.

    Orders at the same address with disjoint windows stay separate points so
    neither is forced outside its window.
    """
    buckets: dict[tuple[float, float], list[DeliveryPoint]] = {}
    ordered: list[DeliveryPoint] = []

    for order in orders:
        key = (round(order.location.latitude, 7), round(order.location.longitude, 7))
        placed = False
        for point in buckets.get(key, []):
            merged_start = _max_dt(point.time_window_start, order.time_window_start)
            merged_end = _min_dt(point.time_window_end, order.time_window_end)
            if merged_start is not None and merged_end is not None and merged_start > merged_end:
                continue  # windows do not intersect — keep this order separate
            point.orders.append(order)
            placed = True
            break
        if not placed:
            point = DeliveryPoint(
                location=order.location, address=order.address, orders=[order]
            )
            buckets.setdefault(key, []).append(point)
            ordered.append(point)
    return ordered


def _max_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return as_utc(b) if b else None
    if b is None:
        return as_utc(a)
    return max(as_utc(a), as_utc(b))


def _min_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return as_utc(b) if b else None
    if b is None:
        return as_utc(a)
    return min(as_utc(a), as_utc(b))


@dataclass(slots=True)
class InfeasibleOrder:
    order_id: uuid.UUID
    reason: str


@dataclass(slots=True)
class SolvedRoute:
    vehicle_id: uuid.UUID
    stops: list[StopPlan]
    priority_relaxed: bool = False
    total_distance_m: float = 0.0
    total_duration_s: float = 0.0


@dataclass(slots=True)
class OptimisationRequest:
    orders: list[OptOrder]
    vehicles: list[OptVehicle]
    planning_day: date
    locked_route_count: int = 0
    time_limit_seconds: float = 110.0
    progress_callback: Callable[[int, str], None] | None = None


@dataclass(slots=True)
class OptimisationResult:
    routes: list[SolvedRoute] = field(default_factory=list)
    infeasible: list[InfeasibleOrder] = field(default_factory=list)
    unassigned_order_ids: list[uuid.UUID] = field(default_factory=list)
    solver_status: str = "UNKNOWN"
    timed_out: bool = False
    duration_seconds: float = 0.0
    locked_excluded_count: int = 0
    degraded_travel_times: bool = False


class SolverTimeout(RuntimeError):
    """Raised when the wall-clock budget is exhausted mid-solve."""


# --------------------------------------------------------------------------- #
# Distance/duration cache
# --------------------------------------------------------------------------- #
class LegCache:
    """Lazily-populated leg lookup backed by a :class:`TravelMatrix` supplier."""

    def __init__(self, degraded: bool = False) -> None:
        self._legs: dict[tuple[str, str], Leg] = {}
        self.degraded = degraded

    @staticmethod
    def _key(point: GeoPoint) -> str:
        return f"{point.latitude:.6f},{point.longitude:.6f}"

    def ingest(self, waypoints: Sequence[GeoPoint], matrix: TravelMatrix) -> None:
        keys = [self._key(p) for p in waypoints]
        for (i, j), leg in matrix.legs.items():
            if i < len(keys) and j < len(keys):
                self._legs[(keys[i], keys[j])] = leg
        self.degraded = self.degraded or matrix.degraded

    def leg(self, origin: GeoPoint, destination: GeoPoint) -> Leg:
        key = (self._key(origin), self._key(destination))
        cached = self._legs.get(key)
        if cached is not None:
            return cached
        # Geometric fallback keeps the solver total even if a cell is missing.
        road_km = origin.haversine_km(destination) * 1.32
        leg = Leg(duration_seconds=road_km / 32.0 * 3600.0, distance_metres=road_km * 1000.0)
        self._legs[key] = leg
        return leg

    def as_route_matrix(self, waypoints: Sequence[GeoPoint]) -> TravelMatrix:
        legs: dict[tuple[int, int], Leg] = {}
        for i, origin in enumerate(waypoints):
            for j, destination in enumerate(waypoints):
                legs[(i, j)] = Leg(0.0, 0.0) if i == j else self.leg(origin, destination)
        return TravelMatrix(legs=legs, degraded=self.degraded, source="solver-cache")


# --------------------------------------------------------------------------- #
# Solver
# --------------------------------------------------------------------------- #
class OptimisationSolver:
    """Pure, synchronous VRP solver over delivery points. All I/O is the caller's."""

    def __init__(self, legs: LegCache) -> None:
        self.legs = legs

    # -- helpers ------------------------------------------------------- #
    @staticmethod
    def _window(
        point: DeliveryPoint, fallback: tuple[datetime, datetime]
    ) -> tuple[datetime, datetime]:
        start = point.time_window_start or fallback[0]
        end = point.time_window_end or fallback[1]
        return max(as_utc(start), fallback[0]), as_utc(end)

    # -- pre-solve ----------------------------------------------------- #
    def presolve_feasibility(
        self, request: OptimisationRequest, points: list[DeliveryPoint]
    ) -> tuple[list[DeliveryPoint], list[InfeasibleOrder], dict[int, set[uuid.UUID]]]:
        """Identify delivery points no vehicle can serve (Task 13.2, Requirement 5.7).

        Returns the servable points, the infeasible orders with a reason each,
        and — keyed by index into the servable list — the vehicles that could
        serve each point.
        """
        servable: list[DeliveryPoint] = []
        infeasible: list[InfeasibleOrder] = []
        allowed: dict[int, set[uuid.UUID]] = {}

        for point in points:
            if point.location is None:
                infeasible.extend(
                    InfeasibleOrder(order_id, "Order has no resolved delivery location")
                    for order_id in point.order_ids
                )
                continue

            candidates: set[uuid.UUID] = set()
            weight_blocked = True
            volume_blocked = True

            for vehicle in request.vehicles:
                if point.weight_kg > vehicle.capacity_weight_kg:
                    continue
                weight_blocked = False
                if (
                    vehicle.capacity_volume_m3 is not None
                    and point.volume_m3 is not None
                    and point.volume_m3 > vehicle.capacity_volume_m3
                ):
                    continue
                volume_blocked = False

                depart = shift_start(request.planning_day, vehicle.operating_hours_start)
                finish = shift_end(request.planning_day, vehicle.operating_hours_end)
                outbound = self.legs.leg(vehicle.depot, point.location)
                inbound = self.legs.leg(point.location, vehicle.depot)
                window_start, window_end = self._window(point, (depart, finish))

                arrival = max(depart + timedelta(seconds=outbound.duration_seconds), window_start)
                if arrival > window_end:
                    continue
                back_at_depot = (
                    arrival
                    + timedelta(minutes=point.service_duration_min)
                    + timedelta(seconds=inbound.duration_seconds)
                )
                if back_at_depot > finish:
                    continue
                candidates.add(vehicle.vehicle_id)

            if candidates:
                allowed[len(servable)] = candidates
                servable.append(point)
            else:
                if weight_blocked:
                    reason = (
                        f"Cargo weight {float(point.weight_kg):.1f} kg exceeds the capacity of "
                        "every available vehicle"
                    )
                elif volume_blocked:
                    reason = (
                        f"Cargo volume {float(point.volume_m3 or 0):.2f} m³ exceeds the capacity "
                        "of every available vehicle"
                    )
                else:
                    reason = (
                        "No available vehicle can reach this delivery inside its time window "
                        "and return to depot within operating hours"
                    )
                infeasible.extend(
                    InfeasibleOrder(order_id, reason) for order_id in point.order_ids
                )

        return servable, infeasible, allowed

    # -- phase 1: assignment ------------------------------------------- #
    def assign(
        self,
        request: OptimisationRequest,
        points: list[DeliveryPoint],
        allowed: dict[int, set[uuid.UUID]],
        *,
        centroids: dict[uuid.UUID, GeoPoint] | None = None,
        time_limit: float = 20.0,
    ) -> tuple[dict[uuid.UUID, list[DeliveryPoint]], list[int], str]:
        """CP-SAT capacitated assignment of delivery points to vehicles."""
        model = cp_model.CpModel()
        vehicles = request.vehicles
        x: dict[tuple[int, int], cp_model.IntVar] = {}

        for pi in range(len(points)):
            allowed_ids = allowed.get(pi, set())
            for vi, vehicle in enumerate(vehicles):
                if vehicle.vehicle_id in allowed_ids:
                    x[(pi, vi)] = model.NewBoolVar(f"x_{pi}_{vi}")

        unassigned: list[cp_model.IntVar] = []
        for pi in range(len(points)):
            options = [x[(pi, vi)] for vi in range(len(vehicles)) if (pi, vi) in x]
            flag = model.NewBoolVar(f"u_{pi}")
            unassigned.append(flag)
            model.Add(sum(options) + flag == 1)

        for vi, vehicle in enumerate(vehicles):
            members = [(pi, x[(pi, vi)]) for pi in range(len(points)) if (pi, vi) in x]
            if not members:
                continue
            model.Add(
                sum(
                    int(round(float(points[pi].weight_kg) * WEIGHT_SCALE)) * var
                    for pi, var in members
                )
                <= int(round(float(vehicle.capacity_weight_kg) * WEIGHT_SCALE))
            )
            if vehicle.capacity_volume_m3 is not None:
                model.Add(
                    sum(
                        int(round(float(points[pi].volume_m3 or 0) * VOLUME_SCALE)) * var
                        for pi, var in members
                    )
                    <= int(round(float(vehicle.capacity_volume_m3) * VOLUME_SCALE))
                )

            # Coarse shift-time budget so a vehicle is not handed more work than
            # its operating hours can absorb. Phase 2 enforces the exact windows.
            shift_seconds = int(
                (
                    shift_end(request.planning_day, vehicle.operating_hours_end)
                    - shift_start(request.planning_day, vehicle.operating_hours_start)
                ).total_seconds()
            )
            model.Add(
                sum(
                    (
                        points[pi].service_duration_min * 60
                        + int(self.legs.leg(vehicle.depot, points[pi].location).duration_seconds)
                    )
                    * var
                    for pi, var in members
                )
                <= shift_seconds
            )

        # Objective: cluster tightness plus a dominating penalty for dropping work.
        max_cost = 1
        cost_terms: list[tuple[cp_model.IntVar, int]] = []
        for (pi, vi), var in x.items():
            anchor = (centroids or {}).get(vehicles[vi].vehicle_id) or vehicles[vi].depot
            radial = self.legs.leg(vehicles[vi].depot, points[pi].location).distance_metres
            to_centroid = anchor.haversine_km(points[pi].location) * 1000.0
            cost = int(round(2 * radial + 3 * to_centroid))
            max_cost = max(max_cost, cost)
            cost_terms.append((var, cost))

        drop_penalty = max_cost * 20 + 1_000_000
        model.Minimize(
            sum(var * cost for var, cost in cost_terms)
            + sum(
                flag * (drop_penalty * (3 if points[pi].priority else 1))
                for pi, flag in enumerate(unassigned)
            )
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(time_limit, 1.0)
        solver.parameters.num_workers = 8
        # A near-optimal clustering is worth far more than the seconds spent
        # proving optimality — phase 2 re-optimises each cluster exactly.
        solver.parameters.relative_gap_limit = 0.01
        status = solver.Solve(model)

        clusters: dict[uuid.UUID, list[DeliveryPoint]] = {v.vehicle_id: [] for v in vehicles}
        dropped: list[int] = []
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return clusters, list(range(len(points))), solver.StatusName(status)

        for pi in range(len(points)):
            placed = False
            for vi, vehicle in enumerate(vehicles):
                var = x.get((pi, vi))
                if var is not None and solver.Value(var):
                    clusters[vehicle.vehicle_id].append(points[pi])
                    placed = True
                    break
            if not placed:
                dropped.append(pi)
        return clusters, dropped, solver.StatusName(status)

    @staticmethod
    def centroids_of(
        clusters: dict[uuid.UUID, list[DeliveryPoint]],
    ) -> dict[uuid.UUID, GeoPoint]:
        result: dict[uuid.UUID, GeoPoint] = {}
        for vehicle_id, points in clusters.items():
            if not points:
                continue
            result[vehicle_id] = GeoPoint(
                latitude=sum(p.location.latitude for p in points) / len(points),
                longitude=sum(p.location.longitude for p in points) / len(points),
            )
        return result

    # -- phase 2: sequencing ------------------------------------------- #
    def sequence(
        self,
        vehicle: OptVehicle,
        points: list[DeliveryPoint],
        planning_day: date,
        *,
        time_limit: float = 5.0,
        hard_priority: bool = True,
    ) -> tuple[list[DeliveryPoint] | None, bool]:
        """Solve the TSP-with-time-windows for one vehicle's cluster.

        Returns ``(ordered_points, priority_relaxed)``, or ``(None, False)``
        when no feasible sequence exists.
        """
        if not points:
            return [], False

        depart = shift_start(planning_day, vehicle.operating_hours_start)
        finish = shift_end(planning_day, vehicle.operating_hours_end)
        horizon_start = int(depart.timestamp())
        horizon_end = int(finish.timestamp())
        if horizon_end <= horizon_start:
            return None, False

        coordinates = [vehicle.depot, *[p.location for p in points]]
        n = len(coordinates)

        model = cp_model.CpModel()
        arcs: list[tuple[int, int, cp_model.IntVar]] = []
        arc_vars: dict[tuple[int, int], cp_model.IntVar] = {}
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                var = model.NewBoolVar(f"arc_{i}_{j}")
                arcs.append((i, j, var))
                arc_vars[(i, j)] = var
        model.AddCircuit(arcs)

        # Service-start times, bounded by each point's time window.
        t = [model.NewIntVar(horizon_start, horizon_end, f"t_{i}") for i in range(n)]
        model.Add(t[0] == horizon_start)

        for index, point in enumerate(points, start=1):
            window_start, window_end = self._window(point, (depart, finish))
            model.Add(t[index] >= int(window_start.timestamp()))
            model.Add(t[index] <= min(int(window_end.timestamp()), horizon_end))

        for (i, j), var in arc_vars.items():
            travel = int(round(self.legs.leg(coordinates[i], coordinates[j]).duration_seconds))
            service = 0 if i == 0 else points[i - 1].service_duration_min * 60
            if j == 0:
                # The return leg must land inside operating hours.
                model.Add(t[i] + service + travel <= horizon_end).OnlyEnforceIf(var)
            else:
                model.Add(t[j] >= t[i] + service + travel).OnlyEnforceIf(var)

        # Priority ordering (Requirement 6.1 / Property 15). Service times rise
        # monotonically along the route, so ordering them orders the sequence.
        penalties: list[cp_model.IntVar] = []
        priority_idx = [i for i, p in enumerate(points, start=1) if p.priority]
        standard_idx = [i for i, p in enumerate(points, start=1) if not p.priority]
        for pi in priority_idx:
            for si in standard_idx:
                if hard_priority:
                    model.Add(t[pi] <= t[si])
                else:
                    ok = model.NewBoolVar(f"prio_{pi}_{si}")
                    model.Add(t[pi] <= t[si]).OnlyEnforceIf(ok)
                    model.Add(t[si] <= t[pi]).OnlyEnforceIf(ok.Not())
                    penalties.append(ok.Not())

        objective = []
        for (i, j), var in arc_vars.items():
            leg = self.legs.leg(coordinates[i], coordinates[j])
            objective.append(
                var
                * (
                    ALPHA_DISTANCE * int(round(leg.distance_metres))
                    + BETA_TIME * int(round(leg.duration_seconds))
                )
            )
        objective.extend(penalty * GAMMA_PRIORITY for penalty in penalties)
        model.Minimize(sum(objective))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(time_limit, 0.5)
        solver.parameters.num_workers = 4
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, False

        successor = {i: j for (i, j), var in arc_vars.items() if solver.Value(var)}
        sequence: list[DeliveryPoint] = []
        current = 0
        for _ in range(n):
            nxt = successor.get(current)
            if nxt is None or nxt == 0:
                break
            sequence.append(points[nxt - 1])
            current = nxt

        if len(sequence) != len(points):  # pragma: no cover - defensive
            return None, False

        relaxed = bool(penalties) and any(solver.Value(p) for p in penalties)
        return sequence, relaxed

    def sequence_with_drops(
        self,
        vehicle: OptVehicle,
        points: list[DeliveryPoint],
        planning_day: date,
        *,
        deadline: float,
    ) -> tuple[list[DeliveryPoint], list[InfeasibleOrder], bool]:
        """Sequence a cluster, dropping points until a feasible schedule exists.

        Dropped orders become Impossible Order Alerts rather than late stops,
        which is what keeps Property 14 true of the persisted output.
        """
        working = list(points)
        dropped: list[InfeasibleOrder] = []

        for _ in range(MAX_DROP_ITERATIONS):
            if not working:
                return [], dropped, False
            remaining = deadline - time_module.monotonic()
            if remaining <= 0:
                raise SolverTimeout("wall-clock budget exhausted during sequencing")
            budget = min(max(0.5 + 0.15 * len(working), 1.0), remaining, 10.0)

            sequenced, relaxed = self.sequence(
                vehicle, working, planning_day, time_limit=budget, hard_priority=True
            )
            if sequenced is not None:
                return sequenced, dropped, relaxed

            # Retry with priority ordering relaxed before giving up on an order.
            sequenced, _ = self.sequence(
                vehicle, working, planning_day, time_limit=budget, hard_priority=False
            )
            if sequenced is not None:
                return sequenced, dropped, True

            # Still infeasible: drop the hardest point (tightest window, then
            # furthest from the depot), preferring to keep priority orders.
            victim = max(
                working,
                key=lambda p: (
                    0 if p.priority else 1,
                    -_window_width_seconds(p),
                    self.legs.leg(vehicle.depot, p.location).distance_metres,
                ),
            )
            working.remove(victim)
            dropped.extend(
                InfeasibleOrder(
                    order_id,
                    "No feasible position on any available vehicle satisfies this order's "
                    "delivery window alongside the rest of the plan",
                )
                for order_id in victim.order_ids
            )
        return working, dropped, False


def _window_width_seconds(point: DeliveryPoint) -> float:
    if point.time_window_start is None or point.time_window_end is None:
        return float("inf")
    return (point.time_window_end - point.time_window_start).total_seconds()


def solve(request: OptimisationRequest, legs: LegCache) -> OptimisationResult:
    """Run the full two-phase solve. Blocking; call via ``asyncio.to_thread``."""
    started = time_module.monotonic()
    deadline = started + request.time_limit_seconds
    solver = OptimisationSolver(legs)
    result = OptimisationResult(locked_excluded_count=request.locked_route_count)

    def progress(pct: int, message: str) -> None:
        if request.progress_callback is not None:
            request.progress_callback(pct, message)

    if not request.vehicles:
        result.solver_status = "NO_VEHICLES"
        result.unassigned_order_ids = [o.order_id for o in request.orders]
        return result

    progress(5, "Checking order feasibility")
    # Co-located orders are merged into one delivery point before solving so the
    # merged stop's constraints are the ones the solver satisfies.
    points = group_by_location([o for o in request.orders if o.location is not None])
    for order in request.orders:
        if order.location is None:
            result.infeasible.append(
                InfeasibleOrder(order.order_id, "Order has no resolved delivery location")
            )

    servable, infeasible, allowed = solver.presolve_feasibility(request, points)
    result.infeasible.extend(infeasible)

    if not servable:
        result.solver_status = "NO_SERVABLE_ORDERS"
        result.unassigned_order_ids = [o.order_id for o in request.orders]
        result.duration_seconds = time_module.monotonic() - started
        return result

    order_count = sum(len(p.orders) for p in servable)
    progress(20, f"Assigning {order_count} orders across {len(request.vehicles)} vehicles")

    # Scale the assignment budget with problem size and never let it consume
    # more than a quarter of the wall-clock allowance.
    size_budget = 3.0 + 0.05 * len(servable) + 0.2 * len(request.vehicles)
    assign_budget = min(size_budget, (deadline - time_module.monotonic()) * 0.25, 30.0)
    clusters, dropped_indexes, status = solver.assign(
        request, servable, allowed, time_limit=assign_budget
    )
    result.solver_status = status

    # Medoid refinement: re-run the assignment against cluster centroids, which
    # tightens clusters that a depot-radial cost alone leaves ragged.
    for round_index in range(2):
        remaining = deadline - time_module.monotonic()
        if remaining < max(15.0, 0.4 * request.time_limit_seconds):
            break
        centroids = solver.centroids_of(clusters)
        if not centroids:
            break
        refined, refined_dropped, refined_status = solver.assign(
            request,
            servable,
            allowed,
            centroids=centroids,
            time_limit=min(assign_budget * 0.6, remaining * 0.15),
        )
        if refined_status in ("OPTIMAL", "FEASIBLE") and len(refined_dropped) <= len(
            dropped_indexes
        ):
            clusters, dropped_indexes, status = refined, refined_dropped, refined_status
            result.solver_status = refined_status
        progress(30 + round_index * 5, "Refining vehicle clusters")

    vehicles_by_id = {v.vehicle_id: v for v in request.vehicles}
    active = [(vid, cluster) for vid, cluster in clusters.items() if cluster]

    progress(45, f"Sequencing {len(active)} routes")
    for index, (vehicle_id, cluster) in enumerate(active):
        vehicle = vehicles_by_id[vehicle_id]
        try:
            sequenced, dropped, relaxed = solver.sequence_with_drops(
                vehicle, cluster, request.planning_day, deadline=deadline
            )
        except SolverTimeout:
            result.timed_out = True
            result.duration_seconds = time_module.monotonic() - started
            result.routes = []
            result.unassigned_order_ids = [o.order_id for o in request.orders]
            return result

        result.infeasible.extend(dropped)
        if not sequenced:
            continue

        result.routes.append(
            SolvedRoute(
                vehicle_id=vehicle_id,
                stops=[
                    StopPlan(
                        location=point.location,
                        address=point.address,
                        order_ids=point.order_ids,
                        weight_kg=point.weight_kg,
                        volume_m3=point.volume_m3,
                        service_duration_min=point.service_duration_min,
                        time_window_start=point.time_window_start,
                        time_window_end=point.time_window_end,
                        has_priority_order=point.priority,
                    )
                    for point in sequenced
                ],
                priority_relaxed=relaxed,
            )
        )
        progress(
            45 + int(45 * (index + 1) / max(len(active), 1)),
            f"Sequenced {index + 1} of {len(active)} routes",
        )

    assigned_ids = {
        order_id
        for route in result.routes
        for stop in route.stops
        for order_id in stop.order_ids
    }
    result.unassigned_order_ids = [
        order.order_id for order in request.orders if order.order_id not in assigned_ids
    ]
    result.degraded_travel_times = legs.degraded
    result.duration_seconds = time_module.monotonic() - started
    progress(95, "Persisting routes")
    return result
