"""Delivery Platform export with exponential back-off retry.

Task 14.1 — Requirements 13.1–13.5 (Property 28).

Back-off schedule (design § Delivery Platform Adapter): the initial attempt
fires immediately, then retries wait 5s, 10s, and 20s.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.delivery_platform import get_delivery_platform_adapter
from app.core.config import settings
from app.core.errors import InvalidStateTransition, NotFound
from app.core.logging import get_logger
from app.models.entities import ExportJob, Route
from app.models.enums import (
    AlertEntityType,
    AlertSeverity,
    AlertType,
    ExportStatus,
    IntegrationSystem,
    RouteStatus,
)
from app.services.alert_service import alert_service
from app.services.integration_health import integration_health
from app.services.route_math import as_utc
from app.services.route_service import route_service

logger = get_logger(__name__)


def backoff_schedule() -> list[float]:
    """Delays preceding retries 1..N: 5s, 10s, 20s (Property 28)."""
    return settings.export_backoff_schedule()


class ExportService:
    async def build_payload(self, session: AsyncSession, route: Route) -> dict[str, Any]:
        vehicle = route.vehicle
        return {
            "route_id": str(route.route_id),
            "vehicle_registration": vehicle.registration if vehicle else None,
            "driver_id": str(route.driver_id) if route.driver_id else None,
            "total_distance_km": float(route.total_distance_km),
            "total_duration_min": route.total_duration_min,
            "stops": [
                {
                    "sequence": stop.sequence_number,
                    "address": stop.address,
                    "lat": stop.location.latitude,
                    "lon": stop.location.longitude,
                    "eta_utc": as_utc(stop.eta).isoformat(),
                    "order_ids": [str(link.order_id) for link in stop.stop_orders],
                    "service_duration_min": stop.service_duration_min,
                }
                for stop in sorted(route.stops, key=lambda s: s.sequence_number)
            ],
        }

    async def enqueue_export(
        self,
        session: AsyncSession,
        route_id: uuid.UUID,
        acting_user: uuid.UUID | None,
        *,
        cancel_in_progress: bool = True,
    ) -> ExportJob:
        """Create a fresh export job, cancelling any in-flight retry sequence.

        Requirement 13.5 — a manual re-trigger always supersedes a running
        sequence for the same route.
        """
        route = await route_service.get_route(session, route_id)
        if RouteStatus(route.status) not in (RouteStatus.APPROVED, RouteStatus.DISPATCHED):
            raise InvalidStateTransition(
                f"Only approved routes can be exported; route is '{route.status}'."
            )
        if cancel_in_progress:
            await self.cancel_active_jobs(session, route_id)

        job = ExportJob(
            job_id=uuid.uuid4(),
            route_id=route_id,
            status=ExportStatus.PENDING.value,
            attempts=0,
            attempt_log=[],
            initiated_by=acting_user,
        )
        session.add(job)
        await session.flush()
        return job

    async def cancel_active_jobs(self, session: AsyncSession, route_id: uuid.UUID) -> int:
        result = await session.execute(
            select(ExportJob).where(
                and_(
                    ExportJob.route_id == route_id,
                    ExportJob.status.in_(
                        [ExportStatus.PENDING.value, ExportStatus.IN_PROGRESS.value]
                    ),
                )
            )
        )
        jobs = list(result.scalars().all())
        for job in jobs:
            job.cancelled = True
            job.status = ExportStatus.CANCELLED.value
        await session.flush()
        return len(jobs)

    async def run_job(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        *,
        acting_user: uuid.UUID | None = None,
        sleeper=asyncio.sleep,
    ) -> ExportJob:
        """Execute a job: initial attempt plus up to 3 backed-off retries."""
        job = await session.get(ExportJob, job_id)
        if job is None:
            raise NotFound(f"Export job {job_id} not found")
        route = await route_service.get_route(session, job.route_id)
        adapter = get_delivery_platform_adapter()
        payload = await self.build_payload(session, route)

        job.status = ExportStatus.IN_PROGRESS.value
        await session.flush()

        delays = [0.0, *backoff_schedule()]
        attempt_log: list[dict[str, Any]] = list(job.attempt_log or [])

        for attempt_index, delay in enumerate(delays):
            await session.refresh(job)
            if job.cancelled:
                job.status = ExportStatus.CANCELLED.value
                await session.flush()
                return job
            if delay:
                await sleeper(delay)
                await session.refresh(job)
                if job.cancelled:
                    job.status = ExportStatus.CANCELLED.value
                    await session.flush()
                    return job

            job.attempts = attempt_index + 1
            ack = await adapter.export_route(payload)
            attempt_log.append(
                {
                    "attempt": attempt_index + 1,
                    "delay_seconds": delay,
                    "at": datetime.now(UTC).isoformat(),
                    "acknowledged": ack.acknowledged,
                    "error": ack.error,
                }
            )
            job.attempt_log = attempt_log

            if ack.acknowledged:
                job.status = ExportStatus.SUCCEEDED.value
                job.last_error = None
                await session.flush()
                await integration_health.record_success(
                    session, IntegrationSystem.DELIVERY_PLATFORM
                )
                # Requirement 13.2 — acknowledgement moves the route to dispatched.
                if RouteStatus(route.status) is RouteStatus.APPROVED:
                    await route_service.update_route_status(
                        session, route.route_id, RouteStatus.DISPATCHED, acting_user
                    )
                await alert_service.clear_alerts_for_entity(
                    session,
                    entity_id=route.route_id,
                    alert_type=AlertType.EXPORT_FAILURE,
                    acting_user=acting_user,
                )
                await session.refresh(route)
                await route_service.broadcast_route_updated(route, "exported")
                logger.info(
                    "route_exported",
                    route_id=str(route.route_id),
                    attempts=job.attempts,
                    reference=ack.reference,
                )
                return job

            job.last_error = ack.error
            await session.flush()

        # Requirement 13.4 — all attempts exhausted.
        job.status = ExportStatus.FAILED.value
        await session.flush()
        await integration_health.record_failure(
            session,
            IntegrationSystem.DELIVERY_PLATFORM,
            job.last_error or "Export failed",
        )
        await alert_service.raise_alert(
            session,
            alert_type=AlertType.EXPORT_FAILURE,
            severity=AlertSeverity.CRITICAL,
            entity_type=AlertEntityType.ROUTE,
            entity_id=route.route_id,
            message=(
                f"Export to the Delivery Platform failed after {job.attempts} attempts: "
                f"{job.last_error}. The route stays approved — re-trigger the export when the "
                "platform is reachable."
            ),
            context={"route_id": str(route.route_id), "attempts": job.attempts},
            acting_user=acting_user,
        )
        logger.warning(
            "route_export_failed", route_id=str(route.route_id), attempts=job.attempts
        )
        return job

    async def export_route_now(
        self,
        session: AsyncSession,
        route_id: uuid.UUID,
        acting_user: uuid.UUID | None,
        *,
        sleeper=asyncio.sleep,
    ) -> ExportJob:
        job = await self.enqueue_export(session, route_id, acting_user)
        return await self.run_job(session, job.job_id, acting_user=acting_user, sleeper=sleeper)

    async def list_jobs(self, session: AsyncSession, route_id: uuid.UUID) -> list[ExportJob]:
        result = await session.execute(
            select(ExportJob)
            .where(ExportJob.route_id == route_id)
            .order_by(ExportJob.created_at.desc())
        )
        return list(result.scalars().all())


export_service = ExportService()
