"""Property tests for the Audit Service (Tasks 2.3, 3.2, 3.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import delete, func, select, update

from app.models.entities import AuditLog, ErrorLog, Order
from app.models.enums import EntityType
from app.services.audit_service import AuditWriteError, audit_service
from tests.conftest import build_order, build_vehicle, make_actor
from tests.property.strategies import addresses, capacities, geo_points, weights

pytestmark = pytest.mark.property

ACTIONS = ["order.created", "order.updated", "vehicle.updated", "route.locked", "alert.raised"]


# Feature: route-optimisation-engine, Property 33: Audit log entries are immutable
@given(
    action=st.sampled_from(ACTIONS),
    entity_type=st.sampled_from(list(EntityType)),
    payload=st.dictionaries(
        st.text(min_size=1, max_size=12), st.integers(min_value=-1000, max_value=1000), max_size=5
    ),
)
def test_property_33_audit_entries_are_immutable(
    sessionmaker_, truncate, run_async, action, entity_type, payload
):
    """Any attempt to UPDATE or DELETE an audit row is rejected; the row remains
    identical to its originally written form.

    Validates: Requirements 16.4
    """
    truncate()

    async def scenario():
        entity_id = uuid.uuid4()
        async with sessionmaker_() as session:
            actor = make_actor()
            entry = await audit_service.record_change(
                session,
                entity_id=entity_id,
                entity_type=entity_type,
                action=action,
                acting_user=actor,
                old_state=None,
                new_state=payload,
            )
            await session.commit()
            original = (entry.log_id, entry.action, dict(entry.new_state), entry.acting_user)

        async with sessionmaker_() as session:
            await session.execute(
                update(AuditLog)
                .where(AuditLog.log_id == original[0])
                .values(action="tampered", new_state={"tampered": True})
            )
            await session.execute(delete(AuditLog).where(AuditLog.log_id == original[0]))
            await session.commit()

        async with sessionmaker_() as session:
            after = await session.get(AuditLog, original[0])
            return original, after

    original, after = run_async(scenario())
    assert after is not None, "the audit entry must still exist after a DELETE attempt"
    assert after.action == original[1], "the audit entry must not be modifiable"
    assert after.new_state == original[2]
    assert after.acting_user == original[3]


# Feature: route-optimisation-engine, Property 32: Audit log entries are created for every state change
@given(
    address=addresses,
    weight=weights,
    capacity=capacities,
    depot=geo_points(),
    new_weight=weights,
)
def test_property_32_state_changes_are_audited(
    sessionmaker_, truncate, run_async, address, weight, capacity, depot, new_weight
):
    """Every Order and Vehicle state change writes exactly one audit entry with
    the correct entity id/type, old and new state, acting user, and a UTC
    timestamp with millisecond precision.

    Validates: Requirements 16.1
    """
    truncate()

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            order = build_order(delivery_address=address, cargo_weight_kg=weight)
            vehicle = build_vehicle(capacity_weight_kg=capacity, depot_location=depot)
            session.add_all([order, vehicle])
            await session.flush()

            await audit_service.record_change(
                session,
                entity_id=order.order_id,
                entity_type=EntityType.ORDER,
                action="order.created",
                acting_user=actor,
                old_state=None,
                new_state=order.to_dict(),
            )
            before = order.to_dict()
            order.cargo_weight_kg = new_weight
            await session.flush()
            await audit_service.record_change(
                session,
                entity_id=order.order_id,
                entity_type=EntityType.ORDER,
                action="order.updated",
                acting_user=actor,
                old_state=before,
                new_state=order.to_dict(),
            )
            await audit_service.record_change(
                session,
                entity_id=vehicle.vehicle_id,
                entity_type=EntityType.VEHICLE,
                action="vehicle.created",
                acting_user=actor,
                old_state=None,
                new_state=vehicle.to_dict(),
            )
            await session.commit()

            entries = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_id.in_([order.order_id, vehicle.vehicle_id]))
                        .order_by(AuditLog.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return list(entries), order, vehicle, actor

    entries, order, vehicle, actor = run_async(scenario())

    assert len(entries) == 3, f"expected one entry per state change, found {len(entries)}"
    by_action = {e.action: e for e in entries}
    assert set(by_action) == {"order.created", "order.updated", "vehicle.created"}

    created = by_action["order.created"]
    assert created.entity_id == order.order_id
    assert created.entity_type == EntityType.ORDER.value
    assert created.old_state is None
    assert (
        created.new_state["delivery_address"] == address.strip()
        or created.new_state["delivery_address"] == address
    )

    updated = by_action["order.updated"]
    assert updated.old_state["cargo_weight_kg"] == float(weight)
    assert updated.new_state["cargo_weight_kg"] == float(new_weight)

    vehicle_entry = by_action["vehicle.created"]
    assert vehicle_entry.entity_type == EntityType.VEHICLE.value
    assert vehicle_entry.entity_id == vehicle.vehicle_id

    for entry in entries:
        assert entry.acting_user == actor
        assert entry.created_at.tzinfo is not None
        assert entry.created_at <= datetime.now(UTC)
        # TIMESTAMPTZ(3) keeps millisecond precision, never finer.
        assert entry.created_at.microsecond % 1000 == 0


# Feature: route-optimisation-engine, Property 34: Audit write failure prevents entity state change
@given(new_weight=weights)
def test_property_34_audit_failure_rolls_back_entity_change(
    sessionmaker_, truncate, run_async, new_weight, monkeypatch
):
    """If the audit INSERT fails, the entity state change is rolled back and an
    error_log entry is written.

    Validates: Requirements 16.5
    """
    truncate()

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            order = build_order(cargo_weight_kg=Decimal("11.00"))
            session.add(order)
            await session.commit()
            order_id = order.order_id
            original_weight = order.cargo_weight_kg

        raised = False
        async with sessionmaker_() as session:
            order = await session.get(Order, order_id)
            order.cargo_weight_kg = new_weight
            await session.flush()
            try:
                # Simulate the audit INSERT failing inside the caller's transaction.
                await audit_service.record_change(
                    session,
                    entity_id=order_id,
                    entity_type="not-a-valid-entity-type",  # violates the CHECK constraint
                    action="order.updated",
                    acting_user=actor,
                    old_state=None,
                    new_state={"cargo_weight_kg": float(new_weight)},
                )
                await session.commit()
            except AuditWriteError:
                raised = True
                await session.rollback()

        async with sessionmaker_() as verify:
            persisted = await verify.get(Order, order_id)
            errors = await verify.scalar(select(func.count()).select_from(ErrorLog))
            return raised, persisted.cargo_weight_kg, original_weight, errors

    raised, persisted_weight, original_weight, error_count = run_async(scenario())

    assert raised, "a failing audit write must raise so the caller rolls back"
    assert (
        persisted_weight == original_weight
    ), "the entity change must not survive a failed audit write"
    assert error_count >= 1, "the failure must be recorded in error_log"
