"""Shared adapter primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.schemas.geo import GeoPoint


@dataclass(slots=True)
class GeocodeCandidate:
    location: GeoPoint
    confidence: float
    formatted_address: str


@dataclass(slots=True)
class Leg:
    """One origin→destination segment."""

    duration_seconds: float
    distance_metres: float


@dataclass(slots=True)
class TravelMatrix:
    legs: dict[tuple[int, int], Leg] = field(default_factory=dict)
    #: True when values came from historical averages rather than live traffic.
    degraded: bool = False
    source: str = "live"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def duration_seconds(self, i: int, j: int) -> float:
        leg = self.legs.get((i, j))
        return leg.duration_seconds if leg else 0.0

    def distance_metres(self, i: int, j: int) -> float:
        leg = self.legs.get((i, j))
        return leg.distance_metres if leg else 0.0


class AdapterUnavailable(RuntimeError):
    """Raised when an external system cannot be reached."""
