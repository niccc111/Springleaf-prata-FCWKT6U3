"""Application configuration.

Every external integration is configurable and defaults to a safe local/mock
adapter so the system runs end-to-end without third-party credentials. The
console itself has no authentication: there are no accounts, no passwords and
no login step, so no credential settings appear here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AdapterMode = Literal["mock", "http"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application -----------------------------------------------------
    app_name: str = "Route Optimisation Engine"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    #: NoDecode keeps pydantic-settings from JSON-parsing the raw value, so a
    #: plain comma-separated .env entry works as well as a JSON array.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # ---- Database --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://roe:roe@localhost:5432/roe"
    database_pool_size: int = 20
    database_max_overflow: int = 20
    database_echo: bool = False

    # ---- Redis -----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True

    # ---- Celery ----------------------------------------------------------
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_always_eager: bool = False

    # ---- External integrations ------------------------------------------
    oms_adapter: AdapterMode = "mock"
    oms_base_url: str | None = None
    oms_api_key: str | None = None
    oms_poll_interval_seconds: int = 15
    oms_unavailable_threshold_seconds: int = 60

    fms_adapter: AdapterMode = "mock"
    fms_base_url: str | None = None
    fms_api_key: str | None = None
    fms_poll_interval_seconds: int = 15
    fms_unavailable_threshold_seconds: int = 30

    mapping_adapter: AdapterMode = "mock"
    mapping_base_url: str | None = None
    mapping_api_key: str | None = None
    mapping_timeout_seconds: float = 10.0
    mapping_unavailable_threshold_seconds: int = 30
    mapping_retry_interval_seconds: int = 60

    delivery_platform_adapter: AdapterMode = "mock"
    delivery_platform_base_url: str | None = None
    delivery_platform_api_key: str | None = None
    delivery_platform_timeout_seconds: float = 10.0

    # ---- Optimisation ----------------------------------------------------
    optimisation_wall_clock_limit_seconds: float = 120.0
    optimisation_solver_limit_seconds: float = 110.0
    optimisation_progress_interval_seconds: float = 3.0
    optimisation_solver_workers: int = 8
    default_service_duration_min: int = 10
    average_speed_kmh: float = 32.0

    # ---- Manual operations ----------------------------------------------
    reassignment_timeout_seconds: float = 10.0
    manual_entry_sla_seconds: float = 2.0

    # ---- Upload ----------------------------------------------------------
    upload_max_bytes: int = 50 * 1024 * 1024
    upload_max_rows: int = 10_000

    # ---- Geocoding -------------------------------------------------------
    geocode_confidence_threshold: float = 0.8
    geocode_max_attempts: int = 5
    geocode_retry_interval_seconds: int = 60

    # ---- Export ----------------------------------------------------------
    export_max_retries: int = 3
    export_initial_backoff_seconds: float = 5.0
    export_backoff_multiplier: float = 2.0

    # ---- Retention -------------------------------------------------------
    audit_retention_days: int = 365
    route_retention_days: int = 365
    alert_retention_days: int = 90

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b`` as well as ``["a","b"]``."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN used by Alembic and management commands."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    def export_backoff_schedule(self) -> list[float]:
        """Delays (seconds) preceding each retry attempt: 5s, 10s, 20s."""
        return [
            self.export_initial_backoff_seconds * (self.export_backoff_multiplier**i)
            for i in range(self.export_max_retries)
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
