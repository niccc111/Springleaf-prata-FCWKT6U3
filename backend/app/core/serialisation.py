"""JSON-safe conversion helpers used by audit snapshots and API payloads."""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any

from pydantic import BaseModel

#: PostgreSQL text and jsonb cannot hold a NUL code point, so it is stripped
#: rather than allowed to fail an audit write (which would, per Requirement
#: 16.5, roll back the entity change it was recording).
NUL = "\x00"


def sanitise_text(value: str) -> str:
    return value.replace(NUL, "") if NUL in value else value


def to_jsonable(value: Any) -> Any:
    """Recursively convert a value into something ``json.dumps`` accepts."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        return sanitise_text(value)
    if isinstance(value, float):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return sanitise_text(str(value))
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC).isoformat()
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if isinstance(value, dict):
        return {sanitise_text(str(k)): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [to_jsonable(v) for v in value]
    return sanitise_text(str(value))
