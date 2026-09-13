"""End-to-end API tests over the main dispatcher journeys."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.entities import Alert, Order, Vehicle

pytestmark = pytest.mark.integration

ORDER_PAYLOAD = {
    "delivery_address": "30 Raffles Place, Singapore 048622",
    "cargo_weight_kg": 42.5,
    "cargo_volume_m3": 0.4,
    "priority": "standard",
    "service_duration_min": 10,
}

VEHICLE_PAYLOAD = {
    "registration": "SGTEST1A",
    "capacity_weight_kg": 1200,
    "capacity_volume_m3": 12,
    "depot_location": {"latitude": 1.279, "longitude": 103.809},
    "operating_hours_start": "08:00",
    "operating_hours_end": "18:00",
}


async def test_health_and_readiness(client):
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200


async def test_every_endpoint_is_open_without_credentials(client):
    """The console ships without authentication — no token, no login, no 401s."""
    for method, path in [
        ("GET", "/api/v1/routes"),
        ("GET", "/api/v1/orders"),
        ("GET", "/api/v1/vehicles"),
        ("GET", "/api/v1/alerts"),
        ("GET", "/api/v1/audit"),
        ("GET", "/api/v1/system/summary"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 200, f"{method} {path} -> {response.text}"


async def test_no_authentication_endpoints_are_exposed(client):
    """Login, token refresh and user administration no longer exist."""
    schema = (await client.get("/openapi.json")).json()
    assert not [p for p in schema["paths"] if "/auth" in p or "/users" in p]
    assert "securitySchemes" not in schema.get("components", {})


async def test_manual_order_entry_validates_and_persists(client, session):
    """Requirements 3.1, 3.3, 3.4."""
    invalid = await client.post(
        "/api/v1/orders",
        json={"delivery_address": "", "cargo_weight_kg": -1, "priority": "standard"},
    )
    assert invalid.status_code == 422
    fields = {f["field"] for f in invalid.json()["fields"]}
    assert "delivery_address" in fields
    assert "cargo_weight_kg" in fields
    assert await session.scalar(select(func.count()).select_from(Order)) == 0

    created = await client.post("/api/v1/orders", json=ORDER_PAYLOAD)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "manual"
    assert body["status"] == "unassigned"
    # Requirement 15.1 — the address is geocoded on creation.
    assert body["delivery_location"] is not None
    assert uuid.UUID(body["order_id"])


async def test_manual_coordinate_override_validates_range(client):
    """Requirement 15.5 / Property 31."""
    order = (await client.post("/api/v1/orders", json=ORDER_PAYLOAD)).json()

    bad = await client.post(
        f"/api/v1/orders/{order['order_id']}/coordinates",
        json={"latitude": 91.0, "longitude": 0.0},
    )
    assert bad.status_code == 422
    assert any("latitude" in f["field"] for f in bad.json()["fields"])

    good = await client.post(
        f"/api/v1/orders/{order['order_id']}/coordinates",
        json={"latitude": 1.3, "longitude": 103.85},
    )
    assert good.status_code == 200
    assert good.json()["delivery_location"] == {"latitude": 1.3, "longitude": 103.85}


async def test_vehicle_entry_and_listing(client):
    created = await client.post("/api/v1/vehicles", json=VEHICLE_PAYLOAD)
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "manual"

    listing = await client.get("/api/v1/vehicles")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


async def test_spreadsheet_upload_partitions_rows(client, session):
    """Requirements 4.4, 4.5."""
    csv_body = (
        "delivery_address,cargo_weight_kg,cargo_volume_m3,priority\n"
        "68 Orchard Road Singapore 238839,25,0.3,standard\n"
        "9 Bishan Place Singapore 579837,18.5,0.2,priority\n"
        ",not-a-number,0.1,bogus\n"
    )
    response = await client.post(
        "/api/v1/upload/orders",
        files={"file": ("orders.csv", csv_body.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert {e["row_number"] for e in body["errors"]} == {4}
    assert {e["column"] for e in body["errors"]} >= {"delivery_address", "cargo_weight_kg"}
    assert await session.scalar(select(func.count()).select_from(Order)) == 2


async def test_spreadsheet_upload_rejects_row_limit(client, session):
    """Requirement 4.7 — over-limit files are rejected before any processing."""
    rows = "delivery_address,cargo_weight_kg,priority\n" + "a,1,standard\n" * 10_001
    response = await client.post(
        "/api/v1/upload/orders",
        files={"file": ("big.csv", rows.encode(), "text/csv")},
    )
    assert response.status_code == 413
    assert "row limit" in response.json()["message"]
    assert await session.scalar(select(func.count()).select_from(Order)) == 0


async def test_upload_templates_round_trip(client):
    """Requirement 4.3 — the published template imports cleanly."""
    for kind in ("orders", "vehicles"):
        template = await client.get(f"/api/v1/upload/templates/{kind}")
        assert template.status_code == 200
        upload = await client.post(
            f"/api/v1/upload/{kind}",
            files={"file": (f"{kind}.csv", template.content, "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        assert upload.json()["imported"] == 1
        assert upload.json()["skipped"] == 0

    xlsx = await client.get("/api/v1/upload/templates/orders?format=xlsx")
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"  # a real .xlsx archive


async def test_optimise_requires_vehicles(client):
    """Requirement 5.10."""
    await client.post("/api/v1/orders", json=ORDER_PAYLOAD)
    response = await client.post("/api/v1/optimise", json={"reoptimise": False})
    assert response.status_code == 422
    assert "No vehicles are available" in response.json()["message"]


async def test_full_planning_journey(client, session):
    """Optimise → inspect → lock → approve → dispatch (Requirements 5, 11, 13)."""
    await client.post("/api/v1/vehicles", json=VEHICLE_PAYLOAD)
    for address in (
        "68 Orchard Road, Singapore 238839",
        "9 Bishan Place, Singapore 579837",
        "80 Marine Parade Road, Singapore 449269",
    ):
        await client.post(
            "/api/v1/orders",
            json={**ORDER_PAYLOAD, "delivery_address": address, "cargo_weight_kg": 30},
        )

    run = await client.post("/api/v1/optimise", json={"reoptimise": False})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"
    assert run.json()["routes_created"] == 1
    assert run.json()["orders_assigned"] == 3

    routes = (await client.get("/api/v1/routes")).json()
    assert len(routes) == 1
    route = routes[0]
    assert route["stop_count"] == 3
    assert route["total_weight_kg"] == pytest.approx(90.0)
    # Property 12 — never over capacity.
    assert route["total_weight_kg"] <= route["vehicle_capacity_weight_kg"]
    assert route["status"] == "draft"

    # Requirement 11.1 — lock, then unlock a draft route.
    locked = await client.patch(f"/api/v1/routes/{route['route_id']}", json={"locked": True})
    assert locked.status_code == 200
    assert locked.json()["locked"] is True

    unlocked = await client.patch(f"/api/v1/routes/{route['route_id']}", json={"locked": False})
    assert unlocked.json()["locked"] is False

    # Requirements 13.1/13.2 — approval exports and the acknowledgement dispatches.
    approved = await client.patch(
        f"/api/v1/routes/{route['route_id']}",
        json={"status": "approved"},
    )
    assert approved.status_code == 200, approved.text

    final = (await client.get(f"/api/v1/routes/{route['route_id']}")).json()
    assert final["status"] == "dispatched"
    # Requirement 11.6 / Property 26.
    assert final["locked"] is True

    # Requirement 11.5 — a dispatched route cannot be unlocked.
    refused = await client.patch(f"/api/v1/routes/{route['route_id']}", json={"locked": False})
    assert refused.status_code == 400

    exports = (await client.get(f"/api/v1/routes/{route['route_id']}/exports")).json()
    assert exports and exports[0]["status"] == "succeeded"


async def test_reoptimisation_preserves_locked_routes(client):
    """Requirements 11.3, 12.2 / Property 25."""
    for index in range(2):
        await client.post(
            "/api/v1/vehicles",
            json={
                **VEHICLE_PAYLOAD,
                "registration": f"SGLOCK{index}",
                "depot_location": {"latitude": 1.279 + index * 0.05, "longitude": 103.809},
            },
        )
    for address in (
        "68 Orchard Road, Singapore 238839",
        "9 Bishan Place, Singapore 579837",
        "4 Tampines Central 5, Singapore 529510",
        "50 Jurong Gateway Road, Singapore 608549",
    ):
        await client.post(
            "/api/v1/orders",
            json={**ORDER_PAYLOAD, "delivery_address": address, "cargo_weight_kg": 30},
        )

    await client.post("/api/v1/optimise", json={"reoptimise": False})
    routes = (await client.get("/api/v1/routes")).json()
    assert routes

    target = routes[0]
    await client.patch(f"/api/v1/routes/{target['route_id']}", json={"locked": True})
    before = (await client.get(f"/api/v1/routes/{target['route_id']}")).json()

    reopt = await client.post("/api/v1/optimise", json={"reoptimise": True})
    assert reopt.status_code == 200, reopt.text
    assert reopt.json()["locked_excluded_count"] == 1
    assert reopt.json()["diff"] is not None

    after = (await client.get(f"/api/v1/routes/{target['route_id']}")).json()
    assert [s["order_ids"] for s in after["stops"]] == [s["order_ids"] for s in before["stops"]]
    assert [s["eta"] for s in after["stops"]] == [s["eta"] for s in before["stops"]]
    assert after["total_distance_km"] == before["total_distance_km"]


async def test_records_cannot_be_edited_once_dispatched(client):
    """Requirement 3.6 — orders and vehicles freeze with their dispatched route."""
    vehicle = (await client.post("/api/v1/vehicles", json=VEHICLE_PAYLOAD)).json()
    order = (await client.post("/api/v1/orders", json=ORDER_PAYLOAD)).json()

    # Editable while the route is still a draft.
    assert (
        await client.patch(
            f"/api/v1/orders/{order['order_id']}",
            json={"cargo_weight_kg": 10},
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/api/v1/vehicles/{vehicle['vehicle_id']}",
            json={"capacity_weight_kg": 1500},
        )
    ).status_code == 200

    await client.post("/api/v1/optimise", json={"reoptimise": False})
    routes = (await client.get("/api/v1/routes")).json()
    assert routes
    approved = await client.patch(
        f"/api/v1/routes/{routes[0]['route_id']}",
        json={"status": "approved"},
    )
    assert approved.status_code == 200
    final = (await client.get(f"/api/v1/routes/{routes[0]['route_id']}")).json()
    assert final["status"] == "dispatched"

    blocked_order = await client.patch(
        f"/api/v1/orders/{order['order_id']}",
        json={"cargo_weight_kg": 11},
    )
    assert blocked_order.status_code == 422
    assert "dispatched" in blocked_order.json()["message"]

    blocked_vehicle = await client.patch(
        f"/api/v1/vehicles/{vehicle['vehicle_id']}",
        json={"capacity_weight_kg": 1600},
    )
    assert blocked_vehicle.status_code == 422
    assert "dispatched" in blocked_vehicle.json()["message"]


async def test_oms_ingest_endpoint_creates_orders(client, session):
    """Requirements 1.1, 1.4, 1.6."""
    events = [
        {
            "external_ref": "OMS-1",
            "delivery_address": "30 Raffles Place, Singapore 048622",
            "cargo_weight_kg": "18.5",
            "priority": "priority",
        },
        {"external_ref": "OMS-1", "delivery_address": "dup", "cargo_weight_kg": "5"},
        {"external_ref": "OMS-2", "delivery_address": "missing weight"},
    ]
    response = await client.post("/api/v1/system/integrations/oms/events", json=events)
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 1, "duplicates": 1, "rejected": 1}
    assert await session.scalar(select(func.count()).select_from(Order)) == 1
    # Requirement 1.2 — the rejection raised an Impossible Order Alert.
    assert await session.scalar(select(func.count()).select_from(Alert)) == 1


async def test_fms_ingest_endpoint_upserts_vehicles(client, session):
    """Requirements 2.2, 2.3, 2.5."""
    create = [
        {
            "event_type": "new_registration",
            "external_ref": "FMS-1",
            "registration": "SGFMS001",
            "capacity_weight_kg": "1500",
            "depot_location": {"latitude": 1.279, "longitude": 103.809},
            "operating_hours_start": "07:00",
            "operating_hours_end": "19:00",
        }
    ]
    assert (await client.post("/api/v1/system/integrations/fms/events", json=create)).json()[
        "created"
    ] == 1

    update = [{**create[0], "event_type": "update", "capacity_weight_kg": "1800"}]
    assert (await client.post("/api/v1/system/integrations/fms/events", json=update)).json()[
        "updated"
    ] == 1
    assert await session.scalar(select(func.count()).select_from(Vehicle)) == 1

    broken = [{**create[0], "event_type": "update"}]
    broken[0].pop("capacity_weight_kg")
    assert (await client.post("/api/v1/system/integrations/fms/events", json=broken)).json()[
        "rejected"
    ] == 1
    vehicle = await session.scalar(select(Vehicle))
    await session.refresh(vehicle)
    assert vehicle.available is False


async def test_alert_acknowledgement_is_first_writer_wins(client, session):
    """Requirements 14.5, 14.6 / Property 29."""
    events = [{"external_ref": "BAD-1", "delivery_address": "nowhere"}]
    await client.post("/api/v1/system/integrations/oms/events", json=events)

    alerts = (await client.get("/api/v1/alerts")).json()
    assert alerts
    alert_id = alerts[0]["alert_id"]

    first = await client.patch(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert first.status_code == 200
    assert first.json()["acknowledged"] is True

    second = await client.patch(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert second.status_code == 409
    assert second.json()["message"] == "Alert already acknowledged"


async def test_connectivity_and_config_endpoints(client):
    config = await client.get("/api/v1/system/config")
    assert config.status_code == 200
    assert set(config.json()) >= {"oms", "fms", "mapping", "delivery_platform"}

    connectivity = await client.get("/api/v1/system/connectivity")
    assert connectivity.status_code == 200
    assert isinstance(connectivity.json(), list)
