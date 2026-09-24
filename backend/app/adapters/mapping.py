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

    async def route_geometry(
        self, waypoints: Sequence[GeoPoint]
    ) -> list[list[float]] | None:
        """Road-following polyline through ``waypoints`` in order.

        Returns a list of ``[longitude, latitude]`` pairs tracing the actual
        roads, or ``None`` when the adapter cannot produce road geometry (the
        caller then falls back to straight segments between stops).
        """
        return None

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


# --------------------------------------------------------------------------- #
# Google Maps adapter
# --------------------------------------------------------------------------- #
class GoogleMappingAdapter(MappingAdapter):
    """Google Maps Platform adapter.

    Geocoding uses the Geocoding API; travel times use the Routes API
    ``computeRouteMatrix`` with ``routingPreference: TRAFFIC_AWARE`` so distances
    and durations follow the real road network and the fastest route. The API
    key needs both the Geocoding API and Routes API enabled.
    """

    name = "mapping:google"

    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

    #: Google's geocoding "location_type" mapped to a confidence score.
    _LOCATION_TYPE_CONFIDENCE = {
        "ROOFTOP": 0.98,
        "RANGE_INTERPOLATED": 0.9,
        "GEOMETRIC_CENTER": 0.75,
        "APPROXIMATE": 0.6,
    }

    def __init__(
        self,
        api_key: str,
        *,
        region: str = "sg",
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.region = region
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def geocode(self, address: str) -> list[GeocodeCandidate]:
        params = {
            "address": address,
            "key": self.api_key,
            "region": self.region,
        }
        try:
            response = await self._get_client().get(self.GEOCODE_URL, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"Google geocode failed: {exc}") from exc

        status = payload.get("status")
        if status == "ZERO_RESULTS":
            return []
        if status != "OK":
            # OVER_QUERY_LIMIT / REQUEST_DENIED / INVALID_REQUEST are operational
            # failures the caller should treat as an unavailable service.
            raise AdapterUnavailable(
                f"Google geocode returned {status}: {payload.get('error_message', '')}"
            )

        candidates: list[GeocodeCandidate] = []
        for raw in payload.get("results", []):
            try:
                geometry = raw["geometry"]
                loc = geometry["location"]
                confidence = self._LOCATION_TYPE_CONFIDENCE.get(
                    geometry.get("location_type", ""), 0.6
                )
                candidates.append(
                    GeocodeCandidate(
                        location=GeoPoint(latitude=loc["lat"], longitude=loc["lng"]),
                        confidence=confidence,
                        formatted_address=raw.get("formatted_address", address),
                    )
                )
            except (KeyError, TypeError, ValueError):
                logger.warning("google_geocode_candidate_unparseable", raw=raw)
        return candidates

    @staticmethod
    def _waypoint(point: GeoPoint) -> dict[str, Any]:
        return {
            "waypoint": {
                "location": {
                    "latLng": {"latitude": point.latitude, "longitude": point.longitude}
                }
            }
        }

    @staticmethod
    def _parse_duration(value: Any) -> float:
        """Google durations are strings like ``"123s"`` (protobuf Duration)."""
        if value is None:
            return 0.0
        text = str(value).rstrip("s")
        try:
            return float(text)
        except ValueError:
            return 0.0

    async def travel_matrix(
        self,
        sources: Sequence[GeoPoint],
        targets: Sequence[GeoPoint] | None = None,
        *,
        departure: datetime | None = None,
    ) -> TravelMatrix:
        targets = list(targets) if targets is not None else list(sources)
        sources = list(sources)

        body: dict[str, Any] = {
            "origins": [self._waypoint(p) for p in sources],
            "destinations": [self._waypoint(p) for p in targets],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
        }
        # A future departure time makes Google apply predictive traffic. Google
        # rejects times in the past, so only forward-looking times are sent.
        if departure is not None:
            dep = departure.astimezone(UTC)
            if dep > datetime.now(UTC):
                body["departureTime"] = dep.isoformat().replace("+00:00", "Z")

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "originIndex,destinationIndex,duration,distanceMeters,condition"
            ),
        }
        try:
            response = await self._get_client().post(
                self.MATRIX_URL, json=body, headers=headers
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"Google route matrix failed: {exc}") from exc

        # The REST endpoint returns a JSON array of matrix elements.
        elements = payload if isinstance(payload, list) else payload.get("elements", [])
        legs: dict[tuple[int, int], Leg] = {}
        degraded = False
        for cell in elements:
            try:
                i = int(cell["originIndex"])
                j = int(cell["destinationIndex"])
            except (KeyError, TypeError, ValueError):
                logger.warning("google_matrix_cell_unindexed", cell=cell)
                continue
            # ROUTE_NOT_FOUND etc. leave the leg at zero and flag the matrix.
            if cell.get("condition") not in (None, "ROUTE_EXISTS"):
                degraded = True
                continue
            legs[(i, j)] = Leg(
                duration_seconds=self._parse_duration(cell.get("duration")),
                distance_metres=float(cell.get("distanceMeters", 0.0)),
            )
        return TravelMatrix(legs=legs, degraded=degraded, source=self.name)

    async def health_check(self) -> bool:
        # A trivial geocode round-trip confirms the key and connectivity.
        try:
            await self.geocode("Singapore")
            return True
        except AdapterUnavailable:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# OSRM adapter (free road-network routing, no API key)
