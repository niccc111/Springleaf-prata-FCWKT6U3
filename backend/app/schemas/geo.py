"""GeoPoint value object shared across the API and persistence layers."""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0

_TUPLE_RE = re.compile(r"^\(?\s*(?P<lat>-?\d+(?:\.\d+)?)\s*,\s*(?P<lon>-?\d+(?:\.\d+)?)\s*\)?$")

EARTH_RADIUS_KM = 6371.0088


class GeoPoint(BaseModel):
    """A WGS-84 coordinate pair.

    Latitude must be within [-90, +90] and longitude within [-180, +180]
    (Requirement 15.5 / Property 31).
    """

    model_config = ConfigDict(frozen=True)

    latitude: float = Field(..., ge=LAT_MIN, le=LAT_MAX)
    longitude: float = Field(..., ge=LON_MIN, le=LON_MAX)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def _to_float(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, str) and value.strip():
            return float(value)
        return value

    @classmethod
    def coerce(cls, value: Any) -> GeoPoint:
        """Build a GeoPoint from a model, mapping, tuple, or ``(lat,lon)`` string."""
        if isinstance(value, GeoPoint):
            return value
        if isinstance(value, dict):
            if "latitude" in value:
                return cls(latitude=value["latitude"], longitude=value["longitude"])
            if "lat" in value:
                return cls(latitude=value["lat"], longitude=value.get("lon", value.get("lng")))
            raise ValueError("mapping does not contain latitude/longitude keys")
        if isinstance(value, str):
            match = _TUPLE_RE.match(value.strip())
            if not match:
                raise ValueError(f"cannot parse geopoint from {value!r}")
            return cls(latitude=float(match["lat"]), longitude=float(match["lon"]))
        if isinstance(value, tuple | list) or hasattr(value, "__getitem__"):
            return cls(latitude=value[0], longitude=value[1])
        raise ValueError(f"cannot coerce {type(value).__name__} to GeoPoint")

    def as_tuple(self) -> tuple[float, float]:
        return (self.latitude, self.longitude)

    def haversine_km(self, other: GeoPoint) -> float:
        """Great-circle distance in kilometres."""
        lat1, lon1, lat2, lon2 = map(
            math.radians, (self.latitude, self.longitude, other.latitude, other.longitude)
        )
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))

    def geohash6(self) -> str:
        """Coarse spatial key used for historical travel-time caching."""
        return f"{self.latitude:.3f},{self.longitude:.3f}"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"({self.latitude:.6f}, {self.longitude:.6f})"


class CoordinateValidationError(ValueError):
    """Raised when a manually supplied coordinate pair is out of range."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("; ".join(e["message"] for e in errors))


def validate_manual_coordinates(latitude: Any, longitude: Any) -> GeoPoint:
    """Validate manually supplied coordinates (Requirement 15.5, Property 31).

    Returns a :class:`GeoPoint` or raises :class:`CoordinateValidationError`
    carrying one descriptive entry per offending field.
    """
    errors: list[dict[str, str]] = []

    def _check(name: str, raw: Any, low: float, high: float) -> float | None:
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            errors.append({"field": name, "message": f"{name} must be a number, got {raw!r}"})
            return None
        if math.isnan(numeric) or math.isinf(numeric):
            errors.append({"field": name, "message": f"{name} must be a finite number"})
            return None
        if not (low <= numeric <= high):
            errors.append(
                {
                    "field": name,
                    "message": f"{name} must be between {low:g} and {high:g}, got {numeric:g}",
                }
            )
            return None
        return numeric

    lat = _check("latitude", latitude, LAT_MIN, LAT_MAX)
    lon = _check("longitude", longitude, LON_MIN, LON_MAX)
    if errors:
        raise CoordinateValidationError(errors)
    assert lat is not None and lon is not None
    return GeoPoint(latitude=lat, longitude=lon)
