"""Redis pub/sub event bus bridging services to the WebSocket hub.

Falls back to a purely in-process bus when Redis is unavailable so the system
(and the test suite) keeps working without external infrastructure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.serialisation import to_jsonable

logger = get_logger(__name__)

CHANNEL = "roe:events"

# Server -> client event names (design: WebSocket events table)
ROUTE_UPDATED = "route.updated"
ROUTE_DELETED = "route.deleted"
ALERT_RAISED = "alert.raised"
ALERT_ACKNOWLEDGED = "alert.acknowledged"
OPTIMISATION_PROGRESS = "optimisation.progress"
OPTIMISATION_COMPLETE = "optimisation.complete"
OPTIMISATION_FAILED = "optimisation.failed"
CONNECTIVITY_WARNING = "connectivity.warning"
CONNECTIVITY_RESTORED = "connectivity.restored"
ORDERS_PENDING = "orders.pending"
REOPTIMISATION_SUGGESTED = "reoptimisation.suggested"

Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._redis: Any | None = None
        self._local_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._redis_failed = False

    async def _get_redis(self) -> Any | None:
        if not settings.redis_enabled or self._redis_failed:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
                await self._redis.ping()
            except Exception as exc:  # pragma: no cover - infrastructure dependent
                logger.warning("redis_unavailable", error=str(exc))
                self._redis = None
                self._redis_failed = True
        return self._redis

    async def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        message = {"event": event, "payload": to_jsonable(payload or {})}
        for queue in list(self._local_subscribers):
            queue.put_nowait(message)
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.publish(CHANNEL, json.dumps(message))
            except Exception as exc:  # pragma: no cover
                logger.warning("event_publish_failed", event=event, error=str(exc))

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events for one WebSocket client."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._local_subscribers.add(queue)
        redis = await self._get_redis()
        pubsub = None
        if redis is not None:
            try:
                pubsub = redis.pubsub()
                await pubsub.subscribe(CHANNEL)
            except Exception:  # pragma: no cover
                pubsub = None
        try:
            while True:
                if pubsub is not None:
                    get_local = asyncio.create_task(queue.get())
                    get_remote = asyncio.create_task(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    )
                    done, pending = await asyncio.wait(
                        {get_local, get_remote}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        result = task.result()
                        if task is get_local and result is not None:
                            yield result
                        elif task is get_remote and result:
                            try:
                                yield json.loads(result["data"])
                            except (ValueError, KeyError, TypeError):
                                continue
                else:
                    yield await queue.get()
        finally:
            self._local_subscribers.discard(queue)
            if pubsub is not None:  # pragma: no cover
                with contextlib.suppress(Exception):
                    await pubsub.unsubscribe(CHANNEL)
                    await pubsub.aclose()

    async def close(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                await self._redis.aclose()
            self._redis = None


event_bus = EventBus()


async def publish(event: str, payload: dict[str, Any] | None = None) -> None:
    await event_bus.publish(event, payload)
