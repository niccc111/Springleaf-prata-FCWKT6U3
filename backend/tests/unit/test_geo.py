"""Unit tests for the GeoPoint value object."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.geo import CoordinateValidationError, GeoPoint, validate_manual_coordinates


def test_coerce_accepts_every_supported_shape():
    expected = GeoPoint(latitude=1.3, longitude=103.8)
    assert GeoPoint.coerce(expected) == expected
    assert GeoPoint.coerce({"latitude": 1.3, "longitude": 103.8}) == expected
    assert GeoPoint.coerce({"lat": 1.3, "lon": 103.8}) == expected
    assert GeoPoint.coerce((1.3, 103.8)) == expected
    assert GeoPoint.coerce("(1.3,103.8)") == expected
    assert GeoPoint.coerce((Decimal("1.3"), Decimal("103.8"))) == expected


def test_coerce_rejects_garbage():
    with pytest.raises(ValueError):
        GeoPoint.coerce("not a point")
    with pytest.raises(ValueError):
        GeoPoint.coerce({"x": 1})
    with pytest.raises(ValueError):
        GeoPoint.coerce(42)


@pytest.mark.parametrize(
    ("lat", "lon"), [(91, 0), (-91, 0), (0, 181), (0, -181), (100, 200)]
)
def test_out_of_range_is_rejected(lat, lon):
    with pytest.raises(ValidationError):
        GeoPoint(latitude=lat, longitude=lon)
    with pytest.raises(CoordinateValidationError):
        validate_manual_coordinates(lat, lon)


@pytest.mark.parametrize(("lat", "lon"), [(0, 0), (90, 180), (-90, -180), (1.3521, 103.8198)])
def test_boundary_values_are_accepted(lat, lon):
    point = validate_manual_coordinates(lat, lon)
    assert point.latitude == lat
    assert point.longitude == lon


def test_error_names_the_offending_fields():
    with pytest.raises(CoordinateValidationError) as exc_info:
        validate_manual_coordinates(120, 400)
    fields = {e["field"] for e in exc_info.value.errors}
    assert fields == {"latitude", "longitude"}


def test_non_numeric_input_is_rejected_descriptively():
    with pytest.raises(CoordinateValidationError) as exc_info:
        validate_manual_coordinates("abc", None)
    assert any("must be a number" in e["message"] for e in exc_info.value.errors)


def test_haversine_distance_is_symmetric_and_zero_for_identical_points():
    a = GeoPoint(latitude=1.2840, longitude=103.8515)
    b = GeoPoint(latitude=1.3521, longitude=103.8198)
    assert a.haversine_km(a) == pytest.approx(0.0, abs=1e-9)
    assert a.haversine_km(b) == pytest.approx(b.haversine_km(a), rel=1e-9)
    # Raffles Place to central Singapore is roughly 8 km.
    assert 7.0 < a.haversine_km(b) < 9.0


def test_geohash_key_is_stable():
    a = GeoPoint(latitude=1.28401, longitude=103.85152)
    assert a.geohash6() == "1.284,103.852"
