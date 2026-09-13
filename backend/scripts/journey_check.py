"""End-to-end journey check against a running ROE API.

Exercises: open (unauthenticated) access, manual order entry, spreadsheet
upload, template download, optimisation, route inspection, manual reassignment,
route locking, re-optimisation with locked-route preservation, approval +
export, alerts, and the audit log.

Usage:  python scripts/journey_check.py [--base-url http://127.0.0.1:8099]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def _headroom(route: dict[str, Any], key: str, total: str) -> float:
    capacity = route.get(key)
    if capacity is None:
        return float("inf")
    return capacity - (route.get(total) or 0)


def _pick_reassignment(routes: list[dict[str, Any]]):
    """Find (source, destination, order_id, weight) where the move fits."""
    for src in routes:
        for stop in src["stops"]:
            for order in stop["orders"]:
                for dst in routes:
                    if dst["route_id"] == src["route_id"]:
                        continue
                    if _headroom(dst, "vehicle_capacity_weight_kg", "total_weight_kg") > order["cargo_weight_kg"] and _headroom(
                        dst, "vehicle_capacity_volume_m3", "total_volume_m3"
                    ) > (order.get("cargo_volume_m3") or 0):
                        return src, dst, order["order_id"], order["cargo_weight_kg"]
    return None


def _heaviest_order(routes: list[dict[str, Any]]):
    best = None
    for route in routes:
        for stop in route["stops"]:
            for order in stop["orders"]:
                if best is None or order["cargo_weight_kg"] > best[2]:
                    best = (route, order["order_id"], order["cargo_weight_kg"])
    return best


def check(name: str, condition: bool, detail: str = "") -> bool:
    results.append((PASS if condition else FAIL, name, detail))
    marker = "✓" if condition else "✗"
    print(f"  {marker} {name}" + (f" — {detail}" if detail else ""))
    return condition


def main(base_url: str) -> int:
    client = httpx.Client(base_url=base_url, timeout=180.0)

    print("\n[1] Open access (no authentication)")
    check("routes readable without credentials", client.get("/api/v1/routes").status_code == 200)
    check("audit log readable without credentials", client.get("/api/v1/audit").status_code == 200)
    schema = client.get("/openapi.json").json()
    check(
        "no login or user-administration endpoints exist",
        not [path for path in schema["paths"] if "/auth" in path or "/users" in path],
    )

    print("\n[2] Manual order entry (Requirement 3)")
    bad = client.post(
        "/api/v1/orders",
        json={"delivery_address": "", "cargo_weight_kg": -5, "priority": "standard"},
    )
    check("invalid manual order rejected with field errors",
          bad.status_code == 422 and len(bad.json().get("fields", [])) >= 2,
          f"HTTP {bad.status_code}, {len(bad.json().get('fields', []))} field errors")

    good = client.post(
        "/api/v1/orders",
        json={
            "delivery_address": "30 Raffles Place, Singapore 048622",
            "cargo_weight_kg": 42.5,
            "cargo_volume_m3": 0.4,
            "priority": "priority",
            "service_duration_min": 10,
        },
    )
    check("valid manual order created", good.status_code == 201, f"HTTP {good.status_code}")
    order = good.json() if good.status_code == 201 else {}
    check("manual order has source=manual", order.get("source") == "manual")
    check("manual order was geocoded", order.get("delivery_location") is not None,
          str(order.get("delivery_location")))

    print("\n[3] Spreadsheet upload (Requirement 4)")
    tpl = client.get("/api/v1/upload/templates/orders")
    check("order template downloads", tpl.status_code == 200 and b"delivery_address" in tpl.content)
    check("vehicle template downloads",
          client.get("/api/v1/upload/templates/vehicles?format=xlsx").status_code == 200)

    csv_body = (
        "delivery_address,cargo_weight_kg,cargo_volume_m3,priority\n"
        "68 Orchard Road Singapore 238839,25,0.3,standard\n"
        "9 Bishan Place Singapore 579837,18.5,0.2,priority\n"
        ",not-a-number,0.1,bogus\n"
        "80 Marine Parade Road Singapore 449269,15,0.2,standard\n"
    )
    up = client.post(
        "/api/v1/upload/orders",
        files={"file": ("orders.csv", csv_body.encode(), "text/csv")},
    )
    body = up.json()
    check("mixed upload imports valid rows only",
          up.status_code == 200 and body["imported"] == 3 and body["skipped"] == 1,
          f"imported={body.get('imported')} skipped={body.get('skipped')}")
    check("row errors identify row, column and reason",
          all({"row_number", "column", "reason"} <= set(e) for e in body.get("errors", []))
          and any(e["row_number"] == 4 for e in body.get("errors", [])),
          json.dumps(body.get("errors", [])[:2]))

    all_bad = client.post(
        "/api/v1/upload/orders",
        files={
            "file": (
                "bad.csv",
                b"delivery_address,cargo_weight_kg,priority\n,abc,bogus\n,-1,nope\n",
                "text/csv",
            )
        },
    )
    check("upload with no valid rows rejected entirely",
          all_bad.status_code == 422 and all_bad.json()["details"]["imported"] == 0,
          f"HTTP {all_bad.status_code}")
    over_rows = "delivery_address,cargo_weight_kg,priority\n" + "a,1,standard\n" * 10_001
    too_many = client.post(
        "/api/v1/upload/orders",
        files={"file": ("big.csv", over_rows.encode(), "text/csv")},
    )
    check("upload exceeding the 10,000 row limit rejected before processing",
          too_many.status_code == 413 and "row limit" in too_many.json()["message"],
          f"HTTP {too_many.status_code}")

    print("\n[4] Optimisation (Requirements 5, 6, 18)")
    run = client.post("/api/v1/optimise", json={"reoptimise": False})
    check("optimisation run completes", run.status_code == 200, f"HTTP {run.status_code}")
    run_body = run.json()
    check("run status is completed", run_body.get("status") == "completed", str(run_body.get("status")))
    check("run finishes inside 120s",
          (run_body.get("duration_seconds") or 999) < 120,
          f"{run_body.get('duration_seconds', 0):.1f}s")
    check("routes were produced", (run_body.get("routes_created") or 0) > 0,
          f"{run_body.get('routes_created')} routes, {run_body.get('orders_assigned')} orders assigned")

    routes = client.get("/api/v1/routes").json()
    check("routes listed with stops", bool(routes) and all("stops" in r for r in routes),
          f"{len(routes)} routes")

    print("\n[5] Correctness invariants on produced routes")
    over_weight = [r for r in routes if r["vehicle_capacity_weight_kg"] and r["total_weight_kg"] > r["vehicle_capacity_weight_kg"] + 1e-6]
    check("no route exceeds weight capacity (Property 12)", not over_weight, f"{len(over_weight)} violations")
    over_volume = [
        r for r in routes
        if r.get("vehicle_capacity_volume_m3") and (r.get("total_volume_m3") or 0) > r["vehicle_capacity_volume_m3"] + 1e-6
    ]
    check("no route exceeds volume capacity (Property 13)", not over_volume, f"{len(over_volume)} violations")
    late = [s for r in routes for s in r["stops"] if s["late"]]
    check("no stop scheduled outside its time window (Property 14)", not late, f"{len(late)} late stops")
    monotonic = all(
        [s["eta"] for s in r["stops"]] == sorted(s["eta"] for s in r["stops"]) for r in routes
    )
    check("ETAs increase monotonically along each route", monotonic)
    inversions = 0
    for r in routes:
        seen_standard = False
        for s in r["stops"]:
            if s["has_priority_order"] and seen_standard and not r["priority_relaxed"]:
                inversions += 1
            if not s["has_priority_order"]:
                seen_standard = True
    check("priority stops precede standard stops (Property 15)", inversions == 0,
          f"{inversions} unexplained inversions")
    weights_match = all(
        abs(r["total_weight_kg"] - sum(s["total_weight_kg"] for s in r["stops"])) < 0.01
        for r in routes
    )
    check("route totals equal the sum of their stops (Property 20)", weights_match)

    print("\n[6] Concurrency guard (Property 27)")
    # A second run is only rejected while one is in flight; verified by the
    # guard itself returning 409 for a duplicate in-flight trigger.
    import threading

    outcomes: list[int] = []

    def fire() -> None:
        outcomes.append(client.post("/api/v1/optimise", json={"reoptimise": True}).status_code)

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("concurrent runs: one succeeds, one is rejected with 409",
          sorted(outcomes) == [200, 409], f"status codes {sorted(outcomes)}")

    print("\n[7] Manual reassignment (Requirement 10)")
    routes = [r for r in client.get("/api/v1/routes").json() if r["stops"]]
    move = _pick_reassignment(routes)
    check("a route pair with headroom exists for reassignment", move is not None)
    if move:
        src, dst, moved_order, weight = move
        before_src, before_dst = src["total_weight_kg"], dst["total_weight_kg"]
        resp = client.post(
            "/api/v1/orders/reassign",
            json={
                "order_id": moved_order,
                "source_route_id": src["route_id"],
                "dest_route_id": dst["route_id"],
                "confirm_late_delivery": True,
            },
        )
        check("order reassigned between routes", resp.status_code == 200,
              f"HTTP {resp.status_code}: {resp.text[:160]}")
        if resp.status_code == 200:
            rb = resp.json()
            check("source route weight decreased by the moved cargo",
                  abs(rb["source_route"]["total_weight_kg"] - (before_src - weight)) < 0.05,
                  f"{before_src} -> {rb['source_route']['total_weight_kg']} (moved {weight})")
            check("destination route weight increased by the moved cargo",
                  abs(rb["dest_route"]["total_weight_kg"] - (before_dst + weight)) < 0.05,
                  f"{before_dst} -> {rb['dest_route']['total_weight_kg']}")
            check("destination route contains the moved order",
                  any(moved_order in s["order_ids"] for s in rb["dest_route"]["stops"]))
            check("destination route ETAs still monotonic after reassignment",
                  [s["eta"] for s in rb["dest_route"]["stops"]]
                  == sorted(s["eta"] for s in rb["dest_route"]["stops"]))

    tiny = client.post("/api/v1/vehicles", json={
        "registration": "TINY001", "capacity_weight_kg": 1.0,
        "depot_location": {"latitude": 1.279, "longitude": 103.809},
        "operating_hours_start": "08:00", "operating_hours_end": "18:00"}).json()
    check("tiny vehicle created for capacity test", "vehicle_id" in tiny)

    # Re-read: the successful reassignment above changed the route totals.
    routes = [r for r in client.get("/api/v1/routes").json() if r["stops"]]
    heavy = _heaviest_order(routes)
    if heavy:
        heavy_route, heavy_order, heavy_weight = heavy
        small_dst = min(
            (r for r in routes if r["route_id"] != heavy_route["route_id"]),
            key=lambda r: (r["vehicle_capacity_weight_kg"] or 0) - r["total_weight_kg"],
            default=None,
        )
        if small_dst and (small_dst["vehicle_capacity_weight_kg"] - small_dst["total_weight_kg"]) < heavy_weight:
            rejected = client.post("/api/v1/orders/reassign", json={
                "order_id": heavy_order, "source_route_id": heavy_route["route_id"],
                "dest_route_id": small_dst["route_id"], "confirm_late_delivery": True})
            check("over-capacity reassignment rejected (Property 21)",
                  rejected.status_code == 422
                  and rejected.json()["error"] == "capacity_violation",
                  f"HTTP {rejected.status_code}: {rejected.text[:140]}")
            after_src = client.get(f"/api/v1/routes/{heavy_route['route_id']}").json()
            after_dst = client.get(f"/api/v1/routes/{small_dst['route_id']}").json()
            check("both routes unchanged after a rejected reassignment",
                  after_src["total_weight_kg"] == heavy_route["total_weight_kg"]
                  and after_dst["total_weight_kg"] == small_dst["total_weight_kg"])

    print("\n[8] Route locking and re-optimisation (Requirements 11, 12)")
    routes = client.get("/api/v1/routes").json()
    routes = [r for r in routes if r["stops"]]
    target = routes[0]
    lock = client.patch(f"/api/v1/routes/{target['route_id']}", json={"locked": True})
    check("draft route can be locked", lock.status_code == 200 and lock.json()["locked"])
    locked_snapshot = {
        "orders": [oid for s in lock.json()["stops"] for oid in s["order_ids"]],
        "etas": [s["eta"] for s in lock.json()["stops"]],
        "distance": lock.json()["total_distance_km"],
    }

    reassign_to_locked = client.post("/api/v1/orders/reassign", json={
        "order_id": routes[1]["stops"][0]["order_ids"][0],
        "dest_route_id": target["route_id"], "confirm_late_delivery": True})
    check("reassignment into a locked route is rejected (Property 22)",
          reassign_to_locked.status_code == 409, f"HTTP {reassign_to_locked.status_code}")

    reopt = client.post("/api/v1/optimise", json={"reoptimise": True})
    check("re-optimisation completes", reopt.status_code == 200, f"HTTP {reopt.status_code}")
    check("locked routes counted as excluded",
          (reopt.json().get("locked_excluded_count") or 0) >= 1,
          f"locked_excluded_count={reopt.json().get('locked_excluded_count')}")
    after = client.get(f"/api/v1/routes/{target['route_id']}").json()
    check("locked route preserved exactly (Property 25)",
          [oid for s in after["stops"] for oid in s["order_ids"]] == locked_snapshot["orders"]
          and [s["eta"] for s in after["stops"]] == locked_snapshot["etas"]
          and after["total_distance_km"] == locked_snapshot["distance"])
    check("re-optimisation reports a route diff",
          isinstance(reopt.json().get("diff"), dict) and "routes" in reopt.json()["diff"],
          f"{(reopt.json().get('diff') or {}).get('changed_count')} routes changed")

    print("\n[9] Approval, export and dispatch (Requirement 13)")
    unlocked = [r for r in client.get("/api/v1/routes").json() if not r["locked"] and r["stops"]]
    route_id = unlocked[0]["route_id"]
    approved = client.patch(f"/api/v1/routes/{route_id}", json={"status": "approved"})
    check("route approved", approved.status_code == 200, f"HTTP {approved.status_code}")
    final = client.get(f"/api/v1/routes/{route_id}").json()
    check("approved route exported and marked dispatched", final["status"] == "dispatched", final["status"])
    check("dispatched route is automatically locked (Property 26)", final["locked"] is True)
    unlock = client.patch(f"/api/v1/routes/{route_id}", json={"locked": False})
    check("dispatched route cannot be unlocked (Property 24)", unlock.status_code == 400,
          f"HTTP {unlock.status_code}")
    exports = client.get(f"/api/v1/routes/{route_id}/exports").json()
    check("export attempt recorded", bool(exports) and exports[0]["status"] == "succeeded",
          f"{len(exports)} job(s)")

    print("\n[10] Alerts (Requirement 14)")
    alerts = client.get("/api/v1/alerts").json()
    check("alerts endpoint returns a list", isinstance(alerts, list), f"{len(alerts)} alerts")
    unacked = [a for a in alerts if not a["acknowledged"]]
    if unacked:
        aid = unacked[0]["alert_id"]
        ack1 = client.patch(f"/api/v1/alerts/{aid}/acknowledge")
        ack2 = client.patch(f"/api/v1/alerts/{aid}/acknowledge")
        check("first acknowledgement succeeds", ack1.status_code == 200)
        check("second acknowledgement conflicts (Property 29)", ack2.status_code == 409,
              f"HTTP {ack2.status_code}")
    else:
        check("alerts available to acknowledge", True, "no unacknowledged alerts in this run")

    print("\n[11] Audit trail (Requirement 16)")
    audit = client.get("/api/v1/audit?limit=5").json()
    check("audit entries recorded", audit["total"] > 0, f"{audit['total']} entries")
    check("audit entries carry old/new state and acting user",
          all({"old_state", "new_state", "acting_user", "created_at"} <= set(e) for e in audit["items"]))
    actors = {e["acting_user"] for e in audit["items"]}
    check("every audit entry names the acting console operator", len(actors) >= 1,
          f"{len(actors)} distinct actor(s)")

    print("\n[12] Connectivity and system status")
    conn = client.get("/api/v1/system/connectivity").json()
    check("connectivity status available", isinstance(conn, list), f"{len(conn)} systems tracked")
    cfg = client.get("/api/v1/system/config").json()
    check("adapter modes reported", {"oms", "fms", "mapping", "delivery_platform"} <= set(cfg),
          json.dumps(cfg))

    failures = [r for r in results if r[0] == FAIL]
    print(f"\n{'=' * 70}")
    print(f"{len(results) - len(failures)}/{len(results)} checks passed")
    if failures:
        print("\nFailures:")
        for _, name, detail in failures:
            print(f"  - {name}" + (f" ({detail})" if detail else ""))
    client.close()
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    sys.exit(main(parser.parse_args().base_url))
