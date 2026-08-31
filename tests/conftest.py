"""
Shared fixtures for the MM01 StudentDAQ tests.

These tests need no MM01 and no USB — they run against the simulated device in
app/mm01_bridge/virtual_device.py. That makes them a good check that an install
is healthy before you go looking for hardware problems.

    python3 -m pytest tests/ -q
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings

# Tests must give the same result on every machine, so ignore any .env file and
# scrub the matching environment variables. Otherwise a student with
# MM01_SIM_ENABLED=true set for a lab would get different results from one
# without it.
Settings.model_config["env_file"] = None
for _var in (
    "MM01_AUTO_SCAN",
    "MM01_POLL_INTERVAL_MS",
    "MM01_SIM_ENABLED",
    "MM01_SIM_COUNT",
):
    os.environ.pop(_var, None)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def test_app():
    """The real FastAPI app, with test settings and no device manager attached.

    Individual tests attach their own simulated manager.
    """
    import main as app_module

    app_module.app.state.settings = Settings()
    return app_module.app


@pytest_asyncio.fixture
async def client(test_app):
    """An HTTP client that talks to the app in-process — no server needed."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac
