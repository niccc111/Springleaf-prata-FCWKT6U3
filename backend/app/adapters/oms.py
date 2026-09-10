"""OMS integration adapter — ingests order events.

Task 8 — Requirements 1.1–1.6 (Properties 1, 2, 3).

Transport is pluggable: ``MockOmsSource`` replays a local queue (and backs the
``POST /integrations/oms/events`` webhook used for demos and tests), while
``HttpOmsSource`` polls a real OMS. Event *processing* is identical either way.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.entities import Order
from app.models.enums import (
    AlertEntityType,
    AlertSeverity,
    AlertType,
    EntityType,
    IntegrationSystem,
    OrderSource,
    OrderStatus,
    Priority,
)
from app.schemas.geo import GeoPoint
from app.services.alert_service import alert_service
from app.services.audit_service import audit_service
from app.services.geocoding_service import geocoding_service
from app.services.integration_health import integration_health

logger = get_logger(__name__)


@dataclass(slots=True)
class IngestOutcome:
    created: bool
    duplicate: bool = False
    rejected: bool = False
    order_id: uuid.UUID | None = None
    errors: list[str] = field(default_factory=list)


def validate_order_event(event: dict[str, Any]) -> list[str]:
    """Field validation for an inbound OMS event (Requirements 1.2, 1.3).

    Returns a list of human-readable messages that each name the offending
    field, which is what Property 1 asserts on.
    """
    errors: list[str] = []

    has_coords = _extract_location(event) is not None
    address = str(event.get("delivery_address") or "").strip()
    if not has_coords and not address:
        errors.append(
            "delivery_location is missing and no delivery_address was supplied to geocode from"
        )

    if "cargo_weight_kg" not in event or event.get("cargo_weight_kg") in (None, ""):
        errors.append("cargo_weight_kg is missing")
    else:
        try:
            weight = Decimal(str(event["cargo_weight_kg"]))
        except (InvalidOperation, ValueError, TypeError):
            errors.append(
                f"cargo_weight_kg is not a number (received {event['cargo_weight_kg']!r})"
            )
        else:
            if weight <= 0:
                errors.append(
                    f"cargo_weight_kg must be greater than 0 (received {weight})"
                )

    if event.get("cargo_volume_m3") not in (None, ""):
        try:
            if Decimal(str(event["cargo_volume_m3"])) < 0:
                errors.append("cargo_volume_m3 must be greater than or equal to 0")
        except (InvalidOperation, ValueError, TypeError):
            errors.append(
                f"cargo_volume_m3 is not a number (received {event['cargo_volume_m3']!r})"
            )

    start, end = _parse_dt(event.get("time_window_start")), _parse_dt(event.get("time_window_end"))
    if event.get("time_window_start") and start is None:
        errors.append("time_window_start is not a valid ISO 8601 datetime")
    if event.get("time_window_end") and end is None:
        errors.append("time_window_end is not a valid ISO 8601 datetime")
    if start and end and start > end:
        errors.append("time_window_start must be at or before time_window_end")

    priority = str(event.get("priority") or Priority.STANDARD.value).lower()
    if priority not in (Priority.STANDARD.value, Priority.PRIORITY.value):
        errors.append(f"priority must be 'standard' or 'priority' (received {priority!r})")

    return errors


def _extract_location(event: dict[str, Any]) -> GeoPoint | None:
    raw = event.get("delivery_location")
    if raw in (None, ""):
        if event.get("latitude") not in (None, "") and event.get("longitude") not in (None, ""):
            raw = {"lat": event["latitude"], "lon": event["longitude"]}
        else:
            return None
    try:
        return GeoPoint.coerce(raw)
    except (ValueError, TypeError, KeyError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def external_ref_of(event: dict[str, Any]) -> str | None:
    for key in ("external_ref", "order_id", "oms_order_id", "id", "reference"):
        value = event.get(key)
        if value not in (None, ""):
            return str(value)
    return None


class OmsAdapter:
    async def process_event(
        self,
        session: AsyncSession,
        event: dict[str, Any],
        *,
        acting_user: uuid.UUID | None = None,
    ) -> IngestOutcome:
        """Validate → deduplicate → geocode → persist (design pseudocode)."""
        errors = validate_order_event(event)
        external_ref = external_ref_of(event)

        if errors:
            # Requirements 1.2/1.3 — reject, create nothing, alert with field names.
            reference = external_ref or "unknown"
            await alert_service.raise_alert(
                session,
                alert_type=AlertType.IMPOSSIBLE_ORDER,
                severity=AlertSeverity.CRITICAL,
                entity_type=AlertEntityType.ORDER,
                # No Order row exists, so the alert is keyed by a deterministic
                # id derived from the OMS reference.
                entity_id=uuid.uuid5(uuid.NAMESPACE_URL, f"oms-reject:{reference}"),
                message=(
                    f"OMS order '{reference}' was rejected and not imported: " + "; ".join(errors)
                ),
                context={"external_ref": external_ref, "errors": errors, "source": "OMS"},
                acting_user=acting_user,
            )
            logger.warning("oms_event_rejected", external_ref=external_ref, errors=errors)
            return IngestOutcome(created=False, rejected=True, errors=errors)

        # Requirement 1.6 / Property 3 — duplicates are silently discarded.
        if external_ref is not None:
            existing = await session.scalar(
                select(Order).where(Order.external_ref == external_ref)
            )
            if existing is not None:
                logger.info("oms_event_duplicate", external_ref=external_ref)
                return IngestOutcome(
                    created=False, duplicate=True, order_id=existing.order_id
                )

        location = _extract_location(event)
        order = Order(
            order_id=uuid.uuid4(),
            source=OrderSource.OMS.value,
            delivery_address=str(event.get("delivery_address") or "").strip()
            or (f"{location.latitude:.5f}, {location.longitude:.5f}" if location else ""),
            delivery_location=location,
            pickup_location=(
                GeoPoint.coerce(event["pickup_location"])
                if event.get("pickup_location")
                else None
            ),
            cargo_weight_kg=Decimal(str(event["cargo_weight_kg"])),
            cargo_volume_m3=(
                Decimal(str(event["cargo_volume_m3"]))
                if event.get("cargo_volume_m3") not in (None, "")
                else None
            ),
            time_window_start=_parse_dt(event.get("time_window_start")),
            time_window_end=_parse_dt(event.get("time_window_end")),
            priority=str(event.get("priority") or Priority.STANDARD.value).lower(),
            status=OrderStatus.UNASSIGNED.value,
            service_duration_min=(
                int(event["service_duration_min"])
                if event.get("service_duration_min") not in (None, "")
                else settings.default_service_duration_min
            ),
            external_ref=external_ref,
        )
        session.add(order)
        await session.flush()

        if order.delivery_location is None and order.delivery_address:
            await geocoding_service.geocode_order(session, order, acting_user=acting_user)

        await audit_service.record_change(
            session,
            entity_id=order.order_id,
            entity_type=EntityType.ORDER,
            action="order.ingested.oms",
            acting_user=acting_user,
            old_state=None,
            new_state=order.to_dict(),
        )
        await integration_health.record_success(session, IntegrationSystem.OMS)
        logger.info(
            "oms_order_ingested", order_id=str(order.order_id), external_ref=external_ref
        )
        return IngestOutcome(created=True, order_id=order.order_id)

    async def process_batch(
        self,
        session: AsyncSession,
        events_batch: list[dict[str, Any]],
        *,
        acting_user: uuid.UUID | None = None,
    ) -> dict[str, int]:
        stats = {"created": 0, "duplicates": 0, "rejected": 0}
        for event in events_batch:
            outcome = await self.process_event(session, event, acting_user=acting_user)
            if outcome.created:
                stats["created"] += 1
            elif outcome.duplicate:
                stats["duplicates"] += 1
            elif outcome.rejected:
                stats["rejected"] += 1
        return stats


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
class OmsSource(ABC):
    name = "oms"

    @abstractmethod
    async def fetch_events(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...


@dataclass
class MockOmsSource(OmsSource):
    """In-memory queue. Push events via the webhook endpoint or ``publish``."""

    name: str = "oms:mock"
    queue: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True

    def publish(self, event: dict[str, Any]) -> None:
        self.queue.append(event)

    async def fetch_events(self) -> list[dict[str, Any]]:
        if not self.available:
            raise ConnectionError("OMS queue unavailable")
        drained, self.queue = self.queue, []
        return drained

    async def health_check(self) -> bool:
        return self.available


class HttpOmsSource(OmsSource):
    name = "oms:http"

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
            response = await client.get("/orders/events", params=params)
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


oms_adapter = OmsAdapter()
_oms_source: OmsSource | None = None


def get_oms_source() -> OmsSource:
    global _oms_source
    if _oms_source is None:
        if settings.oms_adapter == "http" and settings.oms_base_url:
            _oms_source = HttpOmsSource(settings.oms_base_url, settings.oms_api_key)
        else:
            _oms_source = MockOmsSource()
    return _oms_source


def set_oms_source(source: OmsSource | None) -> None:
    global _oms_source
    _oms_source = source
