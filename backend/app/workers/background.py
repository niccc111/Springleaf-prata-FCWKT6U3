"""In-process background loops.

Runs the OMS/FMS pollers and health watchdogs, the geocoding retry queue, and
the periodic ETA refresh. In a scaled deployment these run as Celery beat tasks
(see ``app.workers.celery_app``); in-process is the local/dev default so the
system is fully functional from ``docker compose up``.

Tasks 8.2, 9.2, 7.2, 21.1 — Requirements 1.5, 2.6, 7.2, 7.4, 15.6.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.adapters.fms import fms_adapter, get_fms_source
from app.adapters.mapping import get_mapping_adapter
from app.adapters.oms import get_oms_source, oms_adapter
from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import IntegrationSystem
from app.services.geocoding_service import geocoding_service
from app.services.integration_health import integration_health
from app.services.optimisation_service import optimisation_service

logger = get_logger(__name__)


class BackgroundTasks:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._tasks:
            return
        self._stopping = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._loop(self.poll_oms, settings.oms_poll_interval_seconds, "oms")),
            asyncio.create_task(self._loop(self.poll_fms, settings.fms_poll_interval_seconds, "fms")),
            asyncio.create_task(self._loop(self.check_mapping, 30, "mapping")),
            asyncio.create_task(
                self._loop(self.retry_geocoding, settings.geocode_retry_interval_seconds, "geocode")
            ),
        ]
        logger.info("background_tasks_started", count=len(self._tasks))

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    async def _loop(self, fn, interval: float, name: str) -> None:
        while not self._stopping.is_set():
            try:
                await fn()
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception as exc:
                logger.warning("background_task_error", task=name, error=str(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)

    # ------------------------------------------------------------------ #
    async def poll_oms(self) -> None:
        from app.db.session import session_scope

        source = get_oms_source()
        async with session_scope() as session:
            try:
                batch = await source.fetch_events()
            except Exception as exc:
                await integration_health.record_failure(
                    session,
                    IntegrationSystem.OMS,
                    f"Order Management System unreachable: {exc}",
                    threshold_seconds=settings.oms_unavailable_threshold_seconds,
                )
                return
            await integration_health.record_success(session, IntegrationSystem.OMS)
            if batch:
                stats = await oms_adapter.process_batch(session, batch)
                logger.info("oms_batch_processed", **stats)
                await optimisation_service.notify_pending_orders(session)

    async def poll_fms(self) -> None:
        from app.db.session import session_scope

        source = get_fms_source()
        async with session_scope() as session:
            try:
                batch = await source.fetch_events()
            except Exception as exc:
                # Requirement 2.6 — keep operating on last-known vehicle state.
                await integration_health.record_failure(
                    session,
                    IntegrationSystem.FMS,
                    f"Fleet Management System unreachable — using last known vehicle "
                    f"records: {exc}",
                    threshold_seconds=settings.fms_unavailable_threshold_seconds,
                )
                return
            await integration_health.record_success(session, IntegrationSystem.FMS)
            if batch:
                stats = await fms_adapter.process_batch(session, batch)
                logger.info("fms_batch_processed", **stats)

    async def check_mapping(self) -> None:
        from app.db.session import session_scope

        adapter = get_mapping_adapter()
        healthy = await adapter.health_check()
        async with session_scope() as session:
            if healthy:
                await integration_health.record_success(session, IntegrationSystem.MAPPING)
            else:
                await integration_health.record_failure(
                    session,
                    IntegrationSystem.MAPPING,
                    "Mapping Service unavailable — travel times fall back to historical "
                    "averages",
                    threshold_seconds=settings.mapping_unavailable_threshold_seconds,
                )

    async def retry_geocoding(self) -> None:
        from app.db.session import session_scope

        async with session_scope() as session:
            stats = await geocoding_service.process_retry_queue(session)
            if stats["processed"]:
                logger.info("geocoding_retry_pass", **stats)


background_tasks = BackgroundTasks()
