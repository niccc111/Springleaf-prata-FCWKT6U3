"""Geocoding, travel-time matrices, and the durable geocoding retry queue.

Tasks 7.1–7.3 — Requirements 7.4, 7.5, 15.1–15.7 (Properties 30, 31).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailable, GeocodeCandidate, Leg, TravelMatrix
from app.adapters.mapping import MappingAdapter, get_mapping_adapter
from app.core.config import settings
from app.core.logging import get_logger
from app.models.entities import GeocodingQueue, Order, TravelTimeSample
from app.models.enums import (
    AlertEntityType,
    AlertSeverity,
    AlertType,
    IntegrationSystem,
    OrderStatus,
)
from app.schemas.geo import GeoPoint, validate_manual_coordinates  # noqa: F401 (re-exported)
from app.services.alert_service import alert_service
from app.services.integration_health import integration_health

logger = get_logger(__name__)

#: Upper bound on legs folded into the historical averages in one call. A
#: fleet-wide matrix has O(n^2) legs; route-sized matrices are far below this.
MAX_SAMPLES_PER_CALL = 4096


@dataclass(slots=True)
class GeocodeOutcome:
    """Result of resolving one address."""

    location: GeoPoint | None
    confidence: float | None
    needs_review: bool
    resolved: bool
    unavailable: bool = False
    error: str | None = None
    candidates: int = 0


class GeocodingService:
    def __init__(self, adapter: MappingAdapter | None = None) -> None:
        self._adapter = adapter

    @property
    def adapter(self) -> MappingAdapter:
        return self._adapter or get_mapping_adapter()

    # ------------------------------------------------------------------ #
    # Geocoding
    # ------------------------------------------------------------------ #
    def apply_confidence_policy(self, candidates: Sequence[GeocodeCandidate]) -> GeocodeOutcome:
        """Confidence-threshold logic (Requirements 15.2/15.3, Property 30).

        * exactly one candidate with confidence >= threshold → store, no review
        * multiple candidates, or best confidence < threshold → store best,
          flag for dispatcher review
        * no candidates → unresolved
        """
        if not candidates:
            return GeocodeOutcome(
                location=None, confidence=None, needs_review=False, resolved=False, candidates=0
            )
        best = max(candidates, key=lambda c: c.confidence)
        needs_review = (
            len(candidates) > 1 or best.confidence < settings.geocode_confidence_threshold
        )
        return GeocodeOutcome(
            location=best.location,
            confidence=best.confidence,
            needs_review=needs_review,
            resolved=True,
            candidates=len(candidates),
        )

    async def geocode_address(self, address: str) -> GeocodeOutcome:
        """Resolve an address. Never raises — unavailability is reported in the outcome."""
        try:
            candidates = await self.adapter.geocode(address)
        except AdapterUnavailable as exc:
            logger.warning("geocode_unavailable", address=address, error=str(exc))
            return GeocodeOutcome(
                location=None,
                confidence=None,
                needs_review=False,
                resolved=False,
                unavailable=True,
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("geocode_failed", address=address, error=str(exc))
            return GeocodeOutcome(
                location=None,
                confidence=None,
                needs_review=False,
                resolved=False,
                unavailable=True,
                error=str(exc),
            )
        return self.apply_confidence_policy(candidates)

    async def geocode_order(
        self,
        session: AsyncSession,
        order: Order,
        *,
        acting_user: uuid.UUID | None = None,
    ) -> GeocodeOutcome:
        """Resolve an order's delivery address and apply the outcome to the row.

        * resolved (high confidence) → store location, clear review flag
        * resolved (low confidence / ambiguous) → store best, set ``geocode_review``
        * unresolvable → null location + Impossible Order Alert (Requirement 15.4)
        * service unavailable → enqueue for retry (Requirement 15.6)
        """
        outcome = await self.geocode_address(order.delivery_address)

        if outcome.unavailable:
            await integration_health.record_failure(
                session,
                IntegrationSystem.MAPPING,
                outcome.error or "Mapping Service unavailable",
                threshold_seconds=settings.mapping_unavailable_threshold_seconds,
            )
            await self.enqueue_retry(session, order, error=outcome.error)
            order.status = OrderStatus.UNASSIGNED.value
            return outcome

        await integration_health.record_success(session, IntegrationSystem.MAPPING)

        if not outcome.resolved:
            order.delivery_location = None
            order.geocode_review = True
            order.geocode_confidence = None
            await alert_service.raise_alert(
                session,
                alert_type=AlertType.IMPOSSIBLE_ORDER,
                severity=AlertSeverity.CRITICAL,
                entity_type=AlertEntityType.ORDER,
                entity_id=order.order_id,
                message=(
                    f"Address could not be geocoded: '{order.delivery_address}'. "
                    "Supply coordinates manually to continue."
                ),
                context={"reason": "geocoding_failed", "address": order.delivery_address},
                acting_user=acting_user,
            )
            await self.resolve_queue_entry(session, order.order_id, exhausted=True)
            return outcome

        order.delivery_location = outcome.location
        order.geocode_confidence = outcome.confidence
        order.geocode_review = outcome.needs_review
        await self.resolve_queue_entry(session, order.order_id)
        return outcome

    # ------------------------------------------------------------------ #
    # Retry queue (Requirements 15.6, 15.7)
    # ------------------------------------------------------------------ #
    async def enqueue_retry(
        self, session: AsyncSession, order: Order, *, error: str | None = None
    ) -> GeocodingQueue:
        next_retry = datetime.now(UTC) + timedelta(
            seconds=settings.geocode_retry_interval_seconds
        )
        stmt = (
            pg_insert(GeocodingQueue)
            .values(
                queue_id=uuid.uuid4(),
                order_id=order.order_id,
                address=order.delivery_address,
                attempts=0,
                next_retry=next_retry,
                last_error=error,
                resolved=False,
                exhausted=False,
            )
            .on_conflict_do_update(
                index_elements=[GeocodingQueue.order_id],
                set_={"address": order.delivery_address, "last_error": error, "resolved": False},
            )
            .returning(GeocodingQueue)
        )
        return (await session.execute(stmt)).scalar_one()

    async def resolve_queue_entry(
        self, session: AsyncSession, order_id: uuid.UUID, *, exhausted: bool = False
    ) -> None:
        entry = await session.scalar(
            select(GeocodingQueue).where(GeocodingQueue.order_id == order_id)
        )
        if entry is not None:
            entry.resolved = True
            entry.exhausted = exhausted

    async def due_queue_entries(self, session: AsyncSession, limit: int = 100) -> list[GeocodingQueue]:
        result = await session.execute(
            select(GeocodingQueue)
            .where(
                and_(
                    GeocodingQueue.resolved.is_(False),
                    GeocodingQueue.exhausted.is_(False),
                    GeocodingQueue.next_retry <= datetime.now(UTC),
                )
            )
            .order_by(GeocodingQueue.next_retry)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def process_retry_queue(
        self, session: AsyncSession, *, acting_user: uuid.UUID | None = None
    ) -> dict[str, int]:
        """One pass of the retry worker. Returns counters for observability."""
        stats = {"processed": 0, "resolved": 0, "exhausted": 0, "deferred": 0}
        for entry in await self.due_queue_entries(session):
            stats["processed"] += 1
            order = await session.get(Order, entry.order_id)
            if order is None:
                entry.resolved = True
                continue

            entry.attempts += 1
            outcome = await self.geocode_address(entry.address)

            if outcome.resolved:
                order.delivery_location = outcome.location
                order.geocode_confidence = outcome.confidence
                order.geocode_review = outcome.needs_review
                entry.resolved = True
                entry.last_error = None
                stats["resolved"] += 1
                await integration_health.record_success(session, IntegrationSystem.MAPPING)
                continue

            entry.last_error = outcome.error or "address could not be resolved"
            if entry.attempts >= settings.geocode_max_attempts:
                entry.exhausted = True
                stats["exhausted"] += 1
                order.delivery_location = None
                order.geocode_review = True
                await alert_service.raise_alert(
                    session,
                    alert_type=AlertType.IMPOSSIBLE_ORDER,
                    severity=AlertSeverity.CRITICAL,
                    entity_type=AlertEntityType.ORDER,
                    entity_id=order.order_id,
                    message=(
                        f"Geocoding failed after {entry.attempts} attempts for "
                        f"'{entry.address}'. Supply coordinates manually to continue."
                    ),
                    context={
                        "reason": "geocoding_retries_exhausted",
                        "attempts": entry.attempts,
                        "address": entry.address,
                    },
                    acting_user=acting_user,
                )
            else:
                entry.next_retry = datetime.now(UTC) + timedelta(
                    seconds=settings.geocode_retry_interval_seconds
                )
                stats["deferred"] += 1
        return stats

    # ------------------------------------------------------------------ #
    # Travel-time matrix (Requirements 7.1, 7.4, 7.5)
    # ------------------------------------------------------------------ #
    async def get_travel_time_matrix(
        self,
        session: AsyncSession,
        waypoints: Sequence[GeoPoint],
        *,
        departure: datetime | None = None,
        record_history: bool = True,
    ) -> TravelMatrix:
        """Traffic-adjusted matrix, falling back to historical averages.

        On Mapping Service unavailability the cached averages from
        ``travel_time_samples`` are used and ``TravelMatrix.degraded`` is set so
        callers can flag affected routes (Requirement 7.4).

        ``record_history`` folds the observed legs into the running averages.
        Callers building a fleet-wide solver matrix pass ``False``: an N x N
        matrix over hundreds of waypoints has tens of thousands of legs, most of
        which are never driven. The legs of the routes actually produced are
        recorded instead.
        """
        if not waypoints:
            return TravelMatrix(legs={}, degraded=False, source="empty")
        try:
            matrix = await self.adapter.travel_matrix(waypoints, departure=departure)
            await integration_health.record_success(session, IntegrationSystem.MAPPING)
            if record_history:
                await self.record_samples(session, waypoints, matrix)
            return matrix
        except AdapterUnavailable as exc:
            logger.warning("travel_matrix_unavailable", error=str(exc))
            await integration_health.record_failure(
                session,
                IntegrationSystem.MAPPING,
                str(exc),
                threshold_seconds=settings.mapping_unavailable_threshold_seconds,
            )
            return await self.historical_matrix(session, waypoints)

    async def historical_matrix(
        self, session: AsyncSession, waypoints: Sequence[GeoPoint]
    ) -> TravelMatrix:
        """Fallback matrix from stored historical averages, then geometry."""
        keys = [p.geohash6() for p in waypoints]
        rows = await session.execute(
            select(TravelTimeSample).where(
                and_(
                    TravelTimeSample.from_key.in_(keys),
                    TravelTimeSample.to_key.in_(keys),
                )
            )
        )
        cached = {(r.from_key, r.to_key): r for r in rows.scalars().all()}

        legs: dict[tuple[int, int], Leg] = {}
        for i, origin in enumerate(waypoints):
            for j, destination in enumerate(waypoints):
                if i == j:
                    legs[(i, j)] = Leg(0.0, 0.0)
                    continue
                sample = cached.get((keys[i], keys[j]))
                if sample is not None and sample.sample_count > 0:
                    legs[(i, j)] = Leg(sample.avg_duration_seconds, sample.avg_distance_metres)
                else:
                    # Last-resort estimate: straight-line geometry at average speed.
                    road_km = origin.haversine_km(destination) * 1.32
                    legs[(i, j)] = Leg(
                        duration_seconds=road_km / max(settings.average_speed_kmh, 1.0) * 3600.0,
                        distance_metres=road_km * 1000.0,
                    )
        return TravelMatrix(legs=legs, degraded=True, source="historical")

    async def record_samples(
        self,
        session: AsyncSession,
        waypoints: Sequence[GeoPoint],
        matrix: TravelMatrix,
        *,
        limit: int = MAX_SAMPLES_PER_CALL,
    ) -> int:
        """Fold observed legs into the running historical averages.

        Every distinct leg goes in a single multi-row upsert — one round trip
        rather than one per leg — and the batch is capped so an oversized matrix
        can never stall the caller.
        """
        keys = [p.geohash6() for p in waypoints]
        rows: dict[tuple[str, str], dict[str, object]] = {}
        for (i, j), leg in matrix.legs.items():
            if i == j or i >= len(keys) or j >= len(keys):
                continue
            pair = (keys[i], keys[j])
            if pair[0] == pair[1] or pair in rows:
                continue
            rows[pair] = {
                "from_key": pair[0],
                "to_key": pair[1],
                "sample_count": 1,
                "avg_duration_seconds": leg.duration_seconds,
                "avg_distance_metres": leg.distance_metres,
            }
            if len(rows) >= limit:
                break

        if not rows:
            return 0

        statement = pg_insert(TravelTimeSample).values(list(rows.values()))
        # `excluded` carries the incoming row, so the running mean folds the new
        # observation in without a read-modify-write cycle.
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[TravelTimeSample.from_key, TravelTimeSample.to_key],
                set_={
                    "sample_count": TravelTimeSample.sample_count + 1,
                    "avg_duration_seconds": (
                        TravelTimeSample.avg_duration_seconds * TravelTimeSample.sample_count
                        + statement.excluded.avg_duration_seconds
                    )
                    / (TravelTimeSample.sample_count + 1),
                    "avg_distance_metres": (
                        TravelTimeSample.avg_distance_metres * TravelTimeSample.sample_count
                        + statement.excluded.avg_distance_metres
                    )
                    / (TravelTimeSample.sample_count + 1),
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        return len(rows)


geocoding_service = GeocodingService()
