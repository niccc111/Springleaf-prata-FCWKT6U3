"""Structured application errors mapped to HTTP responses by the API layer."""

from __future__ import annotations

from typing import Any


class ROEError(Exception):
    """Base class for all domain errors."""

    status_code = 400
    error_code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        fields: list[dict[str, str]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or []
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.error_code, "message": self.message}
        if self.fields:
            payload["fields"] = self.fields
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationFailed(ROEError):
    status_code = 422
    error_code = "validation_failed"


class NotFound(ROEError):
    status_code = 404
    error_code = "not_found"


class Conflict(ROEError):
    status_code = 409
    error_code = "conflict"


class InvalidStateTransition(ROEError):
    status_code = 400
    error_code = "invalid_state_transition"


class CapacityViolation(ROEError):
    status_code = 422
    error_code = "capacity_violation"


class RouteLockedError(ROEError):
    status_code = 409
    error_code = "route_locked"


class ConfirmationRequired(ROEError):
    """Raised when an action needs explicit dispatcher confirmation first."""

    status_code = 409
    error_code = "confirmation_required"


class Unauthenticated(ROEError):
    status_code = 401
    error_code = "unauthenticated"


class Forbidden(ROEError):
    status_code = 403
    error_code = "forbidden"


class UploadRejected(ROEError):
    status_code = 413
    error_code = "upload_rejected"


class OperationTimeout(ROEError):
    status_code = 504
    error_code = "timeout"


class PersistenceFailure(ROEError):
    status_code = 500
    error_code = "persistence_failed"


class ExternalServiceError(ROEError):
    status_code = 502
    error_code = "external_service_error"
