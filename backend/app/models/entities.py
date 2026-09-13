"""SQLAlchemy ORM models mirroring the design document's PostgreSQL schema."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, updated_at_column, uuid_pk
from app.db.types import GeoPointType, SafeText
from app.models.enums import (
    AlertSeverity,
    AlertType,
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


def _enum_check(column: str, enum_cls: type) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(Text, nullable=False)
    pickup_location: Mapped[GeoPoint | None] = mapped_column(GeoPointType())
    delivery_location: Mapped[GeoPoint | None] = mapped_column(GeoPointType())
    delivery_address: Mapped[str] = mapped_column(SafeText(), nullable=False)
    cargo_weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    cargo_volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    time_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(Text, nullable=False, default=Priority.STANDARD.value)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=OrderStatus.UNASSIGNED.value)
    service_duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    geocode_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    geocode_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    external_ref: Mapped[str | None] = mapped_column(SafeText(), unique=True)

    __table_args__ = (
        CheckConstraint("cargo_weight_kg >= 0", name="orders_cargo_weight_non_negative"),
        CheckConstraint(
            "cargo_volume_m3 IS NULL OR cargo_volume_m3 >= 0",
            name="orders_cargo_volume_non_negative",
        ),
        CheckConstraint("service_duration_min >= 0", name="orders_service_duration_non_negative"),
        CheckConstraint(_enum_check("source", OrderSource), name="orders_source"),
        CheckConstraint(_enum_check("priority", Priority), name="orders_priority"),
        CheckConstraint(_enum_check("status", OrderStatus), name="orders_status"),
        CheckConstraint(
            "time_window_start IS NULL OR time_window_end IS NULL "
            "OR time_window_start <= time_window_end",
            name="time_window_order",
        ),
        Index("idx_orders_status", "status"),
        Index(
            "idx_orders_external_ref",
            "external_ref",
            postgresql_where=sa_text("external_ref IS NOT NULL"),
        ),
        Index("idx_orders_created_at", "created_at"),
    )


class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(Text, nullable=False)
    registration: Mapped[str] = mapped_column(SafeText(), nullable=False)
    capacity_weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    capacity_volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    depot_location: Mapped[GeoPoint] = mapped_column(GeoPointType(), nullable=False)
    operating_hours_start: Mapped[time] = mapped_column(Time, nullable=False)
    operating_hours_end: Mapped[time] = mapped_column(Time, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    driver_name: Mapped[str | None] = mapped_column(SafeText())
    external_ref: Mapped[str | None] = mapped_column(SafeText(), unique=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint("capacity_weight_kg > 0", name="vehicles_capacity_weight_positive"),
        CheckConstraint(
            "capacity_volume_m3 IS NULL OR capacity_volume_m3 > 0",
            name="vehicles_capacity_volume_positive",
        ),
        CheckConstraint(_enum_check("source", VehicleSource), name="vehicles_source"),
        Index(
            "idx_vehicles_external_ref",
            "external_ref",
            postgresql_where=sa_text("external_ref IS NOT NULL"),
        ),
        Index("idx_vehicles_available", "available"),
    )


class OptimisationRun(Base):
    __tablename__ = "optimisation_runs"

    run_id: Mapped[uuid.UUID] = uuid_pk()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    run_type: Mapped[str] = mapped_column(
        Text, nullable=False, default=OptimisationRunType.INITIAL.value
    )
    started_at: Mapped[datetime] = created_at_column()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_excluded_count: Mapped[int | None] = mapped_column(Integer)
    orders_considered: Mapped[int | None] = mapped_column(Integer)
    orders_assigned: Mapped[int | None] = mapped_column(Integer)
    orders_unassigned: Mapped[int | None] = mapped_column(Integer)
    routes_created: Mapped[int | None] = mapped_column(Integer)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(SafeText())
    error_message: Mapped[str | None] = mapped_column(SafeText())
    solver_status: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    diff: Mapped[dict | None] = mapped_column(JSONB)
    initiated_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_enum_check("status", OptimisationRunStatus), name="runs_status"),
        CheckConstraint(_enum_check("run_type", OptimisationRunType), name="runs_type"),
        Index("idx_runs_status", "status"),
        Index("idx_runs_started_at", "started_at"),
    )


class Route(Base):
    __tablename__ = "routes"

    route_id: Mapped[uuid.UUID] = uuid_pk()
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.vehicle_id"), nullable=False
    )
    optimisation_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("optimisation_runs.run_id"), nullable=False
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    total_distance_km: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), nullable=False, default=Decimal("0")
    )
    total_duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), nullable=False, default=Decimal("0")
    )
    total_volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=RouteStatus.DRAFT.value)
    #: Set when travel times came from historical averages rather than live traffic
    #: (Requirements 7.4, 7.5, 9.5).
    data_quality_warning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_quality_message: Mapped[str | None] = mapped_column(SafeText())
    #: Set when a new priority order overlaps this route (Requirement 6.2).
    needs_reoptimisation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: True when priority-before-standard ordering had to be relaxed to keep the
    #: route feasible (Requirement 6.4, Property 15 escape hatch).
    priority_relaxed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    geometry: Mapped[dict | None] = mapped_column(JSONB)
    travel_times_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    stops: Mapped[list[Stop]] = relationship(
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="Stop.sequence_number",
        lazy="selectin",
    )
    vehicle: Mapped[Vehicle] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("total_distance_km >= 0", name="routes_distance_non_negative"),
        CheckConstraint("total_duration_min >= 0", name="routes_duration_non_negative"),
        CheckConstraint("total_weight_kg >= 0", name="routes_weight_non_negative"),
        CheckConstraint(
            "total_volume_m3 IS NULL OR total_volume_m3 >= 0", name="routes_volume_non_negative"
        ),
        CheckConstraint(_enum_check("status", RouteStatus), name="routes_status"),
        CheckConstraint(
            "status NOT IN ('dispatched', 'completed') OR locked = TRUE",
            name="dispatched_routes_locked",
        ),
        Index("idx_routes_vehicle_id", "vehicle_id"),
        Index("idx_routes_status", "status"),
        Index("idx_routes_run_id", "optimisation_run_id"),
        Index("idx_routes_completed_at", "completed_at"),
    )


class Stop(Base):
    __tablename__ = "stops"

    stop_id: Mapped[uuid.UUID] = uuid_pk()
    route_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=False
    )
    location: Mapped[GeoPoint] = mapped_column(GeoPointType(), nullable=False)
    address: Mapped[str] = mapped_column(SafeText(), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    distance_from_previous_km: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), nullable=False, default=Decimal("0")
    )
    travel_time_from_previous_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_priority_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    route: Mapped[Route] = relationship(back_populates="stops")
    stop_orders: Mapped[list[StopOrder]] = relationship(
        back_populates="stop", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("sequence_number >= 1", name="stops_sequence_positive"),
        CheckConstraint("service_duration_min >= 0", name="stops_service_duration_non_negative"),
        UniqueConstraint("route_id", "sequence_number", name="uq_stops_route_sequence"),
        Index("idx_stops_route_id", "route_id"),
    )

    @property
    def order_ids(self) -> list[uuid.UUID]:
        return [link.order_id for link in self.stop_orders]


class StopOrder(Base):
    __tablename__ = "stop_orders"

    stop_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stops.stop_id", ondelete="CASCADE"),
        primary_key=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.order_id"), primary_key=True
    )

    stop: Mapped[Stop] = relationship(back_populates="stop_orders")
    order: Mapped[Order] = relationship(lazy="joined")

    __table_args__ = (Index("idx_stop_orders_order_id", "order_id"),)


class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[uuid.UUID] = uuid_pk()
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    message: Mapped[str] = mapped_column(SafeText(), nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)
    raised_at: Mapped[datetime] = created_at_column()
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_enum_check("alert_type", AlertType), name="alerts_type"),
        CheckConstraint(_enum_check("severity", AlertSeverity), name="alerts_severity"),
        CheckConstraint("entity_type IN ('vehicle', 'order', 'route')", name="alerts_entity_type"),
        Index(
            "idx_alerts_acknowledged",
            "acknowledged",
            postgresql_where=sa_text("acknowledged = FALSE"),
        ),
        Index("idx_alerts_raised_at", "raised_at"),
        Index("idx_alerts_entity", "entity_type", "entity_id"),
    )


class AuditLog(Base):
    """Append-only audit trail. UPDATE/DELETE are blocked by database rules."""

    __tablename__ = "audit_log"

    log_id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    old_state: Mapped[dict | None] = mapped_column(JSONB)
    new_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    acting_user: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        PG_TIMESTAMP(timezone=True, precision=3), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('order', 'vehicle', 'route', 'alert')",
            name="audit_entity_type",
        ),
        Index("idx_audit_entity", "entity_type", "entity_id", "created_at"),
        Index("idx_audit_created_at", "created_at"),
        Index("idx_audit_acting_user", "acting_user"),
    )


class ErrorLog(Base):
    """Out-of-transaction error sink (design: audit write failures)."""

    __tablename__ = "error_log"

    error_id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(SafeText(), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("idx_error_log_created_at", "created_at"),)


class GeocodingQueue(Base):
    __tablename__ = "geocoding_queue"

    queue_id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    )
    address: Mapped[str] = mapped_column(SafeText(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry: Mapped[datetime] = created_at_column()
    last_error: Mapped[str | None] = mapped_column(SafeText())
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exhausted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        Index("idx_geocoding_queue_next_retry", "next_retry"),
        UniqueConstraint("order_id", name="uq_geocoding_queue_order_id"),
    )


class ExportJob(Base):
    """Tracks Delivery Platform export attempts and back-off state."""

    __tablename__ = "export_jobs"

    job_id: Mapped[uuid.UUID] = uuid_pk()
    route_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default=ExportStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_log: Mapped[list | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(SafeText())
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint(_enum_check("status", ExportStatus), name="export_jobs_status"),
        Index("idx_export_jobs_route_id", "route_id"),
    )


class IntegrationStatus(Base):
    """Live connectivity state for each external system (Requirements 1.5, 2.6, 7.4)."""

    __tablename__ = "integration_status"

    system: Mapped[str] = mapped_column(Text, primary_key=True)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    message: Mapped[str | None] = mapped_column(SafeText())
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    degraded_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at_column()


class TravelTimeSample(Base):
    """Historical travel-time averages used when the Mapping Service is down."""

    __tablename__ = "travel_time_samples"

    from_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    to_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_distance_metres: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = updated_at_column()


__all__ = [
    "Alert",
    "AuditLog",
    "ErrorLog",
    "ExportJob",
    "GeocodingQueue",
    "IntegrationStatus",
    "OptimisationRun",
    "Order",
    "Route",
    "Stop",
    "StopOrder",
    "TravelTimeSample",
    "Vehicle",
    "date",
]
