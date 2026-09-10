"""Seed a realistic demo dataset: vehicles and orders across Singapore.

Usage:  python scripts/seed_demo.py [--orders N] [--vehicles N]
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select

from app.adapters.mapping import GAZETTEER
from app.core.bootstrap import ensure_seed_users
from app.db.session import session_scope
from app.models.entities import (
    Alert,
    ExportJob,
    GeocodingQueue,
    OptimisationRun,
    Order,
    Route,
    Stop,
    StopOrder,
    User,
    Vehicle,
)
from app.models.enums import OrderSource, OrderStatus, Priority, VehicleSource
from app.schemas.geo import GeoPoint

DEPOTS = [
    ("Alexandra Depot", 1.2790, 103.8090),
    ("Jurong Port Depot", 1.3163, 103.7127),
    ("Changi Business Park Depot", 1.3345, 103.9628),
    ("Ang Mo Kio Depot", 1.3810, 103.8480),
]

DRIVERS = [
    "A. Tan", "B. Lim", "C. Rahman", "D. Kaur", "E. Ong",
    "F. Chandra", "G. Wong", "H. Ismail", "J. Menon", "K. Goh",
]

DELIVERY_ANCHORS = [addr for addr in GAZETTEER if "depot" not in addr]


async def reset(session) -> None:
    await session.execute(delete(StopOrder))
    await session.execute(delete(Stop))
    await session.execute(delete(ExportJob))
    await session.execute(delete(Route))
    await session.execute(delete(GeocodingQueue))
    await session.execute(delete(Alert))
    await session.execute(delete(Order))
    await session.execute(delete(Vehicle))
    await session.execute(delete(OptimisationRun))


async def main(order_count: int, vehicle_count: int, seed: int) -> None:
    random.seed(seed)
    async with session_scope() as session:
        await ensure_seed_users(session)
        await reset(session)

        for index in range(vehicle_count):
            _, lat, lon = DEPOTS[index % len(DEPOTS)]
            capacity = random.choice([800, 1200, 1800, 2500])
            session.add(
                Vehicle(
                    vehicle_id=uuid.uuid4(),
                    source=VehicleSource.FMS.value,
                    external_ref=f"FMS-VEH-{index + 1:03d}",
                    registration=f"SG{random.randint(1000, 9999)}{chr(65 + index % 26)}",
                    capacity_weight_kg=Decimal(str(capacity)),
                    capacity_volume_m3=Decimal(str(round(capacity / 150, 1))),
                    depot_location=GeoPoint(latitude=lat, longitude=lon),
                    operating_hours_start=time(8, 0),
                    operating_hours_end=time(18, 0),
                    available=True,
                    driver_id=uuid.uuid4(),
                    driver_name=DRIVERS[index % len(DRIVERS)],
                )
            )

        day = datetime.now(UTC).date()
        for index in range(order_count):
            anchor = random.choice(DELIVERY_ANCHORS)
            lat, lon = GAZETTEER[anchor]
            location = GeoPoint(
                latitude=round(lat + random.uniform(-0.02, 0.02), 6),
                longitude=round(lon + random.uniform(-0.02, 0.02), 6),
            )
            window = None
            if random.random() < 0.6:
                start_hour = random.choice([9, 10, 11, 13, 14, 15])
                start = datetime.combine(day, time(start_hour, 0), tzinfo=UTC)
                window = (start, start + timedelta(hours=random.choice([3, 4, 5])))
            unit = f"#{random.randint(1, 25):02d}-{random.randint(1, 99):02d}"
            session.add(
                Order(
                    order_id=uuid.uuid4(),
                    source=random.choice(
                        [OrderSource.OMS.value, OrderSource.OMS.value, OrderSource.MANUAL.value]
                    ),
                    external_ref=f"OMS-{index + 1:05d}",
                    delivery_address=f"{anchor.title()} {unit}",
                    delivery_location=location,
                    cargo_weight_kg=Decimal(str(round(random.uniform(8, 220), 1))),
                    cargo_volume_m3=Decimal(str(round(random.uniform(0.1, 1.8), 2))),
                    time_window_start=window[0] if window else None,
                    time_window_end=window[1] if window else None,
                    priority=(
                        Priority.PRIORITY.value
                        if random.random() < 0.15
                        else Priority.STANDARD.value
                    ),
                    status=OrderStatus.UNASSIGNED.value,
                    service_duration_min=random.choice([5, 10, 15]),
                    geocode_confidence=0.97,
                )
            )

    async with session_scope() as session:
        orders = await session.scalar(select(func.count()).select_from(Order))
        vehicles = await session.scalar(select(func.count()).select_from(Vehicle))
        users = await session.scalar(select(func.count()).select_from(User))
    print(f"Seeded {orders} orders, {vehicles} vehicles, {users} users.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=int, default=60)
    parser.add_argument("--vehicles", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    asyncio.run(main(args.orders, args.vehicles, args.seed))
