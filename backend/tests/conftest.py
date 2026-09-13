"""Shared test fixtures.

A single event loop is created for the whole session so Hypothesis tests (which
are synchronous) can drive async code through :func:`run_async` while sharing
one asyncpg engine.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://roe:roe@localhost:5432/roe_test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("REDIS_ENABLED", "false")

from hypothesis import HealthCheck, Verbosity
from hypothesis import settings as hyp_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.adapters.delivery_platform import (
    MockDeliveryPlatformAdapter,
    set_delivery_platform_adapter,
)
from app.adapters.mapping import MockMappingAdapter, set_mapping_adapter
from app.core.config import settings
from app.db.base import Base
from app.db.session import configure_engine
from app.models.entities import Order, Vehicle
from app.models.enums import (
    OrderSource,
    OrderStatus,
    Priority,
    VehicleSource,
)
from app.schemas.geo import GeoPoint

hyp_settings.register_profile(
    "roe",
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
    ],
    verbosity=Verbosity.normal,
)
hyp_settings.register_profile(
    "fast",
    parent=hyp_settings.get_profile("roe"),
    max_examples=25,
)
hyp_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "roe"))

T = TypeVar("T")

TABLES_IN_TRUNCATION_ORDER = [
    "stop_orders",
    "stops",
    "export_jobs",
    "routes",
    "geocoding_queue",
    "alerts",
    "orders",
    "vehicles",
    "optimisation_runs",
    "travel_time_samples",
    "integration_status",
    "error_log",
]


@pytest.fixture(scope="session")
async def session_loop() -> asyncio.AbstractEventLoop:
    """The loop pytest-asyncio runs every async test on.

    ``asyncio_default_test_loop_scope = session`` (pytest.ini) gives the whole
    suite one loop; capturing it here lets the engine, the async tests, and the
    synchronous Hypothesis tests all share a single connection pool.
    """
    return asyncio.get_running_loop()


#: Alias kept for tests that only need the loop to exist.
@pytest.fixture(scope="session")
def event_loop_instance(session_loop) -> asyncio.AbstractEventLoop:
    return session_loop


@pytest.fixture(scope="session")
async def engine(session_loop):
    # NullPool keeps every session on a freshly-opened connection, which avoids
    # pooled connections outliving a test and keeps pre-ping out of the picture.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'geopoint') THEN
                        CREATE TYPE geopoint AS (
                            latitude DECIMAL(10,7), longitude DECIMAL(11,7)
                        );
                    END IF;
                END $$;
                """
            )
        )
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # Mirror migration 0002 so immutability is exercised in tests too.
        await conn.execute(
            text("CREATE RULE audit_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING")
        )
        await conn.execute(
            text("CREATE RULE audit_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING")
        )

    configure_engine(engine)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def sessionmaker_(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture(scope="session")
def run_async(session_loop) -> Callable[[Coroutine[Any, Any, T]], T]:
    """Drive a coroutine on the session loop.

    Hypothesis tests are synchronous, so they cannot ``await``; this runs their
    async scenario on the same loop the engine is bound to. Never call it from
    inside a coroutine — the loop would be re-entered.
    """

    def _run(coro: Coroutine[Any, Any, T]) -> T:
        return session_loop.run_until_complete(coro)

    return _run


@pytest.fixture(autouse=True)
def _reset_adapters():
    set_mapping_adapter(MockMappingAdapter())
    set_delivery_platform_adapter(MockDeliveryPlatformAdapter())
    yield
    set_mapping_adapter(None)
    set_delivery_platform_adapter(None)


TRUNCATE_STATEMENT = text(
    "TRUNCATE TABLE " + ", ".join(TABLES_IN_TRUNCATION_ORDER) + " RESTART IDENTITY CASCADE"
)


async def truncate_all(sessionmaker_: async_sessionmaker[AsyncSession]) -> None:
    """Empty every domain table (audit_log is append-only and kept)."""
    async with sessionmaker_() as session:
        # Fail fast rather than hang if another connection still holds a lock.
        await session.execute(text("SET LOCAL lock_timeout = '10s'"))
        await session.execute(TRUNCATE_STATEMENT)
        await session.commit()


@pytest.fixture
def truncate(sessionmaker_, run_async) -> Callable[[], None]:
    """Synchronous truncation for Hypothesis tests, which are not coroutines.

    Async tests must await :func:`truncate_all` instead — driving the session
    loop re-entrantly from inside a running coroutine would deadlock.
    """

    def _call() -> None:
        run_async(truncate_all(sessionmaker_))

    return _call


@pytest.fixture
async def session(sessionmaker_) -> AsyncIterator[AsyncSession]:
    await truncate_all(sessionmaker_)
    db = sessionmaker_()
    try:
        yield db
    finally:
        # Release the connection promptly — a lingering "idle in transaction"
        # session would block the next test's TRUNCATE.
        await db.rollback()
        await db.close()


@pytest.fixture
def make_session(sessionmaker_) -> Callable[[], AsyncSession]:
    return sessionmaker_


# --------------------------------------------------------------------------- #
# Entity builders
# --------------------------------------------------------------------------- #
DEPOT = GeoPoint(latitude=1.2790, longitude=103.8090)


def build_vehicle(**overrides: Any) -> Vehicle:
    defaults: dict[str, Any] = {
        "vehicle_id": uuid.uuid4(),
        "source": VehicleSource.MANUAL.value,
        "registration": f"SG{uuid.uuid4().hex[:6].upper()}",
        "capacity_weight_kg": Decimal("1000"),
        "capacity_volume_m3": Decimal("10"),
        "depot_location": DEPOT,
        "operating_hours_start": time(8, 0),
        "operating_hours_end": time(18, 0),
        "available": True,
    }
    defaults.update(overrides)
    return Vehicle(**defaults)


def build_order(**overrides: Any) -> Order:
    defaults: dict[str, Any] = {
        "order_id": uuid.uuid4(),
        "source": OrderSource.MANUAL.value,
        "delivery_address": "30 Raffles Place, Singapore 048622",
        "delivery_location": GeoPoint(latitude=1.2840, longitude=103.8515),
        "cargo_weight_kg": Decimal("50"),
        "cargo_volume_m3": Decimal("0.5"),
        "priority": Priority.STANDARD.value,
        "status": OrderStatus.UNASSIGNED.value,
        "service_duration_min": 10,
    }
    defaults.update(overrides)
    return Order(**defaults)


def make_actor() -> uuid.UUID:
    """An acting-operator id for audit attribution.

    The console has no accounts, so an actor is simply the identity recorded on
    the audit entry. Tests use distinct ids where they need to tell two
    concurrent operators apart.
    """
    return uuid.uuid4()


@pytest.fixture
def actor() -> uuid.UUID:
    return make_actor()


def planning_window(hours_from: int = 1, hours_to: int = 6) -> tuple[datetime, datetime]:
    base = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    return base + timedelta(hours=hours_from), base + timedelta(hours=hours_to)
