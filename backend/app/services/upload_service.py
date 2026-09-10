"""Bulk spreadsheet import for orders and vehicles.

Tasks 11.1, 11.2 — Requirements 4.1–4.8 (Properties 9, 10).

The whole file is processed in a single transaction: any system error rolls
back every row, so an upload never partially imports.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from openpyxl import Workbook, load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UploadRejected, ValidationFailed
from app.core.logging import get_logger
from app.models.entities import Order, Vehicle
from app.models.enums import EntityType, OrderSource, OrderStatus, Priority, VehicleSource
from app.schemas.entities import RowError, UploadResult
from app.schemas.geo import CoordinateValidationError, validate_manual_coordinates
from app.services.audit_service import audit_service
from app.services.geocoding_service import geocoding_service

logger = get_logger(__name__)

UploadKind = Literal["orders", "vehicles"]

ORDER_COLUMNS = [
    "delivery_address",
    "cargo_weight_kg",
    "cargo_volume_m3",
    "time_window_start",
    "time_window_end",
    "priority",
    "service_duration_min",
    "latitude",
    "longitude",
]
ORDER_REQUIRED = {"delivery_address", "cargo_weight_kg", "priority"}

VEHICLE_COLUMNS = [
    "registration",
    "capacity_weight_kg",
    "capacity_volume_m3",
    "depot_latitude",
    "depot_longitude",
    "operating_hours_start",
    "operating_hours_end",
    "available",
    "driver_name",
]
VEHICLE_REQUIRED = {
    "registration",
    "capacity_weight_kg",
    "depot_latitude",
    "depot_longitude",
    "operating_hours_start",
    "operating_hours_end",
}

TEMPLATE_TYPES = {
    "orders": {
        "delivery_address": "string (1-500 chars, required)",
        "cargo_weight_kg": "decimal 0.01-99999.99 (required)",
        "cargo_volume_m3": "decimal >= 0 (optional)",
        "time_window_start": "ISO 8601 datetime (optional)",
        "time_window_end": "ISO 8601 datetime (optional)",
        "priority": "standard | priority (required)",
        "service_duration_min": "integer >= 0 (optional, default 10)",
        "latitude": "decimal -90..90 (optional, skips geocoding)",
        "longitude": "decimal -180..180 (optional, skips geocoding)",
    },
    "vehicles": {
        "registration": "string (1-20 chars, required)",
        "capacity_weight_kg": "decimal 0.01-99999.99 (required)",
        "capacity_volume_m3": "decimal > 0 (optional)",
        "depot_latitude": "decimal -90..90 (required)",
        "depot_longitude": "decimal -180..180 (required)",
        "operating_hours_start": "HH:MM (required)",
        "operating_hours_end": "HH:MM (required)",
        "available": "true | false (optional, default true)",
        "driver_name": "string (optional)",
    },
}

TEMPLATE_EXAMPLES = {
    "orders": {
        "delivery_address": "30 Raffles Place, Singapore 048622",
        "cargo_weight_kg": "12.5",
        "cargo_volume_m3": "0.3",
        "time_window_start": "2026-06-01T09:00:00Z",
        "time_window_end": "2026-06-01T12:00:00Z",
        "priority": "standard",
        "service_duration_min": "10",
        "latitude": "",
        "longitude": "",
    },
    "vehicles": {
        "registration": "SGX1234A",
        "capacity_weight_kg": "1200",
        "capacity_volume_m3": "12",
        "depot_latitude": "1.2790",
        "depot_longitude": "103.8090",
        "operating_hours_start": "08:00",
        "operating_hours_end": "18:00",
        "available": "true",
        "driver_name": "A. Tan",
    },
}


@dataclass(slots=True)
class ParsedRow:
    row_number: int
    values: dict[str, str]


class UploadSystemError(RuntimeError):
    """Injected/raised system failure used to exercise rollback (Property 9)."""


class UploadService:
    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #
    def parse(self, filename: str, content: bytes) -> list[ParsedRow]:
        """Parse CSV or XLSX into row dicts, enforcing the size and row caps."""
        if len(content) > settings.upload_max_bytes:
            raise UploadRejected(
                f"File is {len(content) / 1_048_576:.1f} MB which exceeds the "
                f"{settings.upload_max_bytes // 1_048_576} MB limit. No records were imported.",
                details={"limit": "file_size", "max_bytes": settings.upload_max_bytes},
            )

        lowered = (filename or "").lower()
        if lowered.endswith(".csv"):
            rows = self._parse_csv(content)
        elif lowered.endswith((".xlsx", ".xlsm")):
            rows = self._parse_xlsx(content)
        else:
            raise UploadRejected(
                "Unsupported file type. Upload a .csv or .xlsx file.",
                details={"limit": "file_type"},
            )

        if len(rows) > settings.upload_max_rows:
            raise UploadRejected(
                f"File contains {len(rows)} data rows which exceeds the "
                f"{settings.upload_max_rows} row limit. No records were imported.",
                details={"limit": "row_count", "max_rows": settings.upload_max_rows},
            )
        return rows

    def _parse_csv(self, content: bytes) -> list[ParsedRow]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UploadRejected("File is not valid UTF-8 text.") from exc
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise UploadRejected("File has no header row.")
        headers = [(h or "").strip() for h in reader.fieldnames]
        rows: list[ParsedRow] = []
        for index, raw in enumerate(reader, start=2):
            values = {
                header: str(raw.get(header, "") or "").strip()
                for header in headers
                if header
            }
            if any(values.values()) and not _is_comment(values):
                rows.append(ParsedRow(row_number=index, values=values))
        return rows

    def _parse_xlsx(self, content: bytes) -> list[ParsedRow]:
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise UploadRejected(f"Could not read the spreadsheet: {exc}") from exc
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise UploadRejected("File has no header row.") from None
        headers = [str(h).strip() if h is not None else "" for h in header_row]
        rows: list[ParsedRow] = []
        for index, raw in enumerate(rows_iter, start=2):
            values = {
                header: self._cell_to_str(value)
                for header, value in zip(headers, raw, strict=False)
                if header
            }
            if any(values.values()) and not _is_comment(values):
                rows.append(ParsedRow(row_number=index, values=values))
        workbook.close()
        return rows

    @staticmethod
    def _cell_to_str(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, time):
            return value.strftime("%H:%M")
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate_order_row(self, row: ParsedRow) -> tuple[dict[str, Any] | None, list[RowError]]:
        errors: list[RowError] = []
        values = row.values
        parsed: dict[str, Any] = {}

        address = (values.get("delivery_address") or "").strip()
        if not address:
            errors.append(RowError(row_number=row.row_number, column="delivery_address", reason="Required value is missing"))
        elif len(address) > 500:
            errors.append(RowError(row_number=row.row_number, column="delivery_address", reason="Must be 500 characters or fewer"))
        else:
            parsed["delivery_address"] = address

        weight = self._decimal(values.get("cargo_weight_kg"))
        if weight is None:
            errors.append(RowError(row_number=row.row_number, column="cargo_weight_kg", reason="Required decimal value is missing or not a number"))
        elif not (Decimal("0.01") <= weight <= Decimal("99999.99")):
            errors.append(RowError(row_number=row.row_number, column="cargo_weight_kg", reason="Must be between 0.01 and 99999.99"))
        else:
            parsed["cargo_weight_kg"] = weight

        if values.get("cargo_volume_m3"):
            volume = self._decimal(values["cargo_volume_m3"])
            if volume is None or volume < 0:
                errors.append(RowError(row_number=row.row_number, column="cargo_volume_m3", reason="Must be a decimal greater than or equal to 0"))
            else:
                parsed["cargo_volume_m3"] = volume

        for column in ("time_window_start", "time_window_end"):
            if values.get(column):
                moment = self._datetime(values[column])
                if moment is None:
                    errors.append(RowError(row_number=row.row_number, column=column, reason="Must be an ISO 8601 datetime, e.g. 2026-06-01T09:00:00Z"))
                else:
                    parsed[column] = moment
        if (
            parsed.get("time_window_start")
            and parsed.get("time_window_end")
            and parsed["time_window_start"] > parsed["time_window_end"]
        ):
            errors.append(RowError(row_number=row.row_number, column="time_window_end", reason="Must be at or after time_window_start"))

        priority = (values.get("priority") or "standard").strip().lower()
        if priority not in (Priority.STANDARD.value, Priority.PRIORITY.value):
            errors.append(RowError(row_number=row.row_number, column="priority", reason="Must be 'standard' or 'priority'"))
        else:
            parsed["priority"] = priority

        if values.get("service_duration_min"):
            try:
                duration = int(float(values["service_duration_min"]))
                if duration < 0:
                    raise ValueError
                parsed["service_duration_min"] = duration
            except ValueError:
                errors.append(RowError(row_number=row.row_number, column="service_duration_min", reason="Must be a whole number of minutes >= 0"))

        lat_raw, lon_raw = values.get("latitude"), values.get("longitude")
        if lat_raw or lon_raw:
            try:
                parsed["delivery_location"] = validate_manual_coordinates(lat_raw, lon_raw)
            except CoordinateValidationError as exc:
                for entry in exc.errors:
                    errors.append(RowError(row_number=row.row_number, column=entry["field"], reason=entry["message"]))

        return (None if errors else parsed), errors

    def validate_vehicle_row(self, row: ParsedRow) -> tuple[dict[str, Any] | None, list[RowError]]:
        errors: list[RowError] = []
        values = row.values
        parsed: dict[str, Any] = {}

        registration = (values.get("registration") or "").strip()
        if not registration:
            errors.append(RowError(row_number=row.row_number, column="registration", reason="Required value is missing"))
        elif len(registration) > 20:
            errors.append(RowError(row_number=row.row_number, column="registration", reason="Must be 20 characters or fewer"))
        else:
            parsed["registration"] = registration

        capacity = self._decimal(values.get("capacity_weight_kg"))
        if capacity is None:
            errors.append(RowError(row_number=row.row_number, column="capacity_weight_kg", reason="Required decimal value is missing or not a number"))
        elif not (Decimal("0.01") <= capacity <= Decimal("99999.99")):
            errors.append(RowError(row_number=row.row_number, column="capacity_weight_kg", reason="Must be between 0.01 and 99999.99"))
        else:
            parsed["capacity_weight_kg"] = capacity

        if values.get("capacity_volume_m3"):
            volume = self._decimal(values["capacity_volume_m3"])
            if volume is None or volume <= 0:
                errors.append(RowError(row_number=row.row_number, column="capacity_volume_m3", reason="Must be a decimal greater than 0"))
            else:
                parsed["capacity_volume_m3"] = volume

        try:
            parsed["depot_location"] = validate_manual_coordinates(
                values.get("depot_latitude"), values.get("depot_longitude")
            )
        except CoordinateValidationError as exc:
            for entry in exc.errors:
                column = "depot_latitude" if entry["field"] == "latitude" else "depot_longitude"
                errors.append(RowError(row_number=row.row_number, column=column, reason=entry["message"]))

        for column in ("operating_hours_start", "operating_hours_end"):
            parsed_time = self._time(values.get(column))
            if parsed_time is None:
                errors.append(RowError(row_number=row.row_number, column=column, reason="Required time in HH:MM format is missing or invalid"))
            else:
                parsed[column] = parsed_time
        if (
            parsed.get("operating_hours_start")
            and parsed.get("operating_hours_end")
            and parsed["operating_hours_start"] >= parsed["operating_hours_end"]
        ):
            errors.append(RowError(row_number=row.row_number, column="operating_hours_end", reason="Must be later than operating_hours_start"))

        available_raw = (values.get("available") or "true").strip().lower()
        if available_raw not in ("true", "false", "1", "0", "yes", "no", ""):
            errors.append(RowError(row_number=row.row_number, column="available", reason="Must be true or false"))
        else:
            parsed["available"] = available_raw in ("true", "1", "yes", "")

        if values.get("driver_name"):
            parsed["driver_name"] = values["driver_name"][:200]

        return (None if errors else parsed), errors

    @staticmethod
    def _decimal(raw: str | None) -> Decimal | None:
        if raw is None or not str(raw).strip():
            return None
        try:
            return Decimal(str(raw).strip())
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _datetime(raw: str) -> datetime | None:
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _time(raw: str | None) -> time | None:
        if not raw or not str(raw).strip():
            return None
        text = str(raw).strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------ #
    # Import
    # ------------------------------------------------------------------ #
    async def import_file(
        self,
        session: AsyncSession,
        *,
        kind: UploadKind,
        filename: str,
        content: bytes,
        acting_user: uuid.UUID,
        geocode: bool = True,
        fail_after_rows: int | None = None,
    ) -> UploadResult:
        """Validate and import a spreadsheet inside one transaction.

        ``fail_after_rows`` injects a system error mid-import so the rollback
        guarantee (Property 9) is directly testable.
        """
        rows = self.parse(filename, content)
        if not rows:
            raise ValidationFailed(
                f"{filename} contains no data rows. No records were imported.",
                details={"imported": 0, "skipped": 0, "errors": []},
            )

        validator = self.validate_order_row if kind == "orders" else self.validate_vehicle_row
        valid: list[dict[str, Any]] = []
        errors: list[RowError] = []
        for row in rows:
            parsed, row_errors = validator(row)
            if parsed is None:
                errors.extend(row_errors)
            else:
                valid.append(parsed)

        # Requirement 4.6 — no valid rows means the entire upload is rejected.
        if not valid:
            raise ValidationFailed(
                f"No valid rows found in {filename}. No records were imported.",
                details={
                    "imported": 0,
                    "skipped": len(rows),
                    "errors": [e.model_dump() for e in errors],
                },
            )

        savepoint = await session.begin_nested()
        try:
            created_ids: list[uuid.UUID] = []
            for index, payload in enumerate(valid):
                if fail_after_rows is not None and index >= fail_after_rows:
                    raise UploadSystemError("Injected system failure during import")
                if kind == "orders":
                    created_ids.append(
                        await self._insert_order(session, payload, acting_user, geocode)
                    )
                else:
                    created_ids.append(await self._insert_vehicle(session, payload, acting_user))
            await savepoint.commit()
        except Exception as exc:
            # Requirement 4.8 / Property 9 — nothing from this file is persisted.
            await savepoint.rollback()
            logger.error("upload_failed", kind=kind, filename=filename, error=str(exc))
            raise ValidationFailed(
                f"The upload of {filename} failed and no records were imported. "
                "Please retry, or contact support if the problem persists.",
                details={"imported": 0, "skipped": len(rows), "reason": str(exc)},
            ) from exc

        logger.info(
            "upload_complete",
            kind=kind,
            filename=filename,
            imported=len(created_ids),
            skipped=len(rows) - len(valid),
        )
        return UploadResult(
            imported=len(created_ids),
            skipped=len(rows) - len(valid),
            errors=errors,
            message=(
                f"Imported {len(created_ids)} {kind[:-1]} record(s); "
                f"skipped {len(rows) - len(valid)} invalid row(s)."
            ),
        )

    async def _insert_order(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        acting_user: uuid.UUID,
        geocode: bool,
    ) -> uuid.UUID:
        order = Order(
            order_id=uuid.uuid4(),
            source=OrderSource.SPREADSHEET.value,
            delivery_address=payload["delivery_address"],
            delivery_location=payload.get("delivery_location"),
            cargo_weight_kg=payload["cargo_weight_kg"],
            cargo_volume_m3=payload.get("cargo_volume_m3"),
            time_window_start=payload.get("time_window_start"),
            time_window_end=payload.get("time_window_end"),
            priority=payload.get("priority", Priority.STANDARD.value),
            status=OrderStatus.UNASSIGNED.value,
            service_duration_min=payload.get("service_duration_min", 10),
        )
        session.add(order)
        await session.flush()
        if geocode and order.delivery_location is None:
            await geocoding_service.geocode_order(session, order, acting_user=acting_user)
        await audit_service.record_change(
            session,
            entity_id=order.order_id,
            entity_type=EntityType.ORDER,
            action="order.imported",
            acting_user=acting_user,
            old_state=None,
            new_state=order.to_dict(),
        )
        return order.order_id

    async def _insert_vehicle(
        self, session: AsyncSession, payload: dict[str, Any], acting_user: uuid.UUID
    ) -> uuid.UUID:
        vehicle = Vehicle(
            vehicle_id=uuid.uuid4(),
            source=VehicleSource.SPREADSHEET.value,
            registration=payload["registration"],
            capacity_weight_kg=payload["capacity_weight_kg"],
            capacity_volume_m3=payload.get("capacity_volume_m3"),
            depot_location=payload["depot_location"],
            operating_hours_start=payload["operating_hours_start"],
            operating_hours_end=payload["operating_hours_end"],
            available=payload.get("available", True),
            driver_name=payload.get("driver_name"),
        )
        session.add(vehicle)
        await session.flush()
        await audit_service.record_change(
            session,
            entity_id=vehicle.vehicle_id,
            entity_type=EntityType.VEHICLE,
            action="vehicle.imported",
            acting_user=acting_user,
            old_state=None,
            new_state=vehicle.to_dict(),
        )
        return vehicle.vehicle_id

    # ------------------------------------------------------------------ #
    # Templates (Requirement 4.3)
    # ------------------------------------------------------------------ #
    def columns_for(self, kind: UploadKind) -> list[str]:
        return ORDER_COLUMNS if kind == "orders" else VEHICLE_COLUMNS

    def template_csv(self, kind: UploadKind) -> bytes:
        columns = self.columns_for(kind)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        writer.writerow([f"# {TEMPLATE_TYPES[kind][c]}" for c in columns])
        writer.writerow([TEMPLATE_EXAMPLES[kind][c] for c in columns])
        return buffer.getvalue().encode("utf-8")

    def template_xlsx(self, kind: UploadKind) -> bytes:
        columns = self.columns_for(kind)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = kind
        sheet.append(columns)
        sheet.append([f"# {TEMPLATE_TYPES[kind][c]}" for c in columns])
        sheet.append([TEMPLATE_EXAMPLES[kind][c] for c in columns])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()


def _is_comment(values: dict[str, str]) -> bool:
    """Template type-hint rows start with ``#`` and are skipped on re-upload."""
    for value in values.values():
        if value:
            return value.lstrip().startswith("#")
    return False


upload_service = UploadService()
