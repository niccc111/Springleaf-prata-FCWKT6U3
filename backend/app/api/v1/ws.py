"""WebSocket hub fanning Redis pub/sub events out to dispatcher clients.

Task 15.2 — Requirements 8.6, 12.6, 14.4, 18.4, 18.6.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core import events
from app.core.errors import Unauthenticated
from app.core.logging import get_logger
from app.core.security import decode_token

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def dispatcher_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
    """Authenticated event stream. The token is passed as a query parameter
    because browsers cannot set headers on a WebSocket handshake."""
    try:
        claims = decode_token(token, expected_type="access")
    except Unauthenticated:
        await websocket.close(code=4401, reason="Missing or invalid credentials")
        return

    await websocket.accept()
    await websocket.send_json(
        {"event": "connected", "payload": {"user_id": claims["sub"], "role": claims.get("role")}}
    )

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(25)
            await websocket.send_json({"event": "ping", "payload": {}})

    beat = asyncio.create_task(heartbeat())
    try:
        async for message in events.event_bus.subscribe():
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport level
        logger.warning("websocket_error", error=str(exc))
    finally:
        beat.cancel()
        with contextlib.suppress(Exception):
            await beat
        with contextlib.suppress(Exception):
            await websocket.close()
