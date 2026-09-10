"""Property tests for the OMS integration adapter (Tasks 8.3-8.5)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.adapters.oms import oms_adapter
from app.models.entities import Alert, Order
from app.models.enums import AlertType, OrderSource
from app.schemas.geo import GeoPoint
from tests.property.strategies import (
    addresses,
    external_refs,
    valid_oms_events,
)

pytestmark = pytest.mark.property


# Feature: route-optimisation-engine, Property 1: OMS event validation rejects missing required fields
@given(
    ref=external_refs,
    address=addresses,
    drop_location=st.booleans(),
    bad_weight=st.one_of(
        st.none(),
        st.just(""),
        st.decimals(min_value=Decimal("-500"), max_value=Decimal("0"), places=2),
        st.just("not-a-number"),
    ),
)
def test_property_1_invalid_events_are_rejected(
    sessionmaker_, truncate, run_async, ref, address, drop_location, bad_weight
):
    """For any OMS event with a missing/invalid location or weight, no Order is
    created and an Impossible Order Alert names the offending field.

    Validates: Requirements 1.2, 1.3
    """
    truncate()

    event: dict = {"external_ref": ref, "delivery_address": address}
    if not drop_location:
        event["delivery_location"] = GeoPoint(latitude=1.3, longitude=103.8).model_dump()
    if bad_weight is None:
        pass  # omit the key entirely
    else:
        event["cargo_weight_kg"] = str(bad_weight)

    # An event with a valid location AND a valid weight is not a rejection case.
    weight_is_valid = False
    if bad_weight not in (None, "", "not-a-number"):
        weight_is_valid = Decimal(str(bad_weight)) > 0
    if not drop_location and weight_is_valid:
        return

    async def scenario() -> tuple[bool, int, list[Alert]]:
        async with sessionmaker_() as session:
            outcome = await oms_adapter.process_event(session, event)
            await session.commit()
            count = await session.scalar(select(func.count()).select_from(Order))
            alerts = (
                (await session.execute(select(Alert))).scalars().all()
            )
            return outcome.rejected, count, list(alerts)

    rejected, order_count, alerts = run_async(scenario())

    assert rejected, "event should have been rejected"
    assert order_count == 0, "no Order record may be created for a rejected event"
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.alert_type == AlertType.IMPOSSIBLE_ORDER.value
    assert alert.severity == "critical"
    # The message names the offending field(s) by name.
    named = list(alert.context["errors"])
    assert named, "alert must carry the validation errors"
    if drop_location and not address.strip():
        assert any("delivery_location" in e for e in named)
    if bad_weight is None or bad_weight == "":
        assert any("cargo_weight_kg is missing" in e for e in named)
    elif bad_weight == "not-a-number":
        assert any("cargo_weight_kg is not a number" in e for e in named)
    else:
        assert any("cargo_weight_kg" in e for e in named)


# Feature: route-optimisation-engine, Property 2: OMS field mapping preserves all data
@given(event=valid_oms_events())
def test_property_2_field_mapping_preserves_data(sessionmaker_, truncate, run_async, event):
    """For any valid OMS event, every field maps onto the Order schema and
    external_ref equals the OMS order identifier.

    Validates: Requirements 1.4
    """
    truncate()

    async def scenario() -> Order | None:
        async with sessionmaker_() as session:
            outcome = await oms_adapter.process_event(session, event)
            await session.commit()
            if outcome.order_id is None:
                return None
            return await session.get(Order, outcome.order_id)

    order = run_async(scenario())
    assert order is not None, "a valid event must produce an Order"
    assert order.source == OrderSource.OMS.value
    assert order.external_ref == event["external_ref"]
    assert order.delivery_address == event["delivery_address"].strip()
    assert order.cargo_weight_kg == Decimal(event["cargo_weight_kg"])
    assert order.priority == event["priority"]
    assert order.service_duration_min == event["service_duration_min"]
    assert order.delivery_location is not None
    assert order.delivery_location.latitude == pytest.approx(
        event["delivery_location"]["latitude"], abs=1e-6
    )
    assert order.delivery_location.longitude == pytest.approx(
        event["delivery_location"]["longitude"], abs=1e-6
    )
    if "cargo_volume_m3" in event:
        assert order.cargo_volume_m3 == Decimal(event["cargo_volume_m3"])
    if "time_window_start" in event:
        assert order.time_window_start.isoformat() == event["time_window_start"]
        assert order.time_window_end.isoformat() == event["time_window_end"]


# Feature: route-optimisation-engine, Property 3: OMS deduplication is idempotent
@given(event=valid_oms_events(), repeats=st.integers(min_value=2, max_value=6))
def test_property_3_deduplication_is_idempotent(
    sessionmaker_, truncate, run_async, event, repeats
):
    """Submitting the same OMS event N>=2 times yields exactly one Order.

    Validates: Requirements 1.6
    """
    truncate()

    async def scenario() -> tuple[int, int, int]:
        created = duplicates = 0
        async with sessionmaker_() as session:
            for _ in range(repeats):
                outcome = await oms_adapter.process_event(session, dict(event))
                await session.commit()
                created += int(outcome.created)
                duplicates += int(outcome.duplicate)
            count = await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.external_ref == event["external_ref"])
            )
            return created, duplicates, count

    created, duplicates, count = run_async(scenario())
    assert count == 1, f"expected exactly one Order, found {count}"
    assert created == 1
    assert duplicates == repeats - 1
