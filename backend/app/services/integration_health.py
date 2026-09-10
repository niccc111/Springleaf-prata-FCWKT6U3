"""Connectivity tracking for OMS, FMS, Mapping, and Delivery Platform.

Requirements 1.5, 2.6, 7.4 — a warning is surfaced to every active dispatcher
while a system is degraded and cleared automatically on recovery.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.logging import get_logger
from app.models.entities import IntegrationStatus
from app.models.enums import IntegrationSystem

logger = get_logger(__name__)

FRIENDLY_NAMES = {
    IntegrationSystem.OMS: "Order Management System",
    IntegrationSystem.FMS: "Fleet Management System",
    IntegrationSystem.MAPPING: "Mapping Service",
    IntegrationSystem.DELIVERY_PLATFORM: "Delivery Platform",
}


class IntegrationHealthService:
    async def record_success(
        self, session: AsyncSession, system: IntegrationSystem
    ) -> IntegrationStatus:
        now = datetime.now(UTC)
        current = await session.get(IntegrationStatus, system.value)
        was_degraded = current is not None and not current.healthy

        stmt = (
            pg_insert(IntegrationStatus)
            .values(
                system=system.value,
                healthy=True,
                message=None,
                last_success_at=now,
                degraded_since=None,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[IntegrationStatus.system],
                set_={
                    "healthy": True,
                    "message": None,
                    "last_success_at": now,
                    "degraded_since": None,
                    "updated_at": now,
                },
            )
            .returning(IntegrationStatus)
        )
        # populate_existing makes the RETURNING row overwrite any instance
        # already in the identity map; without it a caller that loaded the row
        # earlier in this session would keep reading the stale state.
        status = (
            await session.execute(stmt, execution_options={"populate_existing": True})
        ).scalar_one()
        if was_degraded:
            logger.info("integration_restored", system=system.value)
            await events.publish(
                events.CONNECTIVITY_RESTORED,
                {"system": system.value, "name": FRIENDLY_NAMES[system]},
            )
        return status

    async def record_failure(
        self,
        session: AsyncSession,
        system: IntegrationSystem,
        message: str,
        *,
        threshold_seconds: int = 0,
    ) -> IntegrationStatus:
        """Mark a system degraded.

        The warning is only broadcast once ``threshold_seconds`` have elapsed
        since the first failure, implementing the "unavailable for more than N
        seconds" wording in Requirements 1.5 and 7.4.
        """
        now = datetime.now(UTC)
        current = await session.get(IntegrationStatus, system.value)
        degraded_since = (
            current.degraded_since
            if current is not None and current.degraded_since is not None
            else now
        )
        already_warned = current is not None and not current.healthy

        breached = now - degraded_since >= timedelta(seconds=threshold_seconds)
        healthy = not breached

        stmt = (
            pg_insert(IntegrationStatus)
            .values(
                system=system.value,
                healthy=healthy,
                message=message,
                last_success_at=current.last_success_at if current else None,
                degraded_since=degraded_since,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[IntegrationStatus.system],
                set_={
                    "healthy": healthy,
                    "message": message,
                    "degraded_since": degraded_since,
                    "updated_at": now,
                },
            )
            .returning(IntegrationStatus)
        )
        status = (
            await session.execute(stmt, execution_options={"populate_existing": True})
        ).scalar_one()

        if breached and not already_warned:
            logger.warning("integration_degraded", system=system.value, message=message)
            await events.publish(
                events.CONNECTIVITY_WARNING,
                {
                    "system": system.value,
                    "name": FRIENDLY_NAMES[system],
                    "message": message,
                    "degraded_since": degraded_since.isoformat(),
                },
            )
        return status

    async def list_statuses(self, session: AsyncSession) -> list[IntegrationStatus]:
        result = await session.execute(
            select(IntegrationStatus)
            .order_by(IntegrationStatus.system)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def is_healthy(self, session: AsyncSession, system: IntegrationSystem) -> bool:
        status = await session.get(
            IntegrationStatus, system.value, populate_existing=True
        )
        return status is None or status.healthy


integration_health = IntegrationHealthService()
