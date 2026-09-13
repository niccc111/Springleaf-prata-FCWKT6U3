"""Property tests for the spreadsheet Upload Service (Tasks 11.3, 11.4)."""

from __future__ import annotations

import csv
import io

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.core.errors import ValidationFailed
from app.models.entities import Order
from app.models.enums import OrderSource
from app.services.upload_service import upload_service
from tests.conftest import make_actor

pytestmark = pytest.mark.property

HEADERS = ["delivery_address", "cargo_weight_kg", "cargo_volume_m3", "priority"]


def _row(valid: bool, index: int) -> tuple[list[str], str | None]:
    """Return a CSV row plus the column expected to fail (None when valid)."""
    if valid:
        return [
            f"{index} Robinson Road, Singapore 0486{index % 10}0",
            "12.5",
            "0.4",
            "standard",
        ], None
    # Deterministically vary which column is invalid.
    kind = index % 3
    if kind == 0:
        return ["", "12.5", "0.4", "standard"], "delivery_address"
    if kind == 1:
        return [f"{index} Robinson Road", "not-a-number", "0.4", "standard"], "cargo_weight_kg"
    return [f"{index} Robinson Road", "12.5", "0.4", "urgent-please"], "priority"


def _csv(flags: list[bool]) -> tuple[bytes, list[tuple[int, str]]]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    expected: list[tuple[int, str]] = []
    for index, valid in enumerate(flags):
        row, bad_column = _row(valid, index)
        writer.writerow(row)
        if bad_column is not None:
            expected.append((index + 2, bad_column))  # +2: header is row 1
    return buffer.getvalue().encode("utf-8"), expected


# Feature: route-optimisation-engine, Property 10: Spreadsheet row validation correctly partitions valid and invalid rows
@given(flags=st.lists(st.booleans(), min_size=1, max_size=25))
def test_property_10_row_partitioning(sessionmaker_, truncate, run_async, flags):
    """imported == number of valid rows, skipped == number of invalid rows, and
    each error names its row number, column, and reason.

    Validates: Requirements 4.4, 4.5
    """
    truncate()
    content, expected_errors = _csv(flags)
    valid_count = sum(flags)

    async def scenario():
        async with sessionmaker_() as session:
            actor = make_actor()
            try:
                result = await upload_service.import_file(
                    session,
                    kind="orders",
                    filename="orders.csv",
                    content=content,
                    acting_user=actor,
                    geocode=False,
                )
                await session.commit()
            except ValidationFailed as exc:
                await session.rollback()
                return None, exc, 0
            persisted = await session.scalar(select(func.count()).select_from(Order))
            return result, None, persisted

    result, rejection, persisted = run_async(scenario())

    if valid_count == 0:
        # Requirement 4.6 — a file with no valid rows is rejected entirely.
        assert rejection is not None
        assert persisted == 0
        return

    assert rejection is None, f"unexpected rejection: {rejection}"
    assert result.imported == valid_count
    assert result.skipped == len(flags) - valid_count
    assert persisted == valid_count

    reported = {(e.row_number, e.column) for e in result.errors}
    for row_number, column in expected_errors:
        assert (
            (row_number, column) in reported
        ), f"expected an error for row {row_number} column {column}; got {sorted(reported)}"
    for error in result.errors:
        assert error.reason, "every row error must carry a reason"
        assert 2 <= error.row_number <= len(flags) + 1


# Feature: route-optimisation-engine, Property 9: Spreadsheet upload transactional atomicity
@given(
    row_count=st.integers(min_value=2, max_value=20),
    fail_after=st.integers(min_value=0, max_value=19),
)
def test_property_9_system_error_persists_nothing(
    sessionmaker_, truncate, run_async, row_count, fail_after
):
    """If a system error occurs mid-import, zero records from that upload are
    persisted — no partial imports.

    Validates: Requirements 4.8
    """
    if fail_after >= row_count:
        return
    truncate()
    content, _ = _csv([True] * row_count)

    async def scenario() -> tuple[bool, int]:
        async with sessionmaker_() as session:
            actor = make_actor()
            await session.commit()
            raised = False
            try:
                await upload_service.import_file(
                    session,
                    kind="orders",
                    filename="orders.csv",
                    content=content,
                    acting_user=actor,
                    geocode=False,
                    fail_after_rows=fail_after,
                )
                await session.commit()
            except ValidationFailed:
                raised = True
                await session.commit()
        async with sessionmaker_() as verify:
            count = await verify.scalar(select(func.count()).select_from(Order))
        return raised, count

    raised, persisted = run_async(scenario())
    assert raised, "an injected system error must surface as an upload failure"
    assert persisted == 0, f"expected zero persisted rows, found {persisted}"


@given(flags=st.lists(st.booleans(), min_size=1, max_size=12))
def test_imported_rows_are_sourced_spreadsheet(sessionmaker_, truncate, run_async, flags):
    """Requirement 4.5 — imported rows carry source = spreadsheet."""
    if not any(flags):
        return
    truncate()
    content, _ = _csv(flags)

    async def scenario() -> list[str]:
        async with sessionmaker_() as session:
            actor = make_actor()
            await upload_service.import_file(
                session,
                kind="orders",
                filename="orders.csv",
                content=content,
                acting_user=actor,
                geocode=False,
            )
            await session.commit()
            rows = (await session.execute(select(Order.source))).scalars().all()
            return list(rows)

    sources = run_async(scenario())
    assert sources
    assert all(source == OrderSource.SPREADSHEET.value for source in sources)
