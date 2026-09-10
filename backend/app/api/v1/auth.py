"""Authentication endpoints (local IdP)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import Unauthenticated
from app.core.security import create_token, decode_token
from app.models.entities import User
from app.schemas.entities import LoginRequest, RefreshRequest, TokenPair, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(
            user_id=user.user_id, email=user.email, role=user.role, token_type="access"
        ),
        refresh_token=create_token(
            user_id=user.user_id, email=user.email, role=user.role, token_type="refresh"
        ),
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post("/login", response_model=TokenPair, summary="Exchange credentials for tokens")
async def login(payload: LoginRequest, session: SessionDep) -> TokenPair:
    from app.core.security import verify_password

    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not user.active or not verify_password(payload.password, user.password_hash):
        raise Unauthenticated("Email or password is incorrect")
    return _issue(user)


@router.post("/refresh", response_model=TokenPair, summary="Rotate an access token")
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    claims = decode_token(payload.refresh_token, expected_type="refresh")
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.active:
        raise Unauthenticated("User account is unknown or inactive")
    # The freshly-minted token carries the current role from the database.
    return _issue(user)


@router.get("/me", response_model=UserRead, summary="Current user profile")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
