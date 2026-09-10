"""Alert lifecycle: raise, broadcast, acknowledge, list.

Task 4.1 — Requirements 14.1–14.7 (Properties 17, 19, 29).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.core.serialisation import to_jsonable
from app.models.entities import Alert
from app.models.enums import AlertEntityType, AlertSeverity, AlertType, EntityType
from app.schemas.entities import AlertRead
from app.services.audit_service import audit_service

logger = get_logger(__name__)


class AlertService:
    async def raise_alert(
        self,
        session: AsyncSession,
        *,
        alert_type: AlertType,
        severity: AlertSeverity,
        entity_type: AlertEntityType,
        entity_id: uuid.UUID,
        message: str,
        context: dict | None = None,
        acting_user: uuid.UUID | None = None,
        deduplicate: bool = True,
    ) -> Alert:
        """Persist an alert, audit it, and broadcast it to connected dispatchers.

        ``deduplicate`` suppresses a second *unacknowledged* alert of the same
        type for the same entity so repeated ETA recalculations do not flood the
        notification panel.
        """
        if deduplicate:
            existing = await session.scalar(
                select(Alert).where(
                    and_(
                        Alert.alert_type == alert_type.value,
                        Alert.entity_type == entity_type.value,
                        Alert.entity_id == entity_id,
                        Alert.acknowledged.is_(False),
                    )
                )
            )
            if existing is not None:
                if existing.message != message or existing.context != to_jsonable(context or {}):
                    existing.message = message
                    existing.context = to_jsonable(context or {})
                    existing.raised_at = datetime.now(UTC)
                    await session.flush()
                return existing

        alert = Alert(
            alert_id=uuid.uuid4(),
            alert_type=alert_type.value,
            severity=severity.value,
            entity_type=entity_type.value,
            entity_id=entity_id,
            message=message,
            context=to_jsonable(context or {}),
            raised_at=datetime.now(UTC),
            acknowledged=False,
        )
        session.add(alert)
        await session.flush()

        await audit_service.record_change(
            session,
            entity_id=alert.alert_id,
            entity_type=EntityType.ALERT,
            action="alert.raised",
            acting_user=acting_user,
            old_state=None,
            new_state=alert.to_dict(),
        )
        logger.info(
            "alert_raised",
            alert_type=alert_type.value,
            severity=severity.value,
            entity_type=entity_type.value,
            entity_id=str(entity_id),
        )
        return alert

    async def broadcast_raised(self, alert: Alert) -> None:
        await events.publish(
            events.ALERT_RAISED, {"alert": AlertRead.model_validate(alert).model_dump(mode="json")}
        )

    async def acknowledge_alert(
        self, session: AsyncSession, alert_id: uuid.UUID, acting_user: uuid.UUID
    ) -> Alert:
        """First-writer-wins acknowledgement (Requirements 14.5, 14.6; Property 29)."""
        alert = await session.get(Alert, alert_id)
        if alert is None:
            raise NotFound(f"Alert {alert_id} not found")
        old_state = alert.to_dict()

        acknowledged_at = datetime.now(UTC)
        result = await session.execute(
            update(Alert)
            .where(and_(Alert.alert_id == alert_id, Alert.acknowledged.is_(False)))
            .values(
                acknowledged=True, acknowledged_by=acting_user, acknowledged_at=acknowledged_at
            )
            .returning(Alert.alert_id)
        )
        if result.scalar_one_or_none() is None:
            raise Conflict("Alert already acknowledged")

        await session.refresh(alert)
        await audit_service.record_change(
            session,
            entity_id=alert.alert_id,
            entity_type=EntityType.ALERT,
            action="alert.acknowledged",
            acting_user=acting_user,
            old_state=old_state,
            new_state=alert.to_dict(),
        )
        return alert

    async def broadcast_acknowledged(self, alert: Alert) -> None:
        await events.publish(
            events.ALERT_ACKNOWLEDGED,
            {
                "alert_id": str(alert.alert_id),
                "acknowledged_by": str(alert.acknowledged_by),
                "acknowledged_at": to_jsonable(alert.acknowledged_at),
            },
        )

    def build_query(
        self,
        *,
        acknowledged: bool | None = None,
        alert_type: AlertType | None = None,
        severity: AlertSeverity | None = None,
        entity_id: uuid.UUID | None = None,
        since: datetime | None = None,
    ) -> Select[tuple[Alert]]:
        conditions = []
        if acknowledged is not None:
            conditions.append(Alert.acknowledged.is_(acknowledged))
        if alert_type is not None:
            conditions.append(Alert.alert_type == alert_type.value)
        if severity is not None:
            conditions.append(Alert.severity == severity.value)
        if entity_id is not None:
            conditions.append(Alert.entity_id == entity_id)
        if since is not None:
            conditions.append(Alert.raised_at >= since)
        stmt = select(Alert)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        return stmt.order_by(Alert.raised_at.desc())

    async def clear_alerts_for_entity(
        self,
        session: AsyncSession,
        *,
        entity_id: uuid.UUID,
        alert_type: AlertType,
        acting_user: uuid.UUID | None = None,
    ) -> int:
        """Auto-acknowledge stale alerts once the underlying condition clears."""
        result = await session.execute(
            update(Alert)
            .where(
                and_(
                    Alert.entity_id == entity_id,
                    Alert.alert_type == alert_type.value,
                    Alert.acknowledged.is_(False),
                )
            )
            .values(
                acknowledged=True,
                acknowledged_by=acting_user,
                acknowledged_at=datetime.now(UTC),
            )
            .returning(Alert.alert_id)
        )
        return len(result.scalars().all())


alert_service = AlertService()
