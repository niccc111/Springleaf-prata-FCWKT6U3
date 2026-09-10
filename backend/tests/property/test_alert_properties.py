"""Property tests for the Alert Service (Task 4.2)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select

from app.core.errors import Conflict
from app.models.entities import Alert
from app.models.enums import AlertEntityType, AlertSeverity, AlertType, UserRole
from app.services.alert_service import alert_service
from tests.conftest import make_user

pytestmark = pytest.mark.property


# Feature: route-optimisation-engine, Property 29: Alert acknowledgement is first-writer-wins
@given(
    alert_type=st.sampled_from(list(AlertType)),
    severity=st.sampled_from(list(AlertSeverity)),
    entity_type=st.sampled_from(list(AlertEntityType)),
    message=st.text(min_size=1, max_size=120),
    contenders=st.integers(min_value=2, max_value=5),
)
def test_property_29_first_writer_wins(
    sessionmaker_, run_async, alert_type, severity, entity_type, message, contenders
):
    """Only the first acknowledgement is recorded; every later attempt is
    rejected with a conflict, and acknowledged_by/at reflect the first writer.

    Validates: Requirements 14.5, 14.6
    """

    async def scenario():
        async with sessionmaker_() as session:
            users = [await make_user(session, UserRole.DISPATCHER) for _ in range(contenders)]
            alert = await alert_service.raise_alert(
                session,
                alert_type=alert_type,
                severity=severity,
                entity_type=entity_type,
                entity_id=uuid.uuid4(),
                message=message,
                acting_user=users[0].user_id,
            )
            await session.commit()
            alert_id = alert.alert_id

        async def attempt(user_id: uuid.UUID) -> uuid.UUID | None:
            async with sessionmaker_() as session:
                try:
                    await alert_service.acknowledge_alert(session, alert_id, user_id)
                    await session.commit()
                    return user_id
                except Conflict:
                    await session.rollback()
                    return None

        outcomes = await asyncio.gather(*(attempt(u.user_id) for u in users))
        async with sessionmaker_() as session:
            stored = await session.get(Alert, alert_id)
            return outcomes, stored

    outcomes, stored = run_async(scenario())

    winners = [o for o in outcomes if o is not None]
    assert len(winners) == 1, f"exactly one acknowledgement must win, got {len(winners)}"
    assert stored.acknowledged is True
    assert stored.acknowledged_by == winners[0]
    assert stored.acknowledged_at is not None


@given(
    alert_type=st.sampled_from(list(AlertType)),
    entity_id=st.uuids(),
    repeats=st.integers(min_value=2, max_value=5),
)
def test_unacknowledged_alerts_deduplicate(
    sessionmaker_, run_async, alert_type, entity_id, repeats
):
    """Repeated identical alerts for the same entity collapse into one entry so
    the notification panel stays legible (Requirement 14.4)."""

    async def scenario() -> int:
        async with sessionmaker_() as session:
            user = await make_user(session, UserRole.DISPATCHER)
            for _ in range(repeats):
                await alert_service.raise_alert(
                    session,
                    alert_type=alert_type,
                    severity=AlertSeverity.WARNING,
                    entity_type=AlertEntityType.ORDER,
                    entity_id=entity_id,
                    message="same condition",
                    acting_user=user.user_id,
                )
            await session.commit()
            rows = (
                await session.execute(
                    select(Alert).where(
                        Alert.entity_id == entity_id,
                        Alert.alert_type == alert_type.value,
                        Alert.acknowledged.is_(False),
                    )
                )
            ).scalars().all()
            return len(list(rows))

    assert run_async(scenario()) == 1
