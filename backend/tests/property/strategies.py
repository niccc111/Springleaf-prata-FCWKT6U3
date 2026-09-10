"""Reusable Hypothesis strategies for ROE entities."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from hypothesis import strategies as st

from app.schemas.geo import GeoPoint

BASE_DAY = datetime(2026, 6, 1, tzinfo=UTC)

latitudes = st.floats(min_value=1.22, max_value=1.47, allow_nan=False, allow_infinity=False)
longitudes = st.floats(min_value=103.6, max_value=104.05, allow_nan=False, allow_infinity=False)


@st.composite
def geo_points(draw) -> GeoPoint:
    return GeoPoint(
        latitude=round(draw(latitudes), 5), longitude=round(draw(longitudes), 5)
    )


def decimals(min_value: str, max_value: str, places: int = 2) -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=Decimal(min_value),
        max_value=Decimal(max_value),
        places=places,
        allow_nan=False,
        allow_infinity=False,
    )


weights = decimals("0.01", "999.99", 2)
volumes = decimals("0.01", "9.99", 2)
capacities = decimals("100.00", "9999.99", 2)
addresses = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters='",\n\r'),
    min_size=1,
    max_size=80,
).map(lambda s: s.strip() or "1 Fallback Road")
external_refs = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-", min_size=3, max_size=20
)
priorities = st.sampled_from(["standard", "priority"])


@st.composite
def time_windows(draw) -> tuple[datetime, datetime]:
    start_hour = draw(st.integers(min_value=8, max_value=14))
    length = draw(st.integers(min_value=1, max_value=8))
    start = BASE_DAY + timedelta(hours=start_hour)
    return start, start + timedelta(hours=length)


@st.composite
def operating_hours(draw) -> tuple[time, time]:
    start = draw(st.integers(min_value=5, max_value=10))
    end = draw(st.integers(min_value=start + 4, max_value=23))
    return time(start, 0), time(end, 0)


@st.composite
def valid_oms_events(draw) -> dict:
    event: dict = {
        "external_ref": draw(external_refs),
        "delivery_address": draw(addresses),
        "delivery_location": draw(geo_points()).model_dump(),
        "cargo_weight_kg": str(draw(weights)),
        "priority": draw(priorities),
        "service_duration_min": draw(st.integers(min_value=0, max_value=60)),
    }
    if draw(st.booleans()):
        event["cargo_volume_m3"] = str(draw(volumes))
    if draw(st.booleans()):
        start, end = draw(time_windows())
        event["time_window_start"] = start.isoformat()
        event["time_window_end"] = end.isoformat()
    return event


@st.composite
def valid_fms_events(draw, *, event_type: str = "new_registration") -> dict:
    start, end = draw(operating_hours())
    event: dict = {
        "event_type": event_type,
        "external_ref": draw(external_refs),
        "registration": draw(
            st.text(alphabet="ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", min_size=4, max_size=12)
        ),
        "capacity_weight_kg": str(draw(capacities)),
        "depot_location": draw(geo_points()).model_dump(),
        "operating_hours_start": start.strftime("%H:%M"),
        "operating_hours_end": end.strftime("%H:%M"),
        "available": draw(st.booleans()),
    }
    if draw(st.booleans()):
        event["capacity_volume_m3"] = str(draw(decimals("1.00", "99.99", 2)))
    return event