# --------------------------------------------------------------------------- #
class OsrmMappingAdapter(MappingAdapter):
    """OSRM road-network routing.

    Uses OSRM's ``/table`` service for a real driving-time/distance matrix so
    the optimiser plans along actual roads and picks the fastest route. OSRM is
    routing-only, so geocoding is delegated to the local gazetteer/hash geocoder
    (:class:`MockMappingAdapter`). Point ``osrm_base_url`` at your own server for
    production; the public demo server is rate-limited and only returns
    durations, so distances are derived from duration and average speed there.
    """

    name = "mapping:osrm"

    def __init__(
        self,
        base_url: str,
        *,
        speed_kmh: float | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.speed_kmh = speed_kmh or settings.average_speed_kmh
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        #: Geocoding is not an OSRM capability; reuse the local geocoder.
        self._geocoder = MockMappingAdapter(speed_kmh=self.speed_kmh)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def geocode(self, address: str) -> list[GeocodeCandidate]:
        return await self._geocoder.geocode(address)

    async def travel_matrix(
        self,
        sources: Sequence[GeoPoint],
        targets: Sequence[GeoPoint] | None = None,
        *,
        departure: datetime | None = None,
    ) -> TravelMatrix:
        targets = list(targets) if targets is not None else list(sources)
        sources = list(sources)

        # OSRM takes one coordinate list plus source/destination indices into
        # it. Sources go first, targets after; the returned matrix is indexed by
        # position within the sources/destinations lists (so cell [i][j] is the
        # i-th source to the j-th target), not by the raw coordinate index.
        all_points = sources + targets
        coord_str = ";".join(f"{p.longitude},{p.latitude}" for p in all_points)
        source_idx = range(len(sources))
        target_idx = range(len(sources), len(sources) + len(targets))
        params = {
            "annotations": "duration,distance",
            "sources": ";".join(str(i) for i in source_idx),
            "destinations": ";".join(str(i) for i in target_idx),
        }
        url = f"{self.base_url}/table/v1/driving/{coord_str}"
        try:
            response = await self._get_client().get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"OSRM table failed: {exc}") from exc

        if payload.get("code") != "Ok":
            raise AdapterUnavailable(f"OSRM table returned {payload.get('code')}")

        durations = payload.get("durations") or []
        distances = payload.get("distances")  # absent on the demo server
        legs: dict[tuple[int, int], Leg] = {}
        for i in range(len(sources)):
            for j in range(len(targets)):
                try:
                    dur = float(durations[i][j])
                except (IndexError, TypeError, ValueError):
                    dur = 0.0
                if distances is not None:
                    try:
                        dist = float(distances[i][j])
                    except (IndexError, TypeError, ValueError):
                        dist = 0.0
                else:
                    # Demo server: estimate distance from driving duration.
                    dist = dur / 3600.0 * self.speed_kmh * 1000.0
                legs[(i, j)] = Leg(duration_seconds=dur, distance_metres=dist)
        return TravelMatrix(legs=legs, degraded=distances is None, source=self.name)

    async def route_geometry(
        self, waypoints: Sequence[GeoPoint]
    ) -> list[list[float]] | None:
        points = list(waypoints)
        if len(points) < 2:
            return None
        coord_str = ";".join(f"{p.longitude},{p.latitude}" for p in points)
        url = f"{self.base_url}/route/v1/driving/{coord_str}"
        params = {"overview": "full", "geometries": "geojson", "steps": "false"}
        try:
            response = await self._get_client().get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Geometry is presentational; a failure should not break planning.
            logger.warning("osrm_route_geometry_failed", error=str(exc))
            return None
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return None
        geometry = payload["routes"][0].get("geometry", {})
        coords = geometry.get("coordinates")
        # OSRM returns [lon, lat] pairs, which is what GeoJSON and the map expect.
        return coords if isinstance(coords, list) and coords else None

    async def health_check(self) -> bool:
        try:
            pt = GeoPoint(latitude=1.3521, longitude=103.8198)
            await self.travel_matrix([pt], [pt])
            return True
        except AdapterUnavailable:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await self._geocoder.aclose()


