"""Property tests for authentication and RBAC (Tasks 15.7, 15.8)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select

from app.core.security import create_token
from app.main import create_app
from app.models.entities import AuditLog
from app.models.enums import UserRole
from tests.conftest import make_user

pytestmark = pytest.mark.property

#: Endpoints only an Administrator may reach (design § Role Matrix).
ADMIN_ONLY: list[tuple[str, str]] = [
    ("GET", "/api/v1/audit"),
    ("GET", "/api/v1/users"),
    ("POST", "/api/v1/users"),
    ("PATCH", f"/api/v1/users/{uuid.uuid4()}/role"),
    ("POST", "/api/v1/system/integrations/oms/events"),
    ("POST", "/api/v1/system/integrations/fms/events"),
]

#: Endpoints both roles may reach.
SHARED: list[tuple[str, str]] = [
    ("GET", "/api/v1/routes"),
    ("GET", "/api/v1/orders"),
    ("GET", "/api/v1/vehicles"),
    ("GET", "/api/v1/alerts"),
    ("GET", "/api/v1/system/connectivity"),
    ("GET", "/api/v1/optimise/runs"),
    ("GET", "/api/v1/auth/me"),
]

ALL_PROTECTED = ADMIN_ONLY + SHARED + [
    ("POST", "/api/v1/optimise"),
    ("POST", "/api/v1/orders"),
    ("POST", "/api/v1/vehicles"),
    ("POST", "/api/v1/orders/reassign"),
    ("POST", "/api/v1/upload/orders"),
    ("GET", "/api/v1/upload/templates/orders"),
    ("GET", f"/api/v1/routes/{uuid.uuid4()}"),
    ("PATCH", f"/api/v1/routes/{uuid.uuid4()}"),
    ("POST", f"/api/v1/routes/{uuid.uuid4()}/export"),
    ("PATCH", f"/api/v1/alerts/{uuid.uuid4()}/acknowledge"),
]


def _body(method: str) -> dict[str, Any]:
    return {"json": {}} if method in ("POST", "PATCH") else {}


async def _call(method: str, path: str, headers: dict[str, str] | None):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers or {}, **_body(method))


# Feature: route-optimisation-engine, Property 36: All API endpoints deny unauthenticated requests
@given(endpoint=st.sampled_from(ALL_PROTECTED), token=st.sampled_from(["", "garbage", "expired"]))
def test_property_36_unauthenticated_requests_denied(run_async, endpoint, token):
    """Every protected endpoint returns 401 without valid credentials.

    Validates: Requirements 17.4
    """
    method, path = endpoint
    headers: dict[str, str] = {}
    if token == "garbage":
        headers["Authorization"] = "Bearer not-a-real-token"
    elif token == "expired":
        from datetime import timedelta

        headers["Authorization"] = "Bearer " + create_token(
            user_id=uuid.uuid4(),
            email="ghost@roe.app",
            role="administrator",
            expires_delta=timedelta(seconds=-60),
        )

    response = run_async(_call(method, path, headers))
    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code} without valid credentials"
    )
    assert response.json()["error"] == "unauthenticated"


# Feature: route-optimisation-engine, Property 35: RBAC denies out-of-role actions for all users
@given(endpoint=st.sampled_from(ADMIN_ONLY))
def test_property_35_dispatcher_denied_admin_actions(
    sessionmaker_, truncate, run_async, endpoint
):
    """A dispatcher attempting an administrator-only action gets 403, and the
    denial is recorded in the audit log with the user, action, and timestamp.

    Validates: Requirements 17.2, 17.3, 17.5
    """
    truncate()
    method, path = endpoint

    async def scenario():
        async with sessionmaker_() as session:
            user = await make_user(session, UserRole.DISPATCHER)
            await session.commit()
            user_id, email = user.user_id, user.email

        token = create_token(user_id=user_id, email=email, role=UserRole.DISPATCHER.value)
        response = await _call(method, path, {"Authorization": f"Bearer {token}"})

        async with sessionmaker_() as session:
            entries = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.acting_user == user_id, AuditLog.action == "access.denied"
                    )
                )
            ).scalars().all()
            return response, list(entries), user_id

    response, entries, user_id = run_async(scenario())

    assert response.status_code == 403, f"{method} {path} should be forbidden for a dispatcher"
    assert response.json()["error"] == "forbidden"
    assert "dispatcher" in response.json()["message"]

    assert len(entries) == 1, "the denial must be audited exactly once"
    entry = entries[0]
    assert entry.acting_user == user_id
    assert entry.new_state["attempted_action"] == f"{method} {path}"
    assert entry.new_state["role"] == UserRole.DISPATCHER.value
    assert entry.new_state["required_roles"] == ["administrator"]
    assert entry.created_at is not None


@given(endpoint=st.sampled_from(SHARED), role=st.sampled_from(list(UserRole)))
def test_shared_endpoints_allow_both_roles(sessionmaker_, truncate, run_async, endpoint, role):
    """Requirement 17.2/17.3 — both roles reach every shared dispatcher action."""
    truncate()
    method, path = endpoint

    async def scenario():
        async with sessionmaker_() as session:
            user = await make_user(session, role)
            await session.commit()
            token = create_token(user_id=user.user_id, email=user.email, role=role.value)
        return await _call(method, path, {"Authorization": f"Bearer {token}"})

    response = run_async(scenario())
    assert response.status_code not in (401, 403), (
        f"{role.value} should reach {method} {path}, got {response.status_code}"
    )
