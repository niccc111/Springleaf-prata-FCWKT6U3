"""Custom SQLAlchemy types for the ROE schema."""

from __future__ import annotations

import decimal
from typing import Any

from sqlalchemy import types as sqltypes
from sqlalchemy.engine.interfaces import Dialect

from app.core.serialisation import sanitise_text
from app.schemas.geo import GeoPoint


class SafeText(sqltypes.TypeDecorator[str]):
    """``TEXT`` that strips NUL code points before they reach PostgreSQL.

    PostgreSQL rejects U+0000 in ``text`` and ``jsonb``. User-derived strings
    (addresses, registrations, alert messages) can carry one after a bad
    integration payload or a copy-paste, and a rejected INSERT on an alert or
    audit row would fail the operation it was describing. Stripping the
    character keeps the record intact and the message readable.
    """

    impl = sqltypes.Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if isinstance(value, str):
            return sanitise_text(value)
        return value


class GeoPointType(sqltypes.UserDefinedType[GeoPoint]):
    """Maps the PostgreSQL ``geopoint`` composite type to :class:`GeoPoint`.

    The composite type is declared in the initial Alembic migration as::

        CREATE TYPE geopoint AS (latitude DECIMAL(10,7), longitude DECIMAL(11,7));

    asyncpg returns composites as records/tuples and accepts tuples on the way
    in, so the processors below translate between that and the Pydantic model.
    """

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "geopoint"

    def bind_processor(self, dialect: Dialect):
        def process(value: Any) -> Any:
            if value is None:
                return None
            point = GeoPoint.coerce(value)
            return (
                decimal.Decimal(str(point.latitude)),
                decimal.Decimal(str(point.longitude)),
            )

        return process

    def result_processor(self, dialect: Dialect, coltype: Any):
        def process(value: Any) -> GeoPoint | None:
            if value is None:
                return None
            return GeoPoint.coerce(value)

        return process

    def literal_processor(self, dialect: Dialect):
        def process(value: Any) -> str:
            point = GeoPoint.coerce(value)
            return f"({point.latitude},{point.longitude})"

        return process
