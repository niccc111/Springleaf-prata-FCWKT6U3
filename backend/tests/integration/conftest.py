"""HTTP client fixtures for API integration tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_token
from app.main import create_app
from app.models.enums import UserRole
from tests.conftest import make_user


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def dispatcher_headers(session) -> dict[str, str]:
    user = await make_user(session, UserRole.DISPATCHER)
    await session.commit()
    token = create_token(user_id=user.user_id, email=user.email, role=user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_headers(session) -> dict[str, str]:
    user = await make_user(session, UserRole.ADMINISTRATOR)
    await session.commit()
    token = create_token(user_id=user.user_id, email=user.email, role=user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def unknown_headers() -> dict[str, str]:
    token = create_token(
        user_id=uuid.uuid4(), email="ghost@roe.app", role=UserRole.ADMINISTRATOR.value
    )
    return {"Authorization": f"Bearer {token}"}
