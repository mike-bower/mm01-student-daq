"""
Integration tests for the /mm01 router (StudentDAQ / MultiDAQ).

Uses SimMM01DeviceManager so no USB hardware or `hid` package is needed.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.mm01_bridge import protocol as proto
from app.mm01_bridge.constants import GAIN, GAIN_FACTOR
from app.mm01_bridge.sim_manager import SimMM01DeviceManager

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_mm01_manager():
    """The test app is a module-level singleton — never leak a manager between tests."""
    import main as app_module

    def _drop():
        if hasattr(app_module.app.state, "mm01_manager"):
            delattr(app_module.app.state, "mm01_manager")

    _drop()
    yield
    _drop()


@pytest_asyncio.fixture
async def mm01_manager(test_app):
    """A scanned two-device simulator attached to the app, not yet streaming."""
    manager = SimMM01DeviceManager(device_count=2, poll_interval_ms=50)
    manager.scan()
    test_app.state.mm01_manager = manager
    yield manager
    await manager.stop()


@pytest_asyncio.fixture
async def mm01_client(test_app, mm01_manager):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac


# ── Availability ──────────────────────────────────────────────────────────────

async def test_returns_503_when_mm01_not_enabled(client):
    resp = await client.get("/mm01/devices")
    assert resp.status_code == 503
    assert "MM01_ENABLED" in resp.json()["detail"]


# ── Device listing ────────────────────────────────────────────────────────────

class TestDeviceListing:
    async def test_lists_all_simulated_devices(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices")
        assert resp.status_code == 200
        assert resp.json()["total_devices"] == 2

    async def test_device_identifies_as_mm01(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices")
        dev = resp.json()["devices"][0]
        assert dev["product_type"] == "MM01"
        assert dev["product_id"] == 0xF002
        assert dev["vendor_id"] == 0x275F

    async def test_firmware_version_read_at_scan(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices")
        assert resp.json()["devices"][0]["firmware_version"] == "2.0"

    async def test_defaults_to_quarter_bridge(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices")
        dev = resp.json()["devices"][0]
        assert dev["bridge"] == 0
        assert dev["bridge_name"] == "QB"

    async def test_get_single_device(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices/1")
        assert resp.status_code == 200
        assert resp.json()["device_index"] == 1

    async def test_unknown_device_returns_404(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices/99")
        assert resp.status_code == 404

    async def test_rescan_returns_devices(self, mm01_client):
        resp = await mm01_client.post("/mm01/scan")
        assert resp.status_code == 200
        assert resp.json()["total_devices"] == 2


# ── Readings ──────────────────────────────────────────────────────────────────

class TestReadings:
    async def test_snapshot_lists_every_device(self, mm01_client):
        resp = await mm01_client.get("/mm01/readings")
        assert resp.status_code == 200
        assert set(resp.json()["readings"]) == {"0", "1"}

    async def test_reading_is_null_before_streaming_starts(self, mm01_client):
        """NaN must serialise as null, not crash the response model."""
        resp = await mm01_client.get("/mm01/devices/0/reading")
        assert resp.status_code == 200
        assert resp.json()["microstrain"] is None

    async def test_reading_unknown_device_returns_404(self, mm01_client):
        resp = await mm01_client.get("/mm01/devices/99/reading")
        assert resp.status_code == 404


# ── Configuration ─────────────────────────────────────────────────────────────

class TestBridgeConfiguration:
    async def test_set_full_bridge(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/bridge", json={"bridge": 2})
        assert resp.status_code == 200
        dev = (await mm01_client.get("/mm01/devices/0")).json()
        assert dev["bridge"] == 2
        assert dev["bridge_name"] == "FB"

    async def test_full_bridge_disables_internal_excitation(self, mm01_client, mm01_manager):
        await mm01_client.post("/mm01/devices/0/bridge", json={"bridge": 2})
        assert mm01_manager._handles[0].qb_excitation is False

    async def test_quarter_bridge_enables_internal_excitation(self, mm01_client, mm01_manager):
        await mm01_client.post("/mm01/devices/0/bridge", json={"bridge": 2})
        await mm01_client.post("/mm01/devices/0/bridge", json={"bridge": 0})
        assert mm01_manager._handles[0].qb_excitation is True

    async def test_invalid_bridge_rejected(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/bridge", json={"bridge": 7})
        assert resp.status_code == 422

    async def test_unknown_device_returns_404(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/99/bridge", json={"bridge": 0})
        assert resp.status_code == 404


class TestGageFactor:
    async def test_set_gage_factor(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/gage-factor", json={"gage_factor": 2.11})
        assert resp.status_code == 200
        dev = (await mm01_client.get("/mm01/devices/0")).json()
        assert dev["gage_factor"] == pytest.approx(2.11)

    async def test_zero_gage_factor_rejected(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/gage-factor", json={"gage_factor": 0})
        assert resp.status_code == 422

    async def test_negative_gage_factor_rejected(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/gage-factor", json={"gage_factor": -2.0})
        assert resp.status_code == 422

    async def test_is_per_device(self, mm01_client):
        await mm01_client.post("/mm01/devices/0/gage-factor", json={"gage_factor": 2.5})
        other = (await mm01_client.get("/mm01/devices/1")).json()
        assert other["gage_factor"] == pytest.approx(2.0)


class TestLabel:
    async def test_set_label(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/label", json={"label": "Beam A"})
        assert resp.status_code == 200
        assert (await mm01_client.get("/mm01/devices/0")).json()["label"] == "Beam A"

    async def test_overlong_label_rejected(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/0/label", json={"label": "x" * 33})
        assert resp.status_code == 422


class TestZero:
    async def test_zero_returns_the_measured_offset(self, mm01_client, mm01_manager):
        """A constant 500 µε signal must balance to its exact volts equivalent."""
        vdev = mm01_manager._handles[0]
        vdev.current_microstrain = lambda: 500.0

        resp = await mm01_client.post("/mm01/devices/0/zero")
        assert resp.status_code == 200
        expected_volts = 500.0 / (GAIN * GAIN_FACTOR)   # gage factor 2.0 cancels
        assert resp.json()["zero_offset"] == pytest.approx(expected_volts, rel=1e-3)

    async def test_zero_leaves_the_adc_running(self, mm01_client, mm01_manager):
        """avg_adc stops the ADC; the manager must restart it or the stream dies."""
        vdev = mm01_manager._handles[0]
        vdev.current_microstrain = lambda: 0.0
        await mm01_client.post("/mm01/devices/0/zero")
        assert vdev.adc_running is True

    async def test_clear_zero_resets_the_offset(self, mm01_client, mm01_manager):
        vdev = mm01_manager._handles[0]
        vdev.current_microstrain = lambda: 500.0
        await mm01_client.post("/mm01/devices/0/zero")

        resp = await mm01_client.delete("/mm01/devices/0/zero")
        assert resp.status_code == 200
        assert (await mm01_client.get("/mm01/devices/0")).json()["zero_offset"] == 0.0

    async def test_zero_unknown_device_returns_404(self, mm01_client):
        resp = await mm01_client.post("/mm01/devices/99/zero")
        assert resp.status_code == 404


# ── Streaming ─────────────────────────────────────────────────────────────────

class TestStreaming:
    async def test_readings_appear_once_started(self, test_app):
        """End-to-end: reader threads decode the stream into microstrain."""
        manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
        manager.scan()
        vdev = manager._handles[0]
        vdev.current_microstrain = lambda: 250.0
        test_app.state.mm01_manager = manager
        manager.start(loop=asyncio.get_running_loop())
        try:
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                value = None
                for _ in range(50):
                    await asyncio.sleep(0.05)
                    body = (await ac.get("/mm01/devices/0/reading")).json()
                    if body["microstrain"] is not None:
                        value = body["microstrain"]
                        break
                assert value == pytest.approx(250.0, rel=1e-3)
        finally:
            await manager.stop()

    async def test_publishes_frames_to_subscribers(self, test_app):
        """The /mm01/ws endpoint is a thin wrapper over this queue fan-out."""
        manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
        manager.scan()
        manager._handles[0].current_microstrain = lambda: 100.0
        test_app.state.mm01_manager = manager
        manager.start(loop=asyncio.get_running_loop())
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        manager.subscribe(queue)
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=5.0)
            assert 0 in frame.readings
            assert frame.readings[0] == pytest.approx(100.0, rel=1e-3)
        finally:
            manager.unsubscribe(queue)
            await manager.stop()

    async def test_unsubscribe_stops_delivery(self, test_app):
        manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
        manager.scan()
        test_app.state.mm01_manager = manager
        manager.start(loop=asyncio.get_running_loop())
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        manager.subscribe(queue)
        try:
            await asyncio.wait_for(queue.get(), timeout=5.0)
            manager.unsubscribe(queue)
            while not queue.empty():
                queue.get_nowait()
            await asyncio.sleep(0.2)
            assert queue.empty()
        finally:
            await manager.stop()


# ── Reader/command lock contention ────────────────────────────────────────────

class TestCommandLatency:
    """A streaming reader must not block commands for longer than one read."""

    async def test_command_waits_no_longer_than_one_read_timeout(self, test_app):
        import threading
        import time

        from app.mm01_bridge.manager import MM01DeviceManager

        manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
        manager.scan()

        # A silent device: every read blocks for the full timeout, which is the
        # worst case for a command waiting on the same per-device lock.
        vdev = manager._handles[0]
        timeout_s = MM01DeviceManager._READ_TIMEOUT_MS / 1000.0

        def _blocking_read(timeout_ms=0):
            time.sleep(timeout_ms / 1000.0)
            return b""

        vdev.read_report = _blocking_read

        manager.start(loop=asyncio.get_running_loop())
        try:
            # Let the reader get into its blocking read before timing.
            await asyncio.sleep(timeout_s / 2)
            lock = manager._device_locks[0]
            start = time.monotonic()
            acquired = lock.acquire(timeout=5.0)
            waited = time.monotonic() - start
            if acquired:
                lock.release()
            assert acquired, "command could not acquire the device lock"
            # Allow one full read plus scheduling slack, but nothing like the
            # multi-second stall a long timeout produced.
            assert waited < timeout_s * 3
        finally:
            await manager.stop()

    async def test_reader_timeout_is_small_enough_for_interactive_use(self):
        from app.mm01_bridge.manager import MM01DeviceManager

        assert MM01DeviceManager._READ_TIMEOUT_MS <= 100
