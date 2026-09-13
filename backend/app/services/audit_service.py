"""Immutable, transactional audit logging.

Task 3.1 — Requirements 16.1, 16.4, 16.5 (Properties 32, 33, 34).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.serialisation import to_jsonable
from app.models.entities import AuditLog, ErrorLog
from app.models.enums import EntityType

logger = get_logger(__name__)

#: Well-known user id used when a change originates from an integration adapter
#: or a scheduled job rather than a human dispatcher.
SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class AuditWriteError(RuntimeError):
    """Raised when the audit INSERT fails, forcing the caller to roll back."""


class AuditService:
    """Append-only audit trail writer.

    ``record_change`` participates in the caller's transaction: if the audit
    INSERT fails the exception propagates so the entity change rolls back with
    it (Requirement 16.5).
    """

    async def record_change(
        self,
        session: AsyncSession,
        *,
        entity_id: uuid.UUID,
        entity_type: EntityType | str,
        new_state: Any,
        action: str,
        acting_user: uuid.UUID | None,
        old_state: Any = None,
    ) -> AuditLog:
        entry = AuditLog(
            log_id=uuid.uuid4(),
            entity_id=entity_id,
            entity_type=str(entity_type),
            old_state=to_jsonable(old_state) if old_state is not None else None,
            new_state=to_jsonable(new_state) if new_state is not None else {},
            action=action,
            acting_user=acting_user or SYSTEM_USER_ID,
            created_at=datetime.now(UTC),
        )
        try:
            session.add(entry)
            await session.flush()
        except Exception as exc:
            logger.error(
                "audit_write_failed",
                entity_type=str(entity_type),
                entity_id=str(entity_id),
                action=action,
                error=str(exc),
            )
            await self.record_error(
                source="audit_service",
                message=f"Audit write failed for {entity_type}:{entity_id} ({action})",
                details={"error": str(exc), "action": action},
            )
            raise AuditWriteError(str(exc)) from exc
        return entry

    async def record_error(
        self, *, source: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        """Write to ``error_log`` on a fresh session, outside any failing transaction."""
        from app.db.session import get_sessionmaker

        try:
            async with get_sessionmaker()() as session:
                session.add(
                    ErrorLog(source=source, message=message, details=to_jsonable(details or {}))
                )
                await session.commit()
        except Exception as exc:  # pragma: no cover - last-resort logging
            logger.error("error_log_write_failed", source=source, error=str(exc))

    def build_query(
        self,
        *,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        acting_user: uuid.UUID | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        action: str | None = None,
    ) -> Select[tuple[AuditLog]]:
        conditions = []
        if entity_type:
            conditions.append(AuditLog.entity_type == entity_type)
        if entity_id:
            conditions.append(AuditLog.entity_id == entity_id)
        if acting_user:
            conditions.append(AuditLog.acting_user == acting_user)
        if from_date:
            conditions.append(AuditLog.created_at >= from_date)
        if to_date:
            conditions.append(AuditLog.created_at <= to_date)
        if action:
            conditions.append(AuditLog.action == action)
        stmt = select(AuditLog)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        return stmt.order_by(AuditLog.created_at.desc())


audit_service = AuditService()
