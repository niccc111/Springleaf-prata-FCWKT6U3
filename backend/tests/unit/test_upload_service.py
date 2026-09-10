"""Unit tests for spreadsheet parsing and row validation."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.core.errors import UploadRejected
from app.services.upload_service import ParsedRow, upload_service


def parse_csv(body: str):
    return upload_service.parse("orders.csv", body.encode("utf-8"))


def test_rejects_unsupported_file_types():
    with pytest.raises(UploadRejected) as exc_info:
        upload_service.parse("orders.txt", b"anything")
    assert "Unsupported file type" in exc_info.value.message


def test_rejects_oversized_files():
    oversized = b"x" * (50 * 1024 * 1024 + 1)
    with pytest.raises(UploadRejected) as exc_info:
        upload_service.parse("orders.csv", oversized)
    assert exc_info.value.details["limit"] == "file_size"


def test_rejects_too_many_rows():
    body = "delivery_address,cargo_weight_kg,priority\n" + "a,1,standard\n" * 10_001
    with pytest.raises(UploadRejected) as exc_info:
        parse_csv(body)
    assert exc_info.value.details["limit"] == "row_count"


def test_skips_blank_and_comment_rows():
    rows = parse_csv(
        "delivery_address,cargo_weight_kg,priority\n"
        "# string (required),# decimal,# standard|priority\n"
        "\n"
        "30 Raffles Place,12.5,standard\n"
    )
    assert len(rows) == 1
    assert rows[0].values["delivery_address"] == "30 Raffles Place"


def test_row_numbers_account_for_the_header():
    rows = parse_csv(
        "delivery_address,cargo_weight_kg,priority\na,1,standard\nb,2,priority\n"
    )
    assert [row.row_number for row in rows] == [2, 3]


def test_xlsx_parsing_handles_typed_cells():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["registration", "capacity_weight_kg", "depot_latitude", "depot_longitude",
                  "operating_hours_start", "operating_hours_end"])
    sheet.append(["SG1", 1200, 1.279, 103.809, "08:00", "18:00"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    rows = upload_service.parse("vehicles.xlsx", buffer.getvalue())
    assert len(rows) == 1
    parsed, errors = upload_service.validate_vehicle_row(rows[0])
    assert errors == []
    assert parsed["registration"] == "SG1"
    assert float(parsed["capacity_weight_kg"]) == 1200.0


@pytest.mark.parametrize(
    ("values", "expected_column"),
    [
        ({"delivery_address": "", "cargo_weight_kg": "1", "priority": "standard"}, "delivery_address"),
        ({"delivery_address": "a", "cargo_weight_kg": "0", "priority": "standard"}, "cargo_weight_kg"),
        ({"delivery_address": "a", "cargo_weight_kg": "abc", "priority": "standard"}, "cargo_weight_kg"),
        ({"delivery_address": "a", "cargo_weight_kg": "1", "priority": "urgent"}, "priority"),
        (
            {"delivery_address": "a", "cargo_weight_kg": "1", "priority": "standard",
             "cargo_volume_m3": "-1"},
            "cargo_volume_m3",
        ),
        (
            {"delivery_address": "a", "cargo_weight_kg": "1", "priority": "standard",
             "time_window_start": "nope"},
            "time_window_start",
        ),
        (
            {"delivery_address": "a", "cargo_weight_kg": "1", "priority": "standard",
             "latitude": "95", "longitude": "0"},
            "latitude",
        ),
    ],
)
def test_order_row_validation_names_the_failing_column(values, expected_column):
    parsed, errors = upload_service.validate_order_row(ParsedRow(row_number=2, values=values))
    assert parsed is None
    assert expected_column in {e.column for e in errors}
    assert all(e.reason for e in errors)


def test_valid_order_row_parses_every_field():
    parsed, errors = upload_service.validate_order_row(
        ParsedRow(
            row_number=2,
            values={
                "delivery_address": "30 Raffles Place",
                "cargo_weight_kg": "12.5",
                "cargo_volume_m3": "0.4",
                "time_window_start": "2026-06-01T09:00:00Z",
                "time_window_end": "2026-06-01T12:00:00Z",
                "priority": "priority",
                "service_duration_min": "15",
                "latitude": "1.3",
                "longitude": "103.8",
            },
        )
    )
    assert errors == []
    assert parsed["priority"] == "priority"
    assert parsed["service_duration_min"] == 15
    assert parsed["delivery_location"].latitude == 1.3
    assert parsed["time_window_start"].tzinfo is not None


def test_vehicle_row_rejects_inverted_operating_hours():
    parsed, errors = upload_service.validate_vehicle_row(
        ParsedRow(
            row_number=2,
            values={
                "registration": "SG1",
                "capacity_weight_kg": "1000",
                "depot_latitude": "1.3",
                "depot_longitude": "103.8",
                "operating_hours_start": "18:00",
                "operating_hours_end": "08:00",
            },
        )
    )
    assert parsed is None
    assert any(e.column == "operating_hours_end" for e in errors)


def test_templates_contain_headings_types_and_an_example():
    """Requirement 4.3 — headings, data types, and exactly one example row."""
    import csv

    for kind in ("orders", "vehicles"):
        text = upload_service.template_csv(kind).decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        assert len(rows) == 3
        assert rows[0] == upload_service.columns_for(kind)
        assert all(cell.startswith("# ") for cell in rows[1])
        assert all(cell != "" for cell in rows[2][:2])

        # The type-hint row is skipped when the filled-in template is uploaded.
        parsed = upload_service.parse(f"{kind}.csv", text.encode("utf-8"))
        assert len(parsed) == 1
