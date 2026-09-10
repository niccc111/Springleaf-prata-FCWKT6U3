"""FMS integration adapter — vehicle and driver upserts.

Task 9 — Requirements 2.1–2.6 (Properties 4, 5, 6).
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events as event_bus
from app.core.config import settings
from app.core.logging import get_logger
from app.core.serialisation import to_jsonable
from app.models.entities import Vehicle
from app.models.enums import EntityType, IntegrationSystem, VehicleSource
from app.schemas.geo import GeoPoint
from app.services.audit_service import audit_service
from app.services.integration_health import integration_health

logger = get_logger(__name__)

REDIS_CACHE_KEY = "roe:fms:vehicles"


@dataclass(slots=True)
class VehicleOutcome:
    created: bool = False
    updated: bool = False
    rejected: bool = False
    unknown_ref: bool = False
    vehicle_id: uuid.UUID | None = None
    errors: list[str] = field(default_factory=list)
    notification: str | None = None


def validate_vehicle_event(event: dict[str, Any]) -> list[str]:
    """Requirement 2.5 / Property 6 — name every missing or invalid field."""
    errors: list[str] = []

    if event.get("capacity_weight_kg") in (None, ""):
        errors.append("capacity_weight_kg is missing")
    else:
        try:
            if Decimal(str(event["capacity_weight_kg"])) <= 0:
                errors.append("capacity_weight_kg must be greater than 0")
        except (InvalidOperation, ValueError, TypeError):
            errors.append(
                f"capacity_weight_kg is not a number (received {event['capacity_weight_kg']!r})"
            )

    if _depot_of(event) is None:
        errors.append("depot_location is missing or is not a valid coordinate pair")

    if event.get("capacity_volume_m3") not in (None, ""):
        try:
            if Decimal(str(event["capacity_volume_m3"])) <= 0:
                errors.append("capacity_volume_m3 must be greater than 0")
        except (InvalidOperation, ValueError, TypeError):
            errors.append("capacity_volume_m3 is not a number")

    start = _parse_time(event.get("operating_hours_start"))
    end = _parse_time(event.get("operating_hours_end"))
    if event.get("operating_hours_start") and start is None:
        errors.append("operating_hours_start is not a valid HH:MM time")
    if event.get("operating_hours_end") and end is None:
        errors.append("operating_hours_end is not a valid HH:MM time")
    if start and end and start >= end:
        errors.append("operating_hours_start must be earlier than operating_hours_end")

    if not str(event.get("registration") or "").strip() and not event.get("external_ref"):
        errors.append("registration is missing")

    return errors


def _depot_of(event: dict[str, Any]) -> GeoPoint | None:
    raw = event.get("depot_location")
    if raw in (None, ""):
        if event.get("depot_latitude") not in (None, "") and event.get("depot_longitude") not in (
            None,
            "",
        ):
            raw = {"lat": event["depot_latitude"], "lon": event["depot_longitude"]}
        else:
            return None
    try:
        return GeoPoint.coerce(raw)
    except (ValueError, TypeError, KeyError):
        return None


def _parse_time(value: Any) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt).time()
        except ValueError:
            continue
    return None


class FmsAdapter:
    async def process_event(
        self,
        session: AsyncSession,
        event: dict[str, Any],
        *,
        acting_user: uuid.UUID | None = None,
    ) -> VehicleOutcome:
        external_ref = (
            str(event.get("external_ref") or event.get("vehicle_id") or event.get("id") or "")
            or None
        )
        event_type = str(event.get("event_type") or "update").lower()
        errors = validate_vehicle_event(event)

        existing: Vehicle | None = None
        if external_ref is not None:
            existing = await session.scalar(
                select(Vehicle).where(Vehicle.external_ref == external_ref)
            )

        if errors:
            # Requirement 2.5 — reject, force available=false, notify with the ref.
            message = (
                f"FMS vehicle '{external_ref or 'unknown'}' was rejected: " + "; ".join(errors)
            )
            if existing is not None:
                old_state = existing.to_dict()
                existing.available = False
                existing.updated_at = datetime.now(UTC)
                await session.flush()
                await audit_service.record_change(
                    session,
                    entity_id=existing.vehicle_id,
                    entity_type=EntityType.VEHICLE,
                    action="vehicle.rejected.fms",
                    acting_user=acting_user,
                    old_state=old_state,
                    new_state=existing.to_dict(),
                )
            await self._notify(message, external_ref, errors)
            logger.warning("fms_event_rejected", external_ref=external_ref, errors=errors)
            return VehicleOutcome(
                rejected=True,
                errors=errors,
                notification=message,
                vehicle_id=existing.vehicle_id if existing else None,
            )

        depot = _depot_of(event)
        assert depot is not None
        payload = {
            "registration": str(event.get("registration") or external_ref or "").strip(),
            "capacity_weight_kg": Decimal(str(event["capacity_weight_kg"])),
            "capacity_volume_m3": (
                Decimal(str(event["capacity_volume_m3"]))
                if event.get("capacity_volume_m3") not in (None, "")
                else None
            ),
            "depot_location": depot,
            "operating_hours_start": _parse_time(event.get("operating_hours_start"))
            or time(8, 0),
            "operating_hours_end": _parse_time(event.get("operating_hours_end")) or time(18, 0),
            "available": bool(event.get("available", True)),
            "driver_id": _as_uuid(event.get("driver_id")),
            "driver_name": event.get("driver_name"),
        }

        # Requirement 2.3 / Property 5 — upsert, never duplicate.
        if existing is not None:
            old_state = existing.to_dict()
            for field_name, value in payload.items():
                setattr(existing, field_name, value)
            existing.updated_at = datetime.now(UTC)
            await session.flush()
            await audit_service.record_change(
                session,
                entity_id=existing.vehicle_id,
                entity_type=EntityType.VEHICLE,
                action="vehicle.updated.fms",
                acting_user=acting_user,
                old_state=old_state,
                new_state=existing.to_dict(),
            )
            await integration_health.record_success(session, IntegrationSystem.FMS)
            await self._cache_vehicle(existing)
            logger.info("fms_vehicle_updated", external_ref=external_ref)
            return VehicleOutcome(updated=True, vehicle_id=existing.vehicle_id)

        # Requirement 2.4 — an update for an unknown ref is logged and notified.
        if event_type not in ("new_registration", "create", "created"):
            message = (
                f"Received an FMS update for unknown vehicle reference '{external_ref}'. "
                "No vehicle record was created."
            )
            await self._notify(message, external_ref, [])
            logger.warning("fms_unknown_external_ref", external_ref=external_ref)
            return VehicleOutcome(unknown_ref=True, notification=message)

        # Requirement 2.2 / Property 4 — new registration.
        vehicle = Vehicle(
            vehicle_id=uuid.uuid4(),
            source=VehicleSource.FMS.value,
            external_ref=external_ref,
            **payload,
        )
        session.add(vehicle)
        await session.flush()
        await audit_service.record_change(
            session,
            entity_id=vehicle.vehicle_id,
            entity_type=EntityType.VEHICLE,
            action="vehicle.created.fms",
            acting_user=acting_user,
            old_state=None,
            new_state=vehicle.to_dict(),
        )
        await integration_health.record_success(session, IntegrationSystem.FMS)
        await self._cache_vehicle(vehicle)
        logger.info("fms_vehicle_created", external_ref=external_ref)
        return VehicleOutcome(created=True, vehicle_id=vehicle.vehicle_id)

    async def process_batch(
        self,
        session: AsyncSession,
        events_batch: list[dict[str, Any]],
        *,
        acting_user: uuid.UUID | None = None,
    ) -> dict[str, int]:
        stats = {"created": 0, "updated": 0, "rejected": 0, "unknown_ref": 0}
        for event in events_batch:
            outcome = await self.process_event(session, event, acting_user=acting_user)
            if outcome.created:
                stats["created"] += 1
            elif outcome.updated:
                stats["updated"] += 1
            elif outcome.rejected:
                stats["rejected"] += 1
            elif outcome.unknown_ref:
                stats["unknown_ref"] += 1
        return stats

    async def _notify(
        self, message: str, external_ref: str | None, errors: list[str]
    ) -> None:
        await event_bus.publish(
            event_bus.CONNECTIVITY_WARNING
            if not external_ref
            else "fms.notification",
            {
                "system": IntegrationSystem.FMS.value,
                "name": "Fleet Management System",
                "message": message,
                "external_ref": external_ref,
                "errors": errors,
            },
        )

    # ------------------------------------------------------------------ #
    # Redis last-known-good cache (Requirement 2.6)
    # ------------------------------------------------------------------ #
    async def _cache_vehicle(self, vehicle: Vehicle) -> None:
        redis = await event_bus.event_bus._get_redis()
        if redis is None:
            return
        try:
            await redis.hset(
                REDIS_CACHE_KEY,
                str(vehicle.vehicle_id),
                json.dumps(to_jsonable(vehicle.to_dict())),
            )
        except Exception as exc:  # pragma: no cover - cache is best effort
            logger.warning("fms_cache_write_failed", error=str(exc))

    async def cached_vehicles(self) -> list[dict[str, Any]]:
        """Last-known vehicle state served while the FMS is unreachable."""
        redis = await event_bus.event_bus._get_redis()
        if redis is None:
            return []
        try:
            raw = await redis.hgetall(REDIS_CACHE_KEY)
        except Exception:  # pragma: no cover
            return []
        return [json.loads(value) for value in raw.values()]


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
class FmsSource(ABC):
    name = "fms"

    @abstractmethod
    async def fetch_events(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...


@dataclass
class MockFmsSource(FmsSource):
    name: str = "fms:mock"
    queue: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True

    def publish(self, event: dict[str, Any]) -> None:
        self.queue.append(event)

    async def fetch_events(self) -> list[dict[str, Any]]:
        if not self.available:
            raise ConnectionError("FMS queue unavailable")
        drained, self.queue = self.queue, []
        return drained

    async def health_check(self) -> bool:
        return self.available


class HttpFmsSource(FmsSource):
    name = "fms:http"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._cursor: str | None = None

    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.AsyncClient(base_url=self.base_url, timeout=15.0, headers=headers)

    async def fetch_events(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            params = {"cursor": self._cursor} if self._cursor else {}
            response = await client.get("/vehicles/events", params=params)
            response.raise_for_status()
            payload = response.json()
        self._cursor = payload.get("next_cursor", self._cursor)
        return list(payload.get("events", []))

    async def health_check(self) -> bool:
        try:
            async with self._client() as client:
                return (await client.get("/health")).status_code < 500
        except httpx.HTTPError:
            return False


fms_adapter = FmsAdapter()
_fms_source: FmsSource | None = None


def get_fms_source() -> FmsSource:
    global _fms_source
    if _fms_source is None:
        if settings.fms_adapter == "http" and settings.fms_base_url:
            _fms_source = HttpFmsSource(settings.fms_base_url, settings.fms_api_key)
        else:
            _fms_source = MockFmsSource()
    return _fms_source


def set_fms_source(source: FmsSource | None) -> None:
    global _fms_source
    _fms_source = source
