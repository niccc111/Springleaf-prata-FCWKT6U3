"""Property tests for the Export Service (Task 14.2)."""

from __future__ import annotations

import itertools
import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select

from app.adapters.delivery_platform import (
    MockDeliveryPlatformAdapter,
    set_delivery_platform_adapter,
)
from app.core.config import settings
from app.models.entities import Alert, ExportJob, OptimisationRun, Route
from app.models.enums import (
    AlertType,
    ExportStatus,
    OptimisationRunStatus,
    RouteStatus,
)
from app.services.export_service import backoff_schedule, export_service
from app.services.route_math import StopPlan, build_schedule
from app.services.route_service import route_service
from tests.conftest import build_order, build_vehicle, make_actor
from tests.property.strategies import geo_points

pytestmark = pytest.mark.property


async def _approved_route(session, user_id: uuid.UUID, point) -> Route:
    from app.adapters.mapping import MockMappingAdapter

    vehicle = build_vehicle()
    order = build_order(delivery_location=point)
    run = OptimisationRun(
        run_id=uuid.uuid4(),
        status=OptimisationRunStatus.COMPLETED.value,
        initiated_by=user_id,
    )
    session.add_all([vehicle, order, run])
    await session.flush()

    plan = StopPlan(
        location=point,
        address=order.delivery_address,
        order_ids=[order.order_id],
        weight_kg=order.cargo_weight_kg,
        volume_m3=order.cargo_volume_m3,
        service_duration_min=10,
    )
    matrix = await MockMappingAdapter().travel_matrix([vehicle.depot_location, point])
    schedule = build_schedule(
        depot=vehicle.depot_location,
        departure_time=__import__("datetime").datetime(
            2026, 6, 1, 8, tzinfo=__import__("datetime").UTC
        ),
        stops=[plan],
        matrix=matrix,
    )
    route = await route_service.create_route(
        session, vehicle=vehicle, run_id=run.run_id, schedule=schedule, acting_user=user_id
    )
    await route_service.update_route_status(session, route.route_id, RouteStatus.APPROVED, user_id)
    await session.flush()
    return route


# Feature: route-optimisation-engine, Property 28: Export retry respects exponential back-off
@given(failures=st.integers(min_value=0, max_value=6), point=geo_points())
def test_property_28_export_backoff(sessionmaker_, truncate, run_async, failures, point):
    """At most 3 retries follow the initial attempt, the delays double from 5s
    (5s, 10s, 20s), and no interval exceeds 20s.

    Validates: Requirements 13.3
    """
    truncate()
    adapter = MockDeliveryPlatformAdapter(fail_next=failures)
    set_delivery_platform_adapter(adapter)
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            route = await _approved_route(session, actor, point)
            await session.commit()
            job = await export_service.export_route_now(
                session, route.route_id, actor, sleeper=fake_sleep
            )
            await session.commit()
            refreshed = await route_service.get_route(session, route.route_id)
            alerts = (
                (
                    await session.execute(
                        select(Alert).where(Alert.alert_type == AlertType.EXPORT_FAILURE.value)
                    )
                )
                .scalars()
                .all()
            )
            return job, refreshed, list(alerts)

    job, route, alerts = run_async(scenario())

    expected_delays = backoff_schedule()
    assert expected_delays == [5.0, 10.0, 20.0]
    assert len(slept) <= settings.export_max_retries, "no more than 3 retries"
    assert slept == expected_delays[: len(slept)], f"delays must be 5,10,20; got {slept}"
    assert all(delay <= 20.0 for delay in slept)
    for earlier, later in itertools.pairwise(slept):
        assert later == earlier * 2, "each delay must double the previous one"

    total_attempts = 1 + len(slept)
    assert job.attempts == total_attempts
    assert len(job.attempt_log) == total_attempts

    if failures <= settings.export_max_retries:
        assert job.status == ExportStatus.SUCCEEDED.value
        # Requirement 13.2 — acknowledgement moves the route to dispatched.
        assert route.status == RouteStatus.DISPATCHED.value
        assert route.locked is True
        assert not alerts
    else:
        # Requirement 13.4 — exhausted retries leave the route approved and alert.
        assert job.status == ExportStatus.FAILED.value
        assert route.status == RouteStatus.APPROVED.value
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"


@given(point=geo_points())
def test_manual_retrigger_cancels_in_flight_sequence(sessionmaker_, truncate, run_async, point):
    """Requirement 13.5 — a manual re-trigger cancels any in-progress sequence."""
    truncate()
    set_delivery_platform_adapter(MockDeliveryPlatformAdapter(always_fail=True))

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            route = await _approved_route(session, actor, point)
            await session.commit()
            first = await export_service.enqueue_export(session, route.route_id, actor)
            await session.commit()
            second = await export_service.enqueue_export(session, route.route_id, actor)
            await session.commit()
            jobs = (
                (
                    await session.execute(
                        select(ExportJob).where(ExportJob.route_id == route.route_id)
                    )
                )
                .scalars()
                .all()
            )
            return first, second, {j.job_id: j for j in jobs}

    first, second, jobs = run_async(scenario())
    assert jobs[first.job_id].status == ExportStatus.CANCELLED.value
    assert jobs[first.job_id].cancelled is True
    assert jobs[second.job_id].status == ExportStatus.PENDING.value
