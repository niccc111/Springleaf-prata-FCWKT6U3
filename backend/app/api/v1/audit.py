"""Audit log query endpoint — Task 15.5."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.models.enums import EntityType
from app.schemas.common import Page
from app.schemas.entities import AuditLogRead
from app.services.audit_service import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogRead], summary="Query the audit log")
async def query_audit(
    session: SessionDep,
    entity_type: Annotated[EntityType | None, Query()] = None,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    acting_user: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    from_date: Annotated[datetime | None, Query()] = None,
    to_date: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditLogRead]:
    stmt = audit_service.build_query(
        entity_type=entity_type.value if entity_type else None,
        entity_id=entity_id,
        acting_user=acting_user,
        action=action,
        from_date=from_date,
        to_date=to_date,
    )
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    result = await session.execute(stmt.limit(limit).offset(offset))
    return Page[AuditLogRead](
        items=[AuditLogRead.model_validate(e) for e in result.scalars().all()],
        total=total,
        limit=limit,
        offset=offset,
    )
