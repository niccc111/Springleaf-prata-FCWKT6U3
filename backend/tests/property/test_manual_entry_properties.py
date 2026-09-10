"""Property tests for manual entry and geocoding (Tasks 10.3, 10.4, 7.4, 7.5)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import func, select

from app.adapters.base import GeocodeCandidate
from app.core.config import settings
from app.core.errors import ValidationFailed
from app.models.entities import Order, Vehicle
from app.models.enums import OrderSource, UserRole, VehicleSource
from app.schemas.entities import OrderCreate, VehicleCreate
from app.schemas.geo import (
    CoordinateValidationError,
    GeoPoint,
    validate_manual_coordinates,
)
from app.services.geocoding_service import geocoding_service
from app.services.manual_entry_service import manual_entry_service
from tests.conftest import make_user
from tests.property.strategies import addresses, geo_points, operating_hours

pytestmark = pytest.mark.property


# Feature: route-optimisation-engine, Property 7: Manual form validation rejects invalid submissions atomically
@given(
    # NUL is stripped before length validation (PostgreSQL cannot store it), so
    # the over-length case is generated from printable characters to keep the
    # test's intent unambiguous. NUL handling has its own coverage in
    # test_audit_properties.
    address=st.one_of(
        st.just(""),
        st.text(
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
            min_size=501,
            max_size=520,
        ),
    ),
    weight=st.one_of(
        st.decimals(min_value=Decimal("-100"), max_value=Decimal("0"), places=2),
        st.decimals(min_value=Decimal("100000"), max_value=Decimal("200000"), places=2),
    ),
    volume=st.decimals(min_value=Decimal("-50"), max_value=Decimal("-0.01"), places=2),
    invalid_fields=st.lists(
        st.sampled_from(["delivery_address", "cargo_weight_kg", "cargo_volume_m3"]),
        min_size=1,
        max_size=3,
        unique=True,
    ),
)
def test_property_7_invalid_manual_order_persists_nothing(
    sessionmaker_, truncate, run_async, address, weight, volume, invalid_fields
):
    """Any manual Order submission with one or more out-of-range fields persists
    no record and returns a field-level error naming each invalid field.

    Validates: Requirements 3.3
    """
    truncate()
    payload: dict = {
        "delivery_address": "30 Raffles Place, Singapore 048622",
        "cargo_weight_kg": Decimal("10.00"),
        "cargo_volume_m3": Decimal("1.00"),
        "priority": "standard",
    }
    for field in invalid_fields:
        payload[field] = {
            "delivery_address": address,
            "cargo_weight_kg": weight,
            "cargo_volume_m3": volume,
        }[field]

    with pytest.raises(ValidationError) as exc_info:
        OrderCreate(**payload)

    reported = {".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()}
    for field in invalid_fields:
        assert field in reported, f"{field} should be reported as invalid; got {reported}"

    async def count_orders() -> int:
        async with sessionmaker_() as session:
            return await session.scalar(select(func.count()).select_from(Order))

    assert run_async(count_orders()) == 0, "a rejected submission must persist nothing"


# Feature: route-optimisation-engine, Property 8: Manual form submissions set source = manual
@given(
    address=addresses,
    weight=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999.99"), places=2),
    volume=st.one_of(
        st.none(), st.decimals(min_value=Decimal("0"), max_value=Decimal("99.99"), places=2)
    ),
    priority=st.sampled_from(["standard", "priority"]),
    hours=operating_hours(),
    depot=geo_points(),
)
def test_property_8_manual_submissions_are_sourced_manual(
    sessionmaker_, truncate, run_async, address, weight, volume, priority, hours, depot
):
    """Any valid manual Order or Vehicle submission persists with source=manual.

    Validates: Requirements 3.4
    """
    truncate()
    start, end = hours

    async def scenario() -> tuple[Order, Vehicle]:
        async with sessionmaker_() as session:
            user = await make_user(session, UserRole.DISPATCHER)
            order = await manual_entry_service.create_order(
                session,
                OrderCreate(
                    delivery_address=address,
                    cargo_weight_kg=weight,
                    cargo_volume_m3=volume,
                    priority=priority,
                ),
                user.user_id,
            )
            vehicle = await manual_entry_service.create_vehicle(
                session,
                VehicleCreate(
                    registration="SGTEST01",
                    capacity_weight_kg=Decimal("1000.00"),
                    depot_location=depot,
                    operating_hours_start=start,
                    operating_hours_end=end,
                ),
                user.user_id,
            )
            await session.commit()
            return order, vehicle

    order, vehicle = run_async(scenario())
    assert order.source == OrderSource.MANUAL.value
    assert vehicle.source == VehicleSource.MANUAL.value
    assert order.priority == priority
    assert order.cargo_weight_kg == weight


# Feature: route-optimisation-engine, Property 30: Geocoding confidence threshold logic
@given(
    confidences=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=0, max_size=5
    )
)
def test_property_30_confidence_threshold(confidences):
    """A single candidate at or above the threshold stores without a review flag;
    multiple candidates or a sub-threshold best store the highest-confidence
    result flagged for review.

    Validates: Requirements 15.2, 15.3
    """
    candidates = [
        GeocodeCandidate(
            location=GeoPoint(latitude=1.3 + i / 1000, longitude=103.8 + i / 1000),
            confidence=c,
            formatted_address=f"candidate {i}",
        )
        for i, c in enumerate(confidences)
    ]
    outcome = geocoding_service.apply_confidence_policy(candidates)

    if not candidates:
        assert not outcome.resolved
        assert outcome.location is None
        assert not outcome.needs_review
        return

    best = max(candidates, key=lambda c: c.confidence)
    assert outcome.resolved
    assert outcome.confidence == best.confidence
    assert outcome.location == best.location

    threshold = settings.geocode_confidence_threshold
    if len(candidates) == 1 and best.confidence >= threshold:
        assert not outcome.needs_review
    else:
        assert outcome.needs_review


# Feature: route-optimisation-engine, Property 31: Coordinate validation rejects out-of-range values
@given(
    lat=st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
    lon=st.floats(min_value=-2000, max_value=2000, allow_nan=False, allow_infinity=False),
)
def test_property_31_coordinate_range_validation(lat, lon):
    """Latitude must be in [-90, 90] and longitude in [-180, 180]; anything else
    is rejected with a descriptive, field-named error.

    Validates: Requirements 15.5
    """
    lat_ok = -90.0 <= lat <= 90.0
    lon_ok = -180.0 <= lon <= 180.0

    if lat_ok and lon_ok:
        point = validate_manual_coordinates(lat, lon)
        assert point.latitude == lat
        assert point.longitude == lon
        return

    with pytest.raises(CoordinateValidationError) as exc_info:
        validate_manual_coordinates(lat, lon)
    fields = {e["field"] for e in exc_info.value.errors}
    if not lat_ok:
        assert "latitude" in fields
        assert any("between -90 and 90" in e["message"] for e in exc_info.value.errors)
    if not lon_ok:
        assert "longitude" in fields
        assert any("between -180 and 180" in e["message"] for e in exc_info.value.errors)


@given(
    lat=st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
    lon=st.floats(min_value=-2000, max_value=2000, allow_nan=False, allow_infinity=False),
)
def test_property_31_via_service(sessionmaker_, truncate, run_async, lat, lon):
    """The same range rule holds through the manual-coordinate service path."""
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return
    truncate()

    async def scenario() -> None:
        async with sessionmaker_() as session:
            user = await make_user(session, UserRole.DISPATCHER)
            order = await manual_entry_service.create_order(
                session,
                OrderCreate(
                    delivery_address="30 Raffles Place, Singapore 048622",
                    cargo_weight_kg=Decimal("10"),
                ),
                user.user_id,
            )
            await session.commit()
            with pytest.raises(ValidationFailed) as exc_info:
                await manual_entry_service.set_order_coordinates(
                    session, order.order_id, lat, lon, user.user_id
                )
            assert exc_info.value.fields

    run_async(scenario())
