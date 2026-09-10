"""User management (Administrator only) — Task 15.6, Requirements 17.1, 17.6."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AdminOnly, SessionDep
from app.core.errors import Conflict, NotFound
from app.core.security import hash_password
from app.models.entities import User
from app.models.enums import EntityType
from app.schemas.entities import UserCreate, UserRead, UserRoleUpdate
from app.services.audit_service import audit_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead], summary="List users")
async def list_users(session: SessionDep, admin: AdminOnly) -> list[UserRead]:
    result = await session.execute(select(User).order_by(User.email))
    return [UserRead.model_validate(u) for u in result.scalars().all()]


@router.post(
    "", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="Create a user"
)
async def create_user(payload: UserCreate, session: SessionDep, admin: AdminOnly) -> UserRead:
    email = payload.email.lower()
    if await session.scalar(select(User).where(User.email == email)):
        raise Conflict(f"A user with email {email} already exists")
    user = User(
        user_id=uuid.uuid4(),
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    await audit_service.record_change(
        session,
        entity_id=user.user_id,
        entity_type=EntityType.USER,
        action="user.created",
        acting_user=admin.user_id,
        old_state=None,
        new_state=user.to_dict(exclude={"password_hash"}),
    )
    await session.commit()
    return UserRead.model_validate(user)


@router.patch("/{user_id}/role", response_model=UserRead, summary="Change a user's role")
async def change_role(
    user_id: uuid.UUID, payload: UserRoleUpdate, session: SessionDep, admin: AdminOnly
) -> UserRead:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound(f"User {user_id} not found")
    old_state = user.to_dict(exclude={"password_hash"})
    user.role = payload.role.value
    user.updated_at = datetime.now(UTC)
    await session.flush()
    await audit_service.record_change(
        session,
        entity_id=user.user_id,
        entity_type=EntityType.USER,
        action="user.role_changed",
        acting_user=admin.user_id,
        old_state=old_state,
        new_state=user.to_dict(exclude={"password_hash"}),
    )
    await session.commit()
    return UserRead.model_validate(user)
