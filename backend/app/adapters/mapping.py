"""Mapping Service adapter — geocoding and traffic-aware travel-time matrices.

Two implementations:

* :class:`MockMappingAdapter` — fully local, deterministic, no credentials.
  Geocodes from a built-in gazetteer plus a stable hash fallback, and derives
  travel times from great-circle distance with a road-network factor and a
  time-of-day traffic multiplier.
* :class:`HttpMappingAdapter` — talks to a Valhalla-compatible HTTP service
  using the request/response shapes in the design document.

Requirements 7.1, 7.4, 7.5, 15.1–15.4.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.base import AdapterUnavailable, GeocodeCandidate, Leg, TravelMatrix
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.geo import GeoPoint

logger = get_logger(__name__)

#: Straight-line distance is multiplied by this to approximate road distance.
ROAD_NETWORK_FACTOR = 1.32


class MappingAdapter(ABC):
    name = "mapping"

    @abstractmethod
    async def geocode(self, address: str) -> list[GeocodeCandidate]: ...

    @abstractmethod
    async def travel_matrix(
        self,
        sources: Sequence[GeoPoint],
        targets: Sequence[GeoPoint] | None = None,
        *,
        departure: datetime | None = None,
    ) -> TravelMatrix: ...

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


def traffic_multiplier(when: datetime | None) -> float:
    """Deterministic time-of-day congestion factor (1.0 – 1.6)."""
    if when is None:
        return 1.0
    hour = when.astimezone(UTC).hour + when.minute / 60.0
    # Two commuter peaks, smoothly interpolated.
    morning = math.exp(-(((hour - 8.5) / 1.6) ** 2))
    evening = math.exp(-(((hour - 18.0) / 1.8) ** 2))
    return 1.0 + 0.6 * max(morning, evening)


# --------------------------------------------------------------------------- #
# Mock adapter
# --------------------------------------------------------------------------- #

#: Known addresses used by the seed dataset. Anything else falls back to a
#: deterministic hash within the service area.
GAZETTEER: dict[str, tuple[float, float]] = {
    "1 harbourfront walk, singapore 098585": (1.2644, 103.8220),
    "10 bayfront avenue, singapore 018956": (1.2834, 103.8607),
    "2 orchard turn, singapore 238801": (1.3040, 103.8318),
    "21 lower kent ridge road, singapore 119077": (1.2966, 103.7764),
    "23 serangoon central, singapore 556083": (1.3506, 103.8718),
    "3 gateway drive, singapore 608532": (1.3345, 103.7430),
    "30 raffles place, singapore 048622": (1.2840, 103.8515),
    "4 tampines central 5, singapore 529510": (1.3525, 103.9447),
    "50 jurong gateway road, singapore 608549": (1.3331, 103.7421),
    "6 woodlands square, singapore 737737": (1.4360, 103.7865),
    "68 orchard road, singapore 238839": (1.2999, 103.8450),
    "80 marine parade road, singapore 449269": (1.3013, 103.9052),
    "9 bishan place, singapore 579837": (1.3510, 103.8485),
    "90 hougang avenue 10, singapore 538766": (1.3712, 103.8930),
    "1 depot road, singapore 109679": (1.2790, 103.8090),
    "31 jurong port road, singapore 619115": (1.3163, 103.7127),
    "2 changi business park ave 1, singapore 486015": (1.3345, 103.9628),
    "51 ang mo kio ave 9, singapore 569783": (1.3810, 103.8480),
}

#: Bounding box for hash-derived coordinates (Singapore + immediate hinterland).
SERVICE_AREA = (1.22, 1.47, 103.60, 104.05)


class MockMappingAdapter(MappingAdapter):
    """Local, deterministic mapping service. No credentials required."""

    name = "mapping:mock"

    def __init__(self, *, speed_kmh: float | None = None) -> None:
        self.speed_kmh = speed_kmh or settings.average_speed_kmh
        self.available = True

    async def geocode(self, address: str) -> list[GeocodeCandidate]:
        if not self.available:
            raise AdapterUnavailable("Mapping Service unavailable")
        normalised = " ".join((address or "").lower().split())
        if not normalised:
            return []
        if "unresolvable" in normalised or "!!" in normalised:
            return []

        if normalised in GAZETTEER:
            lat, lon = GAZETTEER[normalised]
            return [
                GeocodeCandidate(
                    location=GeoPoint(latitude=lat, longitude=lon),
                    confidence=0.97,
                    formatted_address=address.strip(),
                )
            ]

        digest = hashlib.sha256(normalised.encode("utf-8")).digest()
        lat_min, lat_max, lon_min, lon_max = SERVICE_AREA
        lat = lat_min + (int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF) * (lat_max - lat_min)
        lon = lon_min + (int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF) * (lon_max - lon_min)

        # Address specificity drives confidence: a street number and a postcode
        # give a precise match; a bare place name is ambiguous.
        has_number = any(token[0].isdigit() for token in normalised.split() if token)
        has_postcode = any(
            token.isdigit() and len(token) == 6 for token in normalised.replace(",", " ").split()
        )
        confidence = 0.55 + 0.2 * has_number + 0.2 * has_postcode
        confidence = round(min(confidence, 0.95), 2)

        candidates = [
            GeocodeCandidate(
                location=GeoPoint(latitude=round(lat, 6), longitude=round(lon, 6)),
                confidence=confidence,
                formatted_address=address.strip(),
            )
        ]
        if confidence < settings.geocode_confidence_threshold:
            # Ambiguous addresses return a nearby second candidate.
            candidates.append(
                GeocodeCandidate(
                    location=GeoPoint(
                        latitude=round(min(lat + 0.012, lat_max), 6),
                        longitude=round(min(lon + 0.012, lon_max), 6),
                    ),
                    confidence=round(max(confidence - 0.15, 0.05), 2),
                    formatted_address=f"{address.strip()} (alternate)",
                )
            )
        return candidates

    async def travel_matrix(
        self,
        sources: Sequence[GeoPoint],
        targets: Sequence[GeoPoint] | None = None,
        *,
        departure: datetime | None = None,
    ) -> TravelMatrix:
        if not self.available:
            raise AdapterUnavailable("Mapping Service unavailable")
        targets = list(targets) if targets is not None else list(sources)
        sources = list(sources)
        factor = traffic_multiplier(departure)
        legs: dict[tuple[int, int], Leg] = {}
        for i, origin in enumerate(sources):
            for j, destination in enumerate(targets):
                if origin is destination or (
                    origin.latitude == destination.latitude
                    and origin.longitude == destination.longitude
                ):
                    legs[(i, j)] = Leg(0.0, 0.0)
                    continue
                straight_km = origin.haversine_km(destination)
                road_km = straight_km * ROAD_NETWORK_FACTOR
                hours = road_km / max(self.speed_kmh, 1.0) * factor
                legs[(i, j)] = Leg(
                    duration_seconds=round(hours * 3600.0, 1),
                    distance_metres=round(road_km * 1000.0, 1),
                )
        return TravelMatrix(legs=legs, degraded=False, source=self.name)

    async def health_check(self) -> bool:
        return self.available


# --------------------------------------------------------------------------- #
# HTTP adapter
# --------------------------------------------------------------------------- #
class HttpMappingAdapter(MappingAdapter):
    """Valhalla-compatible HTTP mapping service (see design § Mapping Service)."""

    name = "mapping:http"

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, headers=headers
            )
        return self._client

    async def geocode(self, address: str) -> list[GeocodeCandidate]:
        try:
            response = await self._get_client().get("/geocode", params={"address": address})
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"Mapping Service geocode failed: {exc}") from exc

        candidates: list[GeocodeCandidate] = []
        for raw in payload.get("candidates", []):
            try:
                candidates.append(
                    GeocodeCandidate(
                        location=GeoPoint(latitude=raw["lat"], longitude=raw["lon"]),
                        confidence=float(raw.get("confidence", 0.0)),
                        formatted_address=raw.get("formatted_address", address),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # Requirement 7.5 — skip unparseable entries rather than failing.
                logger.warning("geocode_candidate_unparseable", raw=raw)
        return candidates

    async def travel_matrix(
        self,
        sources: Sequence[GeoPoint],
        targets: Sequence[GeoPoint] | None = None,
        *,
        departure: datetime | None = None,
    ) -> TravelMatrix:
        targets = list(targets) if targets is not None else list(sources)
        sources = list(sources)
        body = {
            "sources": [{"lat": p.latitude, "lon": p.longitude} for p in sources],
            "targets": [{"lat": p.latitude, "lon": p.longitude} for p in targets],
            "costing": "auto",
            "date_time": {"type": 1, "value": departure.isoformat()} if departure else None,
        }
        try:
            response = await self._get_client().post("/matrix", json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"Mapping Service matrix failed: {exc}") from exc

        legs: dict[tuple[int, int], Leg] = {}
        rows = payload.get("sources_to_targets", [])
        for i, row in enumerate(rows):
            for j, cell in enumerate(row):
                try:
                    legs[(i, j)] = Leg(
                        duration_seconds=float(cell["time"]),
                        distance_metres=float(cell["distance"]) * 1000.0
                        if cell.get("distance_unit") == "km"
                        else float(cell["distance"]),
                    )
                except (KeyError, TypeError, ValueError):
                    logger.warning("matrix_cell_unparseable", i=i, j=j, cell=cell)
        return TravelMatrix(legs=legs, degraded=False, source=self.name)

    async def health_check(self) -> bool:
        try:
            response = await self._get_client().get("/status")
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_mapping_adapter: MappingAdapter | None = None


def get_mapping_adapter() -> MappingAdapter:
    global _mapping_adapter
    if _mapping_adapter is None:
        if settings.mapping_adapter == "http" and settings.mapping_base_url:
            _mapping_adapter = HttpMappingAdapter(
                settings.mapping_base_url,
                settings.mapping_api_key,
                settings.mapping_timeout_seconds,
            )
        else:
            _mapping_adapter = MockMappingAdapter()
    return _mapping_adapter


def set_mapping_adapter(adapter: MappingAdapter | None) -> None:
    """Test/override hook."""
    global _mapping_adapter
    _mapping_adapter = adapter
