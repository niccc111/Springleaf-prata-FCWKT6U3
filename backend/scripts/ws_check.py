"""Verify the WebSocket hub delivers the design's server -> client events.

Connects as a dispatcher, triggers a re-optimisation, a route lock, and a
priority order, then asserts the expected events arrive. Also checks that an
unauthenticated socket is refused.

    python scripts/ws_check.py [--base-url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
import websockets


async def main(base_url: str) -> int:
    ws_url = base_url.replace("http", "ws", 1) + "/api/v1/ws"
    async with httpx.AsyncClient(base_url=base_url, timeout=180) as c:
        tok = (await c.post("/api/v1/auth/login", json={"email":"dispatcher@roe.app","password":"dispatch12345"})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}

        # Unauthenticated socket must be refused.
        try:
            async with websockets.connect(f"{ws_url}?token=bogus"):
                print("  ✗ unauthenticated socket was accepted")
        except Exception as exc:
            print(f"  ✓ unauthenticated socket refused ({type(exc).__name__})")

        received = []
        async with websockets.connect(f"{ws_url}?token={tok}") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            print(f"  ✓ handshake event: {hello['event']}")

            async def collect():
                try:
                    while True:
                        msg = json.loads(await ws.recv())
                        if msg["event"] != "ping":
                            received.append(msg["event"])
                except Exception:
                    pass

            task = asyncio.create_task(collect())
            # Trigger work that should emit progress + completion + route updates.
            await c.post("/api/v1/optimise", headers=H, json={"reoptimise": True})
            routes = (await c.get("/api/v1/routes", headers=H)).json()
            if routes:
                await c.patch(f"/api/v1/routes/{routes[0]['route_id']}", headers=H, json={"locked": True})
            await c.post("/api/v1/orders", headers=H, json={
                "delivery_address": "6 Woodlands Square, Singapore 737737",
                "cargo_weight_kg": 15, "priority": "priority"})
            await asyncio.sleep(3)
            task.cancel()

        unique = sorted(set(received))
        print(f"  ✓ events received: {unique}")
        for expected in ("optimisation.progress", "optimisation.complete", "route.updated"):
            print(f"  {'✓' if expected in unique else '✗'} {expected}")
        return 0 if {"optimisation.progress","optimisation.complete","route.updated"} <= set(unique) else 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    sys.exit(asyncio.run(main(parser.parse_args().base_url)))
