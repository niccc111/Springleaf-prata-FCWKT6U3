"""Alert endpoints — Task 15.4, Requirements 14.4, 14.5, 14.6."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DispatcherOrAdmin, SessionDep
from app.models.enums import AlertSeverity, AlertType
from app.schemas.entities import AlertRead
from app.services.alert_service import alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertRead], summary="List alerts")
async def list_alerts(
    session: SessionDep,
    user: DispatcherOrAdmin,
    acknowledged: Annotated[bool | None, Query()] = None,
    alert_type: Annotated[AlertType | None, Query()] = None,
    severity: Annotated[AlertSeverity | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AlertRead]:
    stmt = alert_service.build_query(
        acknowledged=acknowledged, alert_type=alert_type, severity=severity, since=since
    ).limit(limit)
    result = await session.execute(stmt)
    return [AlertRead.model_validate(a) for a in result.scalars().all()]


@router.patch(
    "/{alert_id}/acknowledge",
    response_model=AlertRead,
    summary="Acknowledge an alert (first writer wins)",
)
async def acknowledge(
    alert_id: uuid.UUID, session: SessionDep, user: DispatcherOrAdmin
) -> AlertRead:
    alert = await alert_service.acknowledge_alert(session, alert_id, user.user_id)
    await session.commit()
    await alert_service.broadcast_acknowledged(alert)
    return AlertRead.model_validate(alert)
