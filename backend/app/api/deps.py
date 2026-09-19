"""Request-scoped dependencies: authentication, RBAC, and sessions.

Task 15.1 — Requirements 17.1–17.6 (Properties 35, 36).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import select
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Unauthenticated
from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_session
from app.models.entities import User
from app.models.enums import UserRole
from app.services.audit_service import audit_service

bearer_scheme = HTTPBearer(auto_error=False, description="ROE JWT access token")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_db(session: SessionDep) -> AsyncIterator[AsyncSession]:  # pragma: no cover
    yield session


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """Validate the JWT and load the live user record.

    The role is read from the database rather than the token so a role change
    takes effect on the user's very next request (Requirement 17.6).
    """
    if settings.auth_disabled:
        email = (settings.bootstrap_dispatcher_email or settings.bootstrap_admin_email).lower()
        user = await session.scalar(select(User).where(User.email == email, User.active.is_(True)))
        if user is None:
            user = await session.scalar(
                select(User).where(User.active.is_(True)).order_by(User.email)
            )
        if user is None:
            raise Unauthenticated("The local dispatcher account is unavailable")
        request.state.user = user
        return user

    if credentials is None or not credentials.credentials:
        raise Unauthenticated("Missing or invalid credentials")

    claims = decode_token(credentials.credentials, expected_type="access")
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthenticated("Token subject is not a valid user id") from exc

    user = await session.get(User, user_id)
    if user is None or not user.active:
        raise Unauthenticated("User account is unknown or inactive")

    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(
    roles: list[UserRole],
) -> Callable[..., Coroutine[Any, Any, User]]:
    """Guard an endpoint behind one or more roles.

    Denials return 403 and are recorded in the audit log with the user
    identity, attempted action, and UTC timestamp (Requirement 17.5).
    """
    allowed = {role.value for role in roles}

    async def dependency(
        request: Request, session: SessionDep, user: CurrentUser
    ) -> User:
        if user.role not in allowed:
            attempted = f"{request.method} {request.url.path}"
            await audit_service.record_access_denial(
                session,
                acting_user=user.user_id,
                attempted_action=attempted,
                role=user.role,
                required_roles=sorted(allowed),
            )
            await session.commit()
            raise Forbidden(f"Action not permitted for role: {user.role}")
        return user

    return dependency


#: Every dispatcher action is also permitted to administrators (design role matrix).
DispatcherOrAdmin = Annotated[
    User, Depends(require_role([UserRole.DISPATCHER, UserRole.ADMINISTRATOR]))
]
AdminOnly = Annotated[User, Depends(require_role([UserRole.ADMINISTRATOR]))]
