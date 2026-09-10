"""Delivery Platform adapter (outbound route push).

``MockDeliveryPlatformAdapter`` records exports in memory and acknowledges them,
so the full approve → export → dispatched flow works without credentials.
``HttpDeliveryPlatformAdapter`` posts the design's payload to a real platform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ExportAck:
    acknowledged: bool
    reference: str | None = None
    error: str | None = None


class DeliveryPlatformAdapter(ABC):
    name = "delivery_platform"

    @abstractmethod
    async def export_route(self, payload: dict[str, Any]) -> ExportAck: ...

    async def aclose(self) -> None:  # pragma: no cover
        return None


@dataclass
class MockDeliveryPlatformAdapter(DeliveryPlatformAdapter):
    """Local stand-in. Set ``fail_next`` in tests to exercise the retry path."""

    name: str = "delivery_platform:mock"
    exported: list[dict[str, Any]] = field(default_factory=list)
    fail_next: int = 0
    always_fail: bool = False

    async def export_route(self, payload: dict[str, Any]) -> ExportAck:
        if self.always_fail or self.fail_next > 0:
            if self.fail_next > 0:
                self.fail_next -= 1
            return ExportAck(acknowledged=False, error="Delivery Platform rejected the route")
        self.exported.append(payload)
        return ExportAck(acknowledged=True, reference=f"DP-{payload['route_id'][:8]}")


class HttpDeliveryPlatformAdapter(DeliveryPlatformAdapter):
    name = "delivery_platform:http"

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, headers=headers
            )
        return self._client

    async def export_route(self, payload: dict[str, Any]) -> ExportAck:
        try:
            response = await self._get_client().post("/routes", json=payload)
            response.raise_for_status()
            body = response.json() if response.content else {}
            return ExportAck(acknowledged=True, reference=body.get("reference"))
        except httpx.HTTPError as exc:
            logger.warning("delivery_platform_export_failed", error=str(exc))
            return ExportAck(acknowledged=False, error=str(exc))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_adapter: DeliveryPlatformAdapter | None = None


def get_delivery_platform_adapter() -> DeliveryPlatformAdapter:
    global _adapter
    if _adapter is None:
        if settings.delivery_platform_adapter == "http" and settings.delivery_platform_base_url:
            _adapter = HttpDeliveryPlatformAdapter(
                settings.delivery_platform_base_url,
                settings.delivery_platform_api_key,
                settings.delivery_platform_timeout_seconds,
            )
        else:
            _adapter = MockDeliveryPlatformAdapter()
    return _adapter


def set_delivery_platform_adapter(adapter: DeliveryPlatformAdapter | None) -> None:
    global _adapter
    _adapter = adapter
