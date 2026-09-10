"""Domain enumerations shared by the ORM, API schemas, and services."""

from __future__ import annotations

from enum import StrEnum


class OrderSource(StrEnum):
    OMS = "OMS"
    MANUAL = "manual"
    SPREADSHEET = "spreadsheet"


class VehicleSource(StrEnum):
    FMS = "FMS"
    MANUAL = "manual"
    SPREADSHEET = "spreadsheet"


class OrderStatus(StrEnum):
    UNASSIGNED = "unassigned"
    ASSIGNED = "assigned"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    FAILED = "failed"


class Priority(StrEnum):
    STANDARD = "standard"
    PRIORITY = "priority"


class RouteStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"


class OptimisationRunStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


class OptimisationRunType(StrEnum):
    INITIAL = "initial"
    REOPTIMISE = "reoptimise"


class AlertType(StrEnum):
    OVERLOAD = "overload"
    LATE_DELIVERY = "late_delivery"
    IMPOSSIBLE_ORDER = "impossible_order"
    #: Extension beyond the three types in the data dictionary, required by the
    #: design's Alert Routing table (Requirement 13.4).
    EXPORT_FAILURE = "export_failure"


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class EntityType(StrEnum):
    VEHICLE = "vehicle"
    ORDER = "order"
    ROUTE = "route"
    ALERT = "alert"
    USER = "user"


class AlertEntityType(StrEnum):
    VEHICLE = "vehicle"
    ORDER = "order"
    ROUTE = "route"


class UserRole(StrEnum):
    DISPATCHER = "dispatcher"
    ADMINISTRATOR = "administrator"


class ExportStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IntegrationSystem(StrEnum):
    OMS = "oms"
    FMS = "fms"
    MAPPING = "mapping"
    DELIVERY_PLATFORM = "delivery_platform"
