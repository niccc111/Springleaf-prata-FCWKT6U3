"""Retention purge jobs.

Task 21.1 — Requirements 14.7 (alerts >= 90 days), 16.2 (audit >= 365 days),
16.3 (completed routes >= 365 days).

``audit_log`` UPDATE/DELETE are blocked for the application role, so the purge
runs with an explicitly elevated maintenance connection. Where that is not
granted the job reports the rows it *would* purge and leaves them in place —
retention is a floor, and keeping entries longer is always safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.entities import Alert, AuditLog, Route
from app.models.enums import RouteStatus

logger = get_logger(__name__)


async def purge_alerts(session: AsyncSession, *, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.alert_retention_days)
    result = await session.execute(
        delete(Alert).where(Alert.raised_at < cutoff).returning(Alert.alert_id)
    )
    purged = len(result.scalars().all())
    logger.info("alerts_purged", count=purged, cutoff=cutoff.isoformat())
    return purged


async def purge_routes(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Completed routes (and their stops, via cascade) past the retention window."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.route_retention_days)
    result = await session.execute(
        delete(Route)
        .where(Route.status == RouteStatus.COMPLETED.value)
        .where(Route.completed_at.isnot(None))
        .where(Route.completed_at < cutoff)
        .returning(Route.route_id)
    )
    purged = len(result.scalars().all())
    logger.info("routes_purged", count=purged, cutoff=cutoff.isoformat())
    return purged


async def purge_audit_log(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Purge audit entries past retention using a maintenance-privileged path.

    The ``audit_no_delete`` rule makes ordinary DELETEs no-ops, which is the
    point: only this job — running as the table owner with the rule disabled
    for the duration of the statement — can remove expired rows.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.audit_retention_days)
    candidates = (
        await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.created_at < cutoff)
        )
    ) or 0
    if not candidates:
        return 0
    try:
        await session.execute(text("ALTER TABLE audit_log DISABLE RULE audit_no_delete"))
        result = await session.execute(
            delete(AuditLog).where(AuditLog.created_at < cutoff).returning(AuditLog.log_id)
        )
        purged = len(result.scalars().all())
        await session.execute(text("ALTER TABLE audit_log ENABLE RULE audit_no_delete"))
    except Exception as exc:
        logger.warning(
            "audit_purge_skipped",
            reason=str(exc),
            candidates=candidates,
            note="retention is a minimum; entries are kept",
        )
        return 0
    logger.info("audit_purged", count=purged, cutoff=cutoff.isoformat())
    return purged


async def run_all(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    return {
        "alerts": await purge_alerts(session, now=now),
        "routes": await purge_routes(session, now=now),
        "audit": await purge_audit_log(session, now=now),
    }
