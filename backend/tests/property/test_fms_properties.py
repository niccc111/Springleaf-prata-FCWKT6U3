"""Property tests for the FMS integration adapter (Tasks 9.3-9.5)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.adapters.fms import fms_adapter
from app.models.entities import Vehicle
from app.models.enums import VehicleSource
from tests.property.strategies import capacities, geo_points, valid_fms_events

pytestmark = pytest.mark.property


# Feature: route-optimisation-engine, Property 4: FMS vehicle creation sets correct source and external_ref
@given(event=valid_fms_events())
def test_property_4_new_registration_sets_source_and_ref(
    sessionmaker_, truncate, run_async, event
):
    """Any valid FMS new-registration event yields source=FMS with the FMS ref.

    Validates: Requirements 2.2
    """
    truncate()

    async def scenario() -> Vehicle | None:
        async with sessionmaker_() as session:
            outcome = await fms_adapter.process_event(session, event)
            await session.commit()
            assert outcome.created, f"expected creation, got {outcome}"
            return await session.get(Vehicle, outcome.vehicle_id)

    vehicle = run_async(scenario())
    assert vehicle is not None
    assert vehicle.source == VehicleSource.FMS.value
    assert vehicle.external_ref == event["external_ref"]
    assert vehicle.capacity_weight_kg == Decimal(event["capacity_weight_kg"])


# Feature: route-optimisation-engine, Property 5: FMS upsert never duplicates vehicles
@given(
    event=valid_fms_events(),
    new_capacity=capacities,
    new_depot=geo_points(),
    updates=st.integers(min_value=1, max_value=4),
)
def test_property_5_upsert_never_duplicates(
    sessionmaker_, truncate, run_async, event, new_capacity, new_depot, updates
):
    """Updates against an existing external_ref keep exactly one Vehicle row and
    apply the event's data.

    Validates: Requirements 2.3
    """
    truncate()
    update_event = dict(event)
    update_event["event_type"] = "update"
    update_event["capacity_weight_kg"] = str(new_capacity)
    update_event["depot_location"] = new_depot.model_dump()

    async def scenario() -> tuple[int, Vehicle | None]:
        async with sessionmaker_() as session:
            await fms_adapter.process_event(session, event)
            await session.commit()
            for _ in range(updates):
                outcome = await fms_adapter.process_event(session, dict(update_event))
                await session.commit()
                assert outcome.updated, f"expected update, got {outcome}"
            count = await session.scalar(
                select(func.count())
                .select_from(Vehicle)
                .where(Vehicle.external_ref == event["external_ref"])
            )
            vehicle = await session.scalar(
                select(Vehicle).where(Vehicle.external_ref == event["external_ref"])
            )
            return count, vehicle

    count, vehicle = run_async(scenario())
    assert count == 1, f"expected exactly one vehicle for the ref, found {count}"
    assert vehicle is not None
    assert vehicle.capacity_weight_kg == new_capacity
    assert vehicle.depot_location.latitude == pytest.approx(new_depot.latitude, abs=1e-6)


# Feature: route-optimisation-engine, Property 6: FMS validation rejects records with missing required fields
@given(
    event=valid_fms_events(),
    drop_capacity=st.booleans(),
    drop_depot=st.booleans(),
    pre_exists=st.booleans(),
)
def test_property_6_missing_required_fields_rejected(
    sessionmaker_, truncate, run_async, event, drop_capacity, drop_depot, pre_exists
):
    """Events missing capacity_weight_kg or depot_location create/update nothing
    and notify the dispatcher naming the FMS ref and the missing field.

    Validates: Requirements 2.5
    """
    if not (drop_capacity or drop_depot):
        return
    truncate()

    broken = dict(event)
    if drop_capacity:
        broken.pop("capacity_weight_kg", None)
    if drop_depot:
        broken.pop("depot_location", None)

    async def scenario():
        async with sessionmaker_() as session:
            baseline_capacity = None
            if pre_exists:
                await fms_adapter.process_event(session, event)
                await session.commit()
                existing = await session.scalar(
                    select(Vehicle).where(Vehicle.external_ref == event["external_ref"])
                )
                baseline_capacity = existing.capacity_weight_kg
            outcome = await fms_adapter.process_event(session, broken)
            await session.commit()
            count = await session.scalar(select(func.count()).select_from(Vehicle))
            vehicle = await session.scalar(
                select(Vehicle).where(Vehicle.external_ref == event["external_ref"])
            )
            return outcome, count, vehicle, baseline_capacity

    outcome, count, vehicle, baseline_capacity = run_async(scenario())

    assert outcome.rejected, "invalid FMS event must be rejected"
    assert outcome.notification is not None
    assert event["external_ref"] in outcome.notification
    if drop_capacity:
        assert any("capacity_weight_kg" in e for e in outcome.errors)
    if drop_depot:
        assert any("depot_location" in e for e in outcome.errors)

    if pre_exists:
        # The pre-existing record survives, is not overwritten with the bad
        # data, and is forced unavailable (Requirement 2.5).
        assert count == 1
        assert vehicle is not None
        assert vehicle.capacity_weight_kg == baseline_capacity
        assert vehicle.available is False
    else:
        assert count == 0, "no Vehicle may be created from an invalid event"
