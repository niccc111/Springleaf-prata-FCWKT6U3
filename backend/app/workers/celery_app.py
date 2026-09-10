"""Celery application for queue-based optimisation workers and scheduled jobs."""

from __future__ import annotations

import asyncio
from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

celery_app = Celery(
    "roe",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_soft_time_limit=int(settings.optimisation_wall_clock_limit_seconds) + 30,
    task_time_limit=int(settings.optimisation_wall_clock_limit_seconds) + 60,
    beat_schedule={
        "retention-purge": {
            "task": "roe.retention_purge",
            "schedule": crontab(hour=3, minute=0),
        },
        "geocoding-retry": {
            "task": "roe.geocoding_retry",
            "schedule": float(settings.geocode_retry_interval_seconds),
        },
        "refresh-etas": {
            "task": "roe.refresh_etas",
            "schedule": 300.0,
        },
    },
)


def _run(coro) -> Any:
    return asyncio.run(coro)


@celery_app.task(name="roe.retention_purge")
def retention_purge() -> dict[str, int]:
    from app.db.session import session_scope
    from app.workers.retention import run_all

    async def job() -> dict[str, int]:
        async with session_scope() as session:
            return await run_all(session)

    return _run(job())


@celery_app.task(name="roe.geocoding_retry")
def geocoding_retry() -> dict[str, int]:
    from app.db.session import session_scope
    from app.services.geocoding_service import geocoding_service

    async def job() -> dict[str, int]:
        async with session_scope() as session:
            return await geocoding_service.process_retry_queue(session)

    return _run(job())


@celery_app.task(name="roe.refresh_etas")
def refresh_etas() -> int:
    """Requirement 7.2 — re-pull traffic and propagate downstream ETAs."""
    from app.db.session import session_scope
    from app.services.route_service import route_service

    async def job() -> int:
        async with session_scope() as session:
            return await route_service.recalculate_all_active_routes(session)

    return _run(job())


@celery_app.task(name="roe.export_route")
def export_route(route_id: str, acting_user: str) -> str:
    import uuid as _uuid

    from app.db.session import session_scope
    from app.services.export_service import export_service

    async def job() -> str:
        async with session_scope() as session:
            result = await export_service.export_route_now(
                session, _uuid.UUID(route_id), _uuid.UUID(acting_user)
            )
            return result.status

    return _run(job())
