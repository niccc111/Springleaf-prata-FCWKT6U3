"""First-run bootstrapping: seed users so the UI is usable immediately."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.entities import User
from app.models.enums import UserRole

logger = get_logger(__name__)


async def ensure_seed_users(session: AsyncSession) -> None:
    """Create the bootstrap administrator (and dispatcher) if no users exist."""
    count = await session.scalar(select(func.count()).select_from(User))
    if count:
        return

    session.add(
        User(
            user_id=uuid.uuid4(),
            email=settings.bootstrap_admin_email.lower(),
            full_name="ROE Administrator",
            password_hash=hash_password(settings.bootstrap_admin_password),
            role=UserRole.ADMINISTRATOR.value,
            active=True,
        )
    )
    if settings.bootstrap_dispatcher_email:
        session.add(
            User(
                user_id=uuid.uuid4(),
                email=settings.bootstrap_dispatcher_email.lower(),
                full_name="Duty Dispatcher",
                password_hash=hash_password(settings.bootstrap_dispatcher_password),
                role=UserRole.DISPATCHER.value,
                active=True,
            )
        )
    await session.commit()
    logger.info("seed_users_created", admin=settings.bootstrap_admin_email)
