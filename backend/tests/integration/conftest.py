"""HTTP client fixtures for API integration tests.

The API has no authentication, so a plain client is all a test needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture(autouse=True)
async def clean_database(sessionmaker_):
    """Give every API test an empty database.

    These tests drive the API rather than the session fixture, so without this
    they would inherit whatever rows the previous test left behind and pass or
    fail depending on collection order.
    """
    from tests.conftest import truncate_all

    await truncate_all(sessionmaker_)