# --------------------------------------------------------------------------- #
# Mapbox adapter
# --------------------------------------------------------------------------- #
class MapboxMappingAdapter(MappingAdapter):
    """Mapbox adapter.

    * Geocoding API for address -> coordinates.
    * Matrix API for the driving-time/distance matrix (fastest route).
    * Directions API for the road-following polyline drawn on the map.

    The standard Matrix/Directions profiles accept at most 25 coordinates per
    request; the optimiser's clusters are well within that.
    """

    name = "mapping:mapbox"

    GEOCODE_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json"
    MATRIX_URL = "https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coords}"
    DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox/driving/{coords}"
    #: Mapbox limit on coordinates per Matrix/Directions request.
    MAX_COORDS = 25

    def __init__(
        self,
        access_token: str,
        *,
        country: str = "sg",
        speed_kmh: float | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.access_token = access_token
        self.country = country
        self.speed_kmh = speed_kmh or settings.average_speed_kmh
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def geocode(self, address: str) -> list[GeocodeCandidate]:
        query = (address or "").strip()
        if not query:
            return []
        params: dict[str, Any] = {
            "access_token": self.access_token,
            "limit": 5,
            "types": "address,poi,place",
        }
        if self.country:
            params["country"] = self.country
        try:
            request_url = self.GEOCODE_URL.format(query=query)
            response = await self._get_client().get(request_url, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"Mapbox geocode failed: {exc}") from exc

        candidates: list[GeocodeCandidate] = []
        for feature in payload.get("features", []):
            try:
                lon, lat = feature["center"]  # Mapbox returns [lon, lat]
                # relevance is 0..1 and maps directly to our confidence notion.
                confidence = float(feature.get("relevance", 0.5))
                candidates.append(
                    GeocodeCandidate(
                        location=GeoPoint(latitude=lat, longitude=lon),
                        confidence=confidence,
                        formatted_address=feature.get("place_name", address),
                    )
                )
            except (KeyError, TypeError, ValueError):
                logger.warning("mapbox_geocode_candidate_unparseable", raw=feature)
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

        # Mapbox caps a single request at 25 coordinates (sources + targets). To
        # support larger matrices we tile the request into blocks of sources and
        # targets whose combined size stays within the limit, then stitch the
        # sub-results back into one matrix.
        max_block = self.MAX_COORDS // 2  # e.g. 12 sources x 12 targets per call
        legs: dict[tuple[int, int], Leg] = {}
        degraded_flag = False
        for si in range(0, len(sources), max_block):
            src_block = sources[si : si + max_block]
            for ti in range(0, len(targets), max_block):
                tgt_block = targets[ti : ti + max_block]
                block, block_degraded = await self._matrix_block(src_block, tgt_block)
                degraded_flag = degraded_flag or block_degraded
                for (bi, bj), leg in block.items():
                    legs[(si + bi, ti + bj)] = leg
        return TravelMatrix(legs=legs, degraded=degraded_flag, source=self.name)

    async def _matrix_block(
        self, sources: list[GeoPoint], targets: list[GeoPoint]
    ) -> tuple[dict[tuple[int, int], Leg], bool]:
        """One Matrix API call for a block within the 25-coordinate limit."""
        all_points = sources + targets
        coord_str = ";".join(f"{p.longitude},{p.latitude}" for p in all_points)
        source_idx = range(len(sources))
        target_idx = range(len(sources), len(sources) + len(targets))
        params = {
            "access_token": self.access_token,
            "annotations": "duration,distance",
            "sources": ";".join(str(i) for i in source_idx),
            "destinations": ";".join(str(i) for i in target_idx),
        }
        url = self.MATRIX_URL.format(coords=coord_str)
        try:
            response = await self._get_client().get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterUnavailable(f"Mapbox matrix failed: {exc}") from exc

        if payload.get("code") != "Ok":
            raise AdapterUnavailable(f"Mapbox matrix returned {payload.get('code')}")

        durations = payload.get("durations") or []
        distances = payload.get("distances")
        block: dict[tuple[int, int], Leg] = {}
        for i in range(len(sources)):
            for j in range(len(targets)):
                try:
                    dur = float(durations[i][j])
                except (IndexError, TypeError, ValueError):
                    dur = 0.0
                if distances is not None:
                    try:
                        dist = float(distances[i][j])
                    except (IndexError, TypeError, ValueError):
                        dist = 0.0
                else:
                    dist = dur / 3600.0 * self.speed_kmh * 1000.0
                block[(i, j)] = Leg(duration_seconds=dur, distance_metres=dist)
        return block, distances is None

    async def route_geometry(
        self, waypoints: Sequence[GeoPoint]
    ) -> list[list[float]] | None:
        points = list(waypoints)
        if len(points) < 2:
            return None
        if len(points) > self.MAX_COORDS:
            # Too many waypoints for one Directions call; skip geometry rather
            # than error (it is presentational only).
            logger.warning("mapbox_directions_too_many_waypoints", count=len(points))
            return None
        coord_str = ";".join(f"{p.longitude},{p.latitude}" for p in points)
        params = {
            "access_token": self.access_token,
            "geometries": "geojson",
            "overview": "full",
            "steps": "false",
        }
        url = self.DIRECTIONS_URL.format(coords=coord_str)
        try:
            response = await self._get_client().get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("mapbox_directions_failed", error=str(exc))
            return None
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return None
        coords = payload["routes"][0].get("geometry", {}).get("coordinates")
        return coords if isinstance(coords, list) and coords else None

    async def health_check(self) -> bool:
        try:
            await self.geocode("Singapore")
            return True
        except AdapterUnavailable:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_mapping_adapter: MappingAdapter | None = None


def get_mapping_adapter() -> MappingAdapter:
    global _mapping_adapter
    if _mapping_adapter is None:
        if settings.mapping_adapter == "mapbox" and settings.mapbox_access_token:
            _mapping_adapter = MapboxMappingAdapter(
                settings.mapbox_access_token,
                country=settings.mapbox_country,
                speed_kmh=settings.average_speed_kmh,
                timeout=settings.mapping_timeout_seconds,
            )
        elif settings.mapping_adapter == "osrm" and settings.osrm_base_url:
            _mapping_adapter = OsrmMappingAdapter(
                settings.osrm_base_url,
                speed_kmh=settings.average_speed_kmh,
                timeout=settings.mapping_timeout_seconds,
            )
        elif settings.mapping_adapter == "google" and settings.google_maps_api_key:
            _mapping_adapter = GoogleMappingAdapter(
                settings.google_maps_api_key,
                region=settings.google_maps_region,
                timeout=settings.mapping_timeout_seconds,
            )
        elif settings.mapping_adapter == "http" and settings.mapping_base_url:
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
