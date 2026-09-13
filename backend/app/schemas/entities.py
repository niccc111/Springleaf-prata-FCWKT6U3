"""Pydantic models mirroring the requirements data dictionary."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.serialisation import sanitise_text
from app.models.enums import (
    AlertEntityType,
    AlertSeverity,
    AlertType,
    EntityType,
    ExportStatus,
    OptimisationRunStatus,
    OptimisationRunType,
    OrderSource,
    OrderStatus,
    Priority,
    RouteStatus,
    VehicleSource,
)
from app.schemas.geo import GeoPoint


class SanitisedModel(BaseModel):
    """Base model that strips NUL code points from every inbound string.

    PostgreSQL ``text`` and ``jsonb`` cannot store U+0000, and an audit write
    that fails on one would (per Requirement 16.5) roll back the very change it
    was recording, so the character is removed at the API boundary.
    """

    @model_validator(mode="before")
    @classmethod
    def _strip_nul(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: sanitise_text(value) if isinstance(value, str) else value
                for key, value in data.items()
            }
        return data


WeightKg = Annotated[Decimal, Field(ge=Decimal("0.01"), le=Decimal("99999.99"))]
OptionalVolume = Annotated[Decimal | None, Field(default=None, ge=Decimal("0"))]
Address = Annotated[str, Field(min_length=1, max_length=500)]
Registration = Annotated[str, Field(min_length=1, max_length=20)]

ORM = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
class OrderBase(SanitisedModel):
    delivery_address: Address
    cargo_weight_kg: WeightKg
    cargo_volume_m3: OptionalVolume = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    priority: Priority = Priority.STANDARD
    service_duration_min: int = Field(default=10, ge=0, le=1440)
    delivery_location: GeoPoint | None = None
    pickup_location: GeoPoint | None = None

    @model_validator(mode="after")
    def _window_order(self) -> OrderBase:
        if (
            self.time_window_start is not None
            and self.time_window_end is not None
            and self.time_window_start > self.time_window_end
        ):
            raise ValueError("time_window_start must be earlier than or equal to time_window_end")
        return self


class OrderCreate(OrderBase):
    """Manual Entry Form payload (Requirement 3.1)."""


class OrderUpdate(SanitisedModel):
    """Partial update for a manually entered order (Requirement 3.6)."""

    delivery_address: Address | None = None
    cargo_weight_kg: WeightKg | None = None
    cargo_volume_m3: OptionalVolume = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    priority: Priority | None = None
    service_duration_min: int | None = Field(default=None, ge=0, le=1440)
    delivery_location: GeoPoint | None = None

    @model_validator(mode="after")
    def _window_order(self) -> OrderUpdate:
        if (
            self.time_window_start is not None
            and self.time_window_end is not None
            and self.time_window_start > self.time_window_end
        ):
            raise ValueError("time_window_start must be earlier than or equal to time_window_end")
        return self


class OrderRead(BaseModel):
    model_config = ORM

    order_id: uuid.UUID
    source: OrderSource
    pickup_location: GeoPoint | None = None
    delivery_location: GeoPoint | None = None
    delivery_address: str
    cargo_weight_kg: Decimal
    cargo_volume_m3: Decimal | None = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    priority: Priority
    status: OrderStatus
    service_duration_min: int
    geocode_review: bool
    geocode_confidence: float | None = None
    created_at: datetime
    updated_at: datetime
    external_ref: str | None = None
    route_id: uuid.UUID | None = None

    @field_serializer("cargo_weight_kg", "cargo_volume_m3")
    def _decimal(self, value: Decimal | None) -> float | None:
        return None if value is None else float(value)


class OrderCoordinateOverride(BaseModel):
    """Dispatcher-supplied coordinates (Requirement 15.5)."""

    latitude: float
    longitude: float


# --------------------------------------------------------------------------- #
# Vehicles
# --------------------------------------------------------------------------- #
class VehicleBase(SanitisedModel):
    registration: Registration
    capacity_weight_kg: WeightKg
    capacity_volume_m3: Annotated[Decimal | None, Field(default=None, gt=Decimal("0"))] = None
    depot_location: GeoPoint
    operating_hours_start: time
    operating_hours_end: time
    available: bool = True
    driver_id: uuid.UUID | None = None
    driver_name: str | None = Field(default=None, max_length=200)

    @field_validator("operating_hours_start", "operating_hours_end", mode="before")
    @classmethod
    def _parse_time(cls, value: Any) -> Any:
        if isinstance(value, str):
            parts = value.strip().split(":")
            if len(parts) == 2:
                return time(int(parts[0]), int(parts[1]))
        return value

    @model_validator(mode="after")
    def _hours_order(self) -> VehicleBase:
        if self.operating_hours_start >= self.operating_hours_end:
            raise ValueError("operating_hours_start must be earlier than operating_hours_end")
        return self


class VehicleCreate(VehicleBase):
    """Manual Entry Form payload (Requirement 3.2)."""


class VehicleUpdate(SanitisedModel):
    registration: Registration | None = None
    capacity_weight_kg: WeightKg | None = None
    capacity_volume_m3: Annotated[Decimal | None, Field(default=None, gt=Decimal("0"))] = None
    depot_location: GeoPoint | None = None
    operating_hours_start: time | None = None
    operating_hours_end: time | None = None
    available: bool | None = None
    driver_id: uuid.UUID | None = None
    driver_name: str | None = Field(default=None, max_length=200)

    @field_validator("operating_hours_start", "operating_hours_end", mode="before")
    @classmethod
    def _parse_time(cls, value: Any) -> Any:
        if isinstance(value, str):
            parts = value.strip().split(":")
            if len(parts) == 2:
                return time(int(parts[0]), int(parts[1]))
        return value


class VehicleRead(BaseModel):
    model_config = ORM

    vehicle_id: uuid.UUID
    source: VehicleSource
    registration: str
    capacity_weight_kg: Decimal
    capacity_volume_m3: Decimal | None = None
    depot_location: GeoPoint
    operating_hours_start: time
    operating_hours_end: time
    available: bool
    driver_id: uuid.UUID | None = None
    driver_name: str | None = None
    external_ref: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("capacity_weight_kg", "capacity_volume_m3")
    def _decimal(self, value: Decimal | None) -> float | None:
        return None if value is None else float(value)


# --------------------------------------------------------------------------- #
# Routes and stops
# --------------------------------------------------------------------------- #
class StopOrderSummary(BaseModel):
    order_id: uuid.UUID
    delivery_address: str
    cargo_weight_kg: float
    cargo_volume_m3: float | None = None
    priority: Priority
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    external_ref: str | None = None


class StopRead(BaseModel):
    model_config = ORM

    stop_id: uuid.UUID
    route_id: uuid.UUID
    order_ids: list[uuid.UUID] = Field(default_factory=list)
    orders: list[StopOrderSummary] = Field(default_factory=list)
    location: GeoPoint
    address: str
    sequence_number: int
    eta: datetime
    departure: datetime | None = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    service_duration_min: int
    distance_from_previous_km: float = 0.0
    travel_time_from_previous_min: int = 0
    has_priority_order: bool = False
    total_weight_kg: float = 0.0
    total_volume_m3: float | None = None
    late: bool = False


class RouteRead(BaseModel):
    model_config = ORM

    route_id: uuid.UUID
    vehicle_id: uuid.UUID
    optimisation_run_id: uuid.UUID
    driver_id: uuid.UUID | None = None
    vehicle_registration: str | None = None
    vehicle_capacity_weight_kg: float | None = None
    vehicle_capacity_volume_m3: float | None = None
    depot_location: GeoPoint | None = None
    total_distance_km: float
    total_duration_min: int
    total_weight_kg: float
    total_volume_m3: float | None = None
    weight_utilisation_pct: float | None = None
    volume_utilisation_pct: float | None = None
    stop_count: int = 0
    locked: bool
    status: RouteStatus
    data_quality_warning: bool = False
    data_quality_message: str | None = None
    needs_reoptimisation: bool = False
    priority_relaxed: bool = False
    travel_times_updated_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    stops: list[StopRead] = Field(default_factory=list)


class RoutePatch(BaseModel):
    """Lock/unlock and status transitions (`PATCH /routes/{id}`)."""

    locked: bool | None = None
    status: RouteStatus | None = None


class ReassignRequest(BaseModel):
    order_id: uuid.UUID
    source_route_id: uuid.UUID | None = None
    dest_route_id: uuid.UUID
    position: int | None = Field(default=None, ge=1)
    confirm_late_delivery: bool = False


class ReassignResponse(BaseModel):
    source_route: RouteRead | None = None
    dest_route: RouteRead
    late_stops: list[uuid.UUID] = Field(default_factory=list)
    message: str


# --------------------------------------------------------------------------- #
# Optimisation
# --------------------------------------------------------------------------- #
class OptimiseRequest(BaseModel):
    reoptimise: bool = False
    planning_date: datetime | None = None


class RouteDiffEntry(BaseModel):
    route_id: uuid.UUID
    vehicle_id: uuid.UUID
    change: str
    added_order_ids: list[uuid.UUID] = Field(default_factory=list)
    removed_order_ids: list[uuid.UUID] = Field(default_factory=list)
    sequence_changed: bool = False
    driver_changed: bool = False
    previous_driver_id: uuid.UUID | None = None
    new_driver_id: uuid.UUID | None = None


class OptimisationRunRead(BaseModel):
    model_config = ORM

    run_id: uuid.UUID
    status: OptimisationRunStatus
    run_type: OptimisationRunType
    started_at: datetime
    ended_at: datetime | None = None
    locked_excluded_count: int | None = None
    orders_considered: int | None = None
    orders_assigned: int | None = None
    orders_unassigned: int | None = None
    routes_created: int | None = None
    progress_pct: int = 0
    progress_message: str | None = None
    error_message: str | None = None
    solver_status: str | None = None
    duration_seconds: float | None = None
    diff: dict | None = None
    initiated_by: uuid.UUID


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
class AlertRead(BaseModel):
    model_config = ORM

    alert_id: uuid.UUID
    alert_type: AlertType
    severity: AlertSeverity
    entity_type: AlertEntityType
    entity_id: uuid.UUID
    message: str
    context: dict | None = None
    raised_at: datetime
    acknowledged: bool
    acknowledged_by: uuid.UUID | None = None
    acknowledged_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditLogRead(BaseModel):
    model_config = ORM

    log_id: uuid.UUID
    entity_id: uuid.UUID
    entity_type: EntityType
    old_state: dict | None = None
    new_state: dict
    action: str
    acting_user: uuid.UUID
    created_at: datetime


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
class RowError(BaseModel):
    row_number: int
    column: str
    reason: str


class UploadResult(BaseModel):
    imported: int
    skipped: int
    errors: list[RowError] = Field(default_factory=list)
    message: str = ""


# --------------------------------------------------------------------------- #
# Export / system
# --------------------------------------------------------------------------- #
class ExportJobRead(BaseModel):
    model_config = ORM

    job_id: uuid.UUID
    route_id: uuid.UUID
    status: ExportStatus
    attempts: int
    attempt_log: list | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationStatusRead(BaseModel):
    model_config = ORM

    system: str
    healthy: bool
    message: str | None = None
    last_success_at: datetime | None = None
    degraded_since: datetime | None = None
    updated_at: datetime
