"""Application configuration.

Every external integration is configurable and defaults to a safe local/mock
adapter so the system runs end-to-end without third-party credentials.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AdapterMode = Literal["mock", "http"]
#: The mapping service additionally supports Google Maps (Geocoding + Routes),
#: OSRM (free road-network routing, no API key), and Mapbox.
MappingAdapterMode = Literal["mock", "http", "google", "osrm", "mapbox"]


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
    #: IANA timezone the operating region works in. Vehicle operating hours are
    #: stored as wall-clock times and interpreted in this zone (e.g. an 08:00
    #: shift start means 08:00 local time). Order time windows remain absolute
    #: (UTC) instants. Defaults to Singapore.
    business_timezone: str = "Asia/Singapore"
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

    # ---- Authentication --------------------------------------------------
    jwt_secret_key: str = "change-me-in-production-roe-dev-secret-key"
    jwt_algorithm: Literal["HS256", "RS256"] = "HS256"
    jwt_issuer: str = "roe"
    jwt_audience: str = "roe-api"
    jwt_public_key: str | None = None
    jwt_jwks_url: str | None = None
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_hours: int = 24
    # Local demo only: use the seeded dispatcher identity for every request.
    # Keep this false outside a trusted local environment.
    auth_disabled: bool = False

    # Seed administrator, created on first startup when the users table is empty.
    bootstrap_admin_email: str = "admin@roe.app"
    bootstrap_admin_password: str = "admin12345"
    bootstrap_dispatcher_email: str | None = "dispatcher@roe.app"
    bootstrap_dispatcher_password: str = "dispatch12345"

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

    mapping_adapter: MappingAdapterMode = "mock"
    mapping_base_url: str | None = None
    mapping_api_key: str | None = None
    mapping_timeout_seconds: float = 10.0
    mapping_unavailable_threshold_seconds: int = 30
    mapping_retry_interval_seconds: int = 60
    #: Google Maps Platform API key (used when mapping_adapter == "google").
    #: Needs the Geocoding API and Routes API enabled on the key.
    google_maps_api_key: str | None = None
    #: Region bias for Google geocoding (ccTLD), e.g. "sg" for Singapore.
    google_maps_region: str = "sg"
    #: OSRM routing server (used when mapping_adapter == "osrm"). Defaults to
    #: the public demo server, which needs no key but is rate-limited and not
    #: for production traffic. Point at your own OSRM host for real use.
    osrm_base_url: str = "https://router.project-osrm.org"
    #: Mapbox access token (used when mapping_adapter == "mapbox"). A secret
    #: token (sk.) is recommended for server-side use. Powers geocoding,
    #: the travel-time/distance matrix, and road-following route geometry.
    mapbox_access_token: str | None = None
    #: ISO 3166-1 country code to bias Mapbox geocoding, e.g. "sg".
    mapbox_country: str = "sg"

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
    def is_production(self) -> bool:
        return self.environment in ("staging", "production")

    @property
    def business_tzinfo(self) -> ZoneInfo:
        """Resolved tzinfo for :attr:`business_timezone`.

        Falls back to UTC if the configured zone name is not available on the
        host, so a bad value degrades gracefully rather than crashing planning.
        """
        try:
            return ZoneInfo(self.business_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN used by Alembic and management commands."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    def validate_production_safety(self) -> None:
        """Fail fast when a deployed environment still uses insecure defaults.

        Called at application startup. In ``staging``/``production`` this refuses
        to boot with the placeholder JWT secret, disabled auth, or the seeded
        demo passwords, so an accidentally-shipped dev config cannot expose the
        API. Local/test environments are left untouched.
        """
        if not self.is_production:
            return

        problems: list[str] = []
        if self.auth_disabled:
            problems.append("AUTH_DISABLED must be false in a deployed environment")
        if "change-me" in self.jwt_secret_key or len(self.jwt_secret_key) < 32:
            problems.append(
                "JWT_SECRET_KEY must be set to a strong random value "
                "(>= 32 chars, no placeholder text)"
            )
        weak_passwords = {"admin12345", "dispatch12345", "", "password", "changeme"}
        if self.bootstrap_admin_password in weak_passwords:
            problems.append("BOOTSTRAP_ADMIN_PASSWORD must be changed from the demo default")
        if self.bootstrap_dispatcher_password in weak_passwords:
            problems.append(
                "BOOTSTRAP_DISPATCHER_PASSWORD must be changed from the demo default"
            )
        if not self.cors_origins or any(
            "localhost" in origin or "127.0.0.1" in origin for origin in self.cors_origins
        ):
            problems.append(
                "CORS_ORIGINS must list your public origin(s), not localhost"
            )

        if problems:
            raise RuntimeError(
                "Refusing to start in "
                f"{self.environment!r} with insecure configuration:\n  - "
                + "\n  - ".join(problems)
            )

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
