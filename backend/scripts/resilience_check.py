"""Exercise the degraded-integration paths against the real services.

Covers the fallbacks that only appear when an external system is down:
geocoding retries (Requirements 15.6, 15.7), historical travel-time fallback
and the data-quality warning (7.4), Delivery Platform retry with exponential
back-off (13.3, 13.4), and connectivity warnings (1.5, 2.6, 7.4).

    python scripts/resilience_check.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.adapters.delivery_platform import (
    MockDeliveryPlatformAdapter,
    set_delivery_platform_adapter,
)
from app.adapters.mapping import MockMappingAdapter, set_mapping_adapter
from app.core.config import settings
from app.db.session import session_scope
from app.models.entities import (
    Alert,
    GeocodingQueue,
    IntegrationStatus,
    OptimisationRun,
    Order,
    Route,
    Vehicle,
)
from app.models.enums import (
    AlertType,
    IntegrationSystem,
    OptimisationRunStatus,
    OrderSource,
    RouteStatus,
)
from app.schemas.entities import OrderCreate
from app.schemas.geo import GeoPoint
from app.services.export_service import export_service
from app.services.geocoding_service import geocoding_service
from app.services.integration_health import integration_health
from app.services.manual_entry_service import manual_entry_service
from app.services.route_math import StopPlan, build_schedule
from app.services.route_service import route_service

results: list[tuple[bool, str, str]] = []
USER = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


async def geocoding_retry_exhaustion() -> None:
    """Requirements 15.6, 15.7 — queue, retry, then alert on exhaustion."""
    adapter = MockMappingAdapter()
    adapter.available = False
    set_mapping_adapter(adapter)

    async with session_scope() as session:
        order = await manual_entry_service.create_order(
            session,
            OrderCreate(
                delivery_address=f"9 Unmapped Way {uuid.uuid4().hex[:6]}",
                cargo_weight_kg=Decimal("10"),
            ),
            USER,
        )
        order_id = order.order_id
        check("unreachable mapping leaves the order ungeocoded", order.delivery_location is None)

    async with session_scope() as session:
        entry = await session.scalar(
            select(GeocodingQueue).where(GeocodingQueue.order_id == order_id)
        )
        check("geocoding request queued for retry", entry is not None)

    # Run the queue until the attempt budget is spent.
    for _ in range(settings.geocode_max_attempts):
        async with session_scope() as session:
            entry = await session.scalar(
                select(GeocodingQueue).where(GeocodingQueue.order_id == order_id)
            )
            if entry is None or entry.exhausted:
                break
            entry.next_retry = datetime.now(UTC) - timedelta(seconds=1)
        async with session_scope() as session:
            await geocoding_service.process_retry_queue(session, acting_user=USER)

    async with session_scope() as session:
        entry = await session.scalar(
            select(GeocodingQueue).where(GeocodingQueue.order_id == order_id)
        )
        alerts = (
            (
                await session.execute(
                    select(Alert).where(
                        Alert.entity_id == order_id,
                        Alert.alert_type == AlertType.IMPOSSIBLE_ORDER.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        check(
            "retries stop at the configured maximum",
            entry is not None and entry.attempts <= settings.geocode_max_attempts,
            f"{entry.attempts if entry else 0} attempts (max {settings.geocode_max_attempts})",
        )
        check("exhausted retries raise an Impossible Order Alert", len(list(alerts)) >= 1)

    # Recovery: the same address resolves once the service returns.
    adapter.available = True
    async with session_scope() as session:
        entry = await session.scalar(
            select(GeocodingQueue).where(GeocodingQueue.order_id == order_id)
        )
        if entry is not None:
            entry.exhausted = False
            entry.resolved = False
            entry.next_retry = datetime.now(UTC) - timedelta(seconds=1)
    async with session_scope() as session:
        await geocoding_service.process_retry_queue(session, acting_user=USER)
    async with session_scope() as session:
        order = await session.get(Order, order_id)
        check("order geocodes once the service recovers", order.delivery_location is not None)
        await session.execute(delete(GeocodingQueue).where(GeocodingQueue.order_id == order_id))
        await session.execute(delete(Alert).where(Alert.entity_id == order_id))
        await session.delete(order)


async def travel_time_fallback() -> None:
    """Requirement 7.4 — historical averages plus a data-quality warning."""
    adapter = MockMappingAdapter()
    set_mapping_adapter(adapter)
    points = [
        GeoPoint(latitude=1.2790, longitude=103.8090),
        GeoPoint(latitude=1.3040, longitude=103.8318),
    ]

    async with session_scope() as session:
        live = await geocoding_service.get_travel_time_matrix(session, points)
        check("live matrix is not degraded", not live.degraded)

    adapter.available = False
    async with session_scope() as session:
        fallback = await geocoding_service.get_travel_time_matrix(session, points)
        check("fallback matrix is flagged degraded", fallback.degraded, fallback.source)
        check(
            "fallback reuses the recorded historical average",
            abs(fallback.duration_seconds(0, 1) - live.duration_seconds(0, 1)) < 1.0,
            f"{fallback.duration_seconds(0, 1):.0f}s vs {live.duration_seconds(0, 1):.0f}s live",
        )
        # Requirement 7.4 warns only after the service has been down for more
        # than 30 s, so the first failure records the outage without warning.
        status = await session.get(
            IntegrationStatus, IntegrationSystem.MAPPING.value, populate_existing=True
        )
        check(
            "first mapping failure records the outage without warning yet",
            status is not None and status.healthy and status.degraded_since is not None,
        )

    async with session_scope() as session:
        status = await session.get(IntegrationStatus, IntegrationSystem.MAPPING.value)
        status.degraded_since = datetime.now(UTC) - timedelta(seconds=90)
    async with session_scope() as session:
        await geocoding_service.get_travel_time_matrix(session, points)
        healthy = await integration_health.is_healthy(session, IntegrationSystem.MAPPING)
        check("mapping warns once the 30s threshold is passed", not healthy)

    adapter.available = True
    async with session_scope() as session:
        await geocoding_service.get_travel_time_matrix(session, points)
        healthy = await integration_health.is_healthy(session, IntegrationSystem.MAPPING)
        check("mapping recovers automatically", healthy)


async def export_retry_and_recovery() -> None:
    """Requirements 13.3, 13.4, 13.5 — back-off, alert, manual re-trigger."""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    async with session_scope() as session:
        vehicle = Vehicle(
            vehicle_id=uuid.uuid4(),
            source=OrderSource.MANUAL.value.replace("manual", "manual"),
            registration=f"RES{uuid.uuid4().hex[:5].upper()}",
            capacity_weight_kg=Decimal("1000"),
            depot_location=GeoPoint(latitude=1.279, longitude=103.809),
            operating_hours_start=time(8, 0),
            operating_hours_end=time(18, 0),
        )
        order = Order(
            order_id=uuid.uuid4(),
            source=OrderSource.MANUAL.value,
            delivery_address="30 Raffles Place, Singapore 048622",
            delivery_location=GeoPoint(latitude=1.284, longitude=103.8515),
            cargo_weight_kg=Decimal("20"),
        )
        run = OptimisationRun(
            run_id=uuid.uuid4(),
            status=OptimisationRunStatus.COMPLETED.value,
            initiated_by=USER,
        )
        session.add_all([vehicle, order, run])
        await session.flush()

        plan = StopPlan(
            location=order.delivery_location,
            address=order.delivery_address,
            order_ids=[order.order_id],
            weight_kg=order.cargo_weight_kg,
            service_duration_min=10,
        )
        matrix = await MockMappingAdapter().travel_matrix(
            [vehicle.depot_location, order.delivery_location]
        )
        schedule = build_schedule(
            depot=vehicle.depot_location,
            departure_time=datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0),
            stops=[plan],
            matrix=matrix,
        )
        route = await route_service.create_route(
            session, vehicle=vehicle, run_id=run.run_id, schedule=schedule, acting_user=USER
        )
        await route_service.update_route_status(
            session, route.route_id, RouteStatus.APPROVED, USER
        )
        route_id = route.route_id

    # Platform down: every attempt fails.
    set_delivery_platform_adapter(MockDeliveryPlatformAdapter(always_fail=True))
    async with session_scope() as session:
        job = await export_service.export_route_now(session, route_id, USER, sleeper=fake_sleep)
        check("export exhausts exactly three retries", job.attempts == 4, f"{job.attempts} attempts")
        check("back-off is 5s, 10s, 20s", slept == [5.0, 10.0, 20.0], str(slept))

    async with session_scope() as session:
        route = await route_service.get_route(session, route_id)
        alerts = (
            (
                await session.execute(
                    select(Alert).where(
                        Alert.entity_id == route_id,
                        Alert.alert_type == AlertType.EXPORT_FAILURE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        check("route stays approved after exhaustion", route.status == RouteStatus.APPROVED.value)
        check("export failure alert raised", len(list(alerts)) == 1)

    # Platform back: the manual re-trigger dispatches the route.
    set_delivery_platform_adapter(MockDeliveryPlatformAdapter())
    async with session_scope() as session:
        job = await export_service.export_route_now(session, route_id, USER, sleeper=fake_sleep)
        check("manual re-trigger succeeds on the first attempt", job.attempts == 1)
    async with session_scope() as session:
        route = await route_service.get_route(session, route_id)
        check("route dispatched and locked", route.status == RouteStatus.DISPATCHED.value and route.locked)
        unacked = (
            (
                await session.execute(
                    select(Alert).where(
                        Alert.entity_id == route_id,
                        Alert.alert_type == AlertType.EXPORT_FAILURE.value,
                        Alert.acknowledged.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        check("export failure alert cleared on success", len(list(unacked)) == 0)

    async with session_scope() as session:
        route = await session.get(Route, route_id)
        await session.execute(delete(Alert).where(Alert.entity_id == route_id))
        await session.delete(route)


async def connectivity_thresholds() -> None:
    """Requirements 1.5, 2.6 — warnings appear only past the threshold."""
    async with session_scope() as session:
        await integration_health.record_success(session, IntegrationSystem.OMS)
        status = await integration_health.record_failure(
            session, IntegrationSystem.OMS, "queue unreachable", threshold_seconds=60
        )
        check("brief OMS outage does not warn immediately", status.healthy)

    async with session_scope() as session:
        status = await session.get(IntegrationStatus, IntegrationSystem.OMS.value)
        status.degraded_since = datetime.now(UTC) - timedelta(seconds=120)
    async with session_scope() as session:
        status = await integration_health.record_failure(
            session, IntegrationSystem.OMS, "queue unreachable", threshold_seconds=60
        )
        check("OMS warns once the 60s threshold is passed", not status.healthy)

    async with session_scope() as session:
        status = await integration_health.record_success(session, IntegrationSystem.OMS)
        check("warning clears on recovery", status.healthy and status.degraded_since is None)


async def main() -> int:
    print("\n[1] Geocoding retry queue (Requirements 15.6, 15.7)")
    await geocoding_retry_exhaustion()
    print("\n[2] Travel-time fallback (Requirement 7.4)")
    await travel_time_fallback()
    print("\n[3] Delivery Platform retry (Requirements 13.3, 13.4, 13.5)")
    await export_retry_and_recovery()
    print("\n[4] Connectivity thresholds (Requirements 1.5, 2.6)")
    await connectivity_thresholds()

    set_mapping_adapter(None)
    set_delivery_platform_adapter(None)

    failed = [r for r in results if not r[0]]
    print(f"\n{'=' * 66}\n{len(results) - len(failed)}/{len(results)} resilience checks passed")
    for _, name, detail in failed:
        print(f"  - {name} ({detail})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
