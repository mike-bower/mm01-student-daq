"""
Tests for recording the MM01 stream to CSV (app/recorder.py, /recording).

No USB hardware and no `hid` package: the readings come from
SimMM01DeviceManager, so these exercise the same recorder path a real MM01 uses.
Every test writes into pytest's tmp_path, never into the project's recordings/.
"""

from __future__ import annotations

import asyncio
import csv
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.mm01_bridge.sim_manager import SimMM01DeviceManager
from app.recorder import Recorder, RecordingError

pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_app_state():
    """The test app is a module-level singleton — never leak state between tests."""
    import main as app_module

    def _drop():
        for attr in ("mm01_manager", "recorder"):
            if hasattr(app_module.app.state, attr):
                delattr(app_module.app.state, attr)

    _drop()
    yield
    _drop()


@pytest.fixture
def recorder(test_app, tmp_path):
    """A recorder writing into tmp_path, attached to the app."""
    rec = Recorder(directory=str(tmp_path / "recordings"),
                   default_interval_ms=20, max_seconds=60.0)
    test_app.state.recorder = rec
    yield rec
    rec.shutdown()


@pytest_asyncio.fixture
async def streaming_manager(test_app):
    """One simulated device, streaming a constant 250 µε."""
    manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
    manager.scan()
    manager._handles[0].current_microstrain = lambda: 250.0
    test_app.state.mm01_manager = manager
    manager.start(loop=asyncio.get_running_loop())
    yield manager
    await manager.stop()


@pytest_asyncio.fixture
async def rec_client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac


async def _wait_for_a_reading(manager, timeout_s: float = 5.0) -> None:
    """Block until the reader thread has decoded at least one conversion."""
    import math

    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if all(math.isfinite(d.last_microstrain) for d in manager.devices):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("simulated device never produced a reading")


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


# ── Availability ──────────────────────────────────────────────────────────────

class TestAvailability:
    async def test_status_is_503_when_recording_is_unavailable(self, rec_client):
        """No recorder on app.state — the recordings directory was unusable."""
        resp = await rec_client.get("/recording/status")
        assert resp.status_code == 503
        assert "Recording is not available" in resp.json()["detail"]

    async def test_start_is_503_without_a_device_manager(self, recorder, rec_client):
        resp = await rec_client.post("/recording/start", json={})
        assert resp.status_code == 503
        assert "nothing to record" in resp.json()["detail"]

    async def test_status_is_idle_before_anything_is_recorded(self, recorder, rec_client):
        body = (await rec_client.get("/recording/status")).json()
        assert body["recording"] is False
        assert body["session"] is None
        assert body["default_interval_ms"] == 20
        assert body["max_seconds"] == 60.0

    async def test_saved_recordings_are_listed_without_hardware(
        self, recorder, streaming_manager, rec_client, test_app
    ):
        """A student can fetch yesterday's data with the MM01 unplugged."""
        sid = (await rec_client.post("/recording/start", json={})).json()["session_id"]
        await asyncio.sleep(0.1)
        await rec_client.post("/recording/stop")

        delattr(test_app.state, "mm01_manager")
        body = (await rec_client.get("/recording/sessions")).json()
        assert [s["session_id"] for s in body["sessions"]] == [sid]


# ── Start and stop ────────────────────────────────────────────────────────────

class TestStartStop:
    async def test_start_then_stop_writes_a_csv(
        self, recorder, streaming_manager, rec_client
    ):
        await _wait_for_a_reading(streaming_manager)
        started = (await rec_client.post(
            "/recording/start", json={"name": "Beam test #3", "note": "200 g mass"}
        )).json()

        assert started["stopped_at"] is None
        assert started["rows"] == 0
        assert started["sample_interval_ms"] == 20
        assert started["sample_rate_hz"] == 50.0
        # The name becomes a filename a student can recognise later.
        assert started["session_id"].endswith("-beam-test-3")
        assert started["csv_file"] == started["session_id"] + ".csv"

        await asyncio.sleep(0.3)
        stopped = (await rec_client.post("/recording/stop")).json()

        assert stopped["session_id"] == started["session_id"]
        assert stopped["stop_reason"] == "user"
        assert stopped["stopped_at"] is not None
        assert stopped["rows"] >= 2
        assert stopped["note"] == "200 g mass"

        rows = _read_csv(recorder.directory / stopped["csv_file"])
        assert rows[0] == ["t_s", "iso_time", "microstrain_0", "mv_per_v_0", "counts_0"]
        assert len(rows) == stopped["rows"] + 1
        assert float(rows[1][2]) == pytest.approx(250.0, rel=1e-3)

    async def test_recorded_times_increase_and_track_the_interval(
        self, recorder, streaming_manager, rec_client
    ):
        await _wait_for_a_reading(streaming_manager)
        await rec_client.post("/recording/start", json={"sample_interval_ms": 50})
        await asyncio.sleep(0.5)
        stopped = (await rec_client.post("/recording/stop")).json()

        rows = _read_csv(recorder.directory / stopped["csv_file"])[1:]
        times = [float(r[0]) for r in rows]
        assert times == sorted(times)
        assert len(set(times)) == len(times)
        # Ticks are anchored to the start time, so 50 ms apart, not 50 ms + work.
        assert times[0] == pytest.approx(0.05, abs=0.03)
        for earlier, later in zip(times, times[1:]):
            assert later - earlier == pytest.approx(0.05, abs=0.03)

    async def test_status_reports_progress_while_recording(
        self, recorder, streaming_manager, rec_client
    ):
        await _wait_for_a_reading(streaming_manager)
        await rec_client.post("/recording/start", json={})
        await asyncio.sleep(0.2)

        body = (await rec_client.get("/recording/status")).json()
        assert body["recording"] is True
        assert body["session"]["rows"] >= 2
        assert body["session"]["duration_s"] > 0
        # Flushed about once a second, so early on the file may still be header-only.
        assert body["session"]["size_bytes"] > 0

        await rec_client.post("/recording/stop")
        assert (await rec_client.get("/recording/status")).json()["recording"] is False

    async def test_a_second_start_is_refused(
        self, recorder, streaming_manager, rec_client
    ):
        await rec_client.post("/recording/start", json={})
        resp = await rec_client.post("/recording/start", json={})
        assert resp.status_code == 409
        assert "Already recording" in resp.json()["detail"]

    async def test_stop_without_a_recording_is_refused(
        self, recorder, streaming_manager, rec_client
    ):
        resp = await rec_client.post("/recording/stop")
        assert resp.status_code == 409
        assert "Not recording" in resp.json()["detail"]

    async def test_recording_survives_a_stop_start_cycle(
        self, recorder, streaming_manager, rec_client
    ):
        first = (await rec_client.post("/recording/start", json={})).json()
        await asyncio.sleep(0.1)
        await rec_client.post("/recording/stop")
        second = (await rec_client.post("/recording/start", json={})).json()
        await asyncio.sleep(0.1)
        await rec_client.post("/recording/stop")

        assert first["session_id"] != second["session_id"]
        assert (await rec_client.get("/recording/sessions")).json()["total_sessions"] == 2

    async def test_an_interval_outside_the_allowed_range_is_rejected(
        self, recorder, streaming_manager, rec_client
    ):
        resp = await rec_client.post("/recording/start", json={"sample_interval_ms": 1})
        assert resp.status_code == 422


# ── What gets recorded ────────────────────────────────────────────────────────

class TestRecordedData:
    @pytest_asyncio.fixture
    async def two_devices(self, test_app):
        manager = SimMM01DeviceManager(device_count=2, poll_interval_ms=50)
        manager.scan()
        test_app.state.mm01_manager = manager
        manager.start(loop=asyncio.get_running_loop())
        yield manager
        await manager.stop()

    async def test_every_device_gets_its_own_columns(
        self, recorder, two_devices, rec_client
    ):
        started = (await rec_client.post("/recording/start", json={})).json()
        assert started["columns"] == [
            "t_s", "iso_time",
            "microstrain_0", "mv_per_v_0", "counts_0",
            "microstrain_1", "mv_per_v_1", "counts_1",
        ]
        assert [d["device_index"] for d in started["devices"]] == [0, 1]

    async def test_only_the_requested_devices_are_recorded(
        self, recorder, two_devices, rec_client
    ):
        started = (await rec_client.post(
            "/recording/start", json={"device_indexes": [1]}
        )).json()
        assert started["columns"] == ["t_s", "iso_time",
                                      "microstrain_1", "mv_per_v_1", "counts_1"]
        assert [d["device_index"] for d in started["devices"]] == [1]

    async def test_an_unknown_device_is_refused(self, recorder, two_devices, rec_client):
        resp = await rec_client.post("/recording/start", json={"device_indexes": [7]})
        assert resp.status_code == 409
        assert "No such device: 7" in resp.json()["detail"]

    async def test_the_scaling_that_produced_the_data_is_stored_with_it(
        self, recorder, streaming_manager, rec_client
    ):
        """Microstrain is meaningless later without the gage factor that made it."""
        await rec_client.post("/mm01/devices/0/gage-factor", json={"gage_factor": 2.13})
        await rec_client.post("/mm01/devices/0/bridge", json={"bridge": 2})

        started = (await rec_client.post("/recording/start", json={})).json()
        device = started["devices"][0]
        assert device["gage_factor"] == pytest.approx(2.13)
        assert device["bridge_name"] == "FB"
        assert device["serial_number"] == "VMM01-0001"
        assert device["simulated"] is True

    async def test_an_unavailable_reading_is_a_blank_cell(
        self, recorder, test_app, rec_client
    ):
        """NaN must not reach the CSV — a blank is what Excel reads as no data."""
        manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
        manager.scan()                      # scanned but never started: no readings
        test_app.state.mm01_manager = manager
        try:
            await rec_client.post("/recording/start", json={})
            await asyncio.sleep(0.1)
            stopped = (await rec_client.post("/recording/stop")).json()
        finally:
            await manager.stop()

        rows = _read_csv(recorder.directory / stopped["csv_file"])[1:]
        assert rows, "expected at least one row"
        assert all(row[2] == "" and row[3] == "" for row in rows)

    async def test_metadata_is_written_alongside_the_csv(
        self, recorder, streaming_manager, rec_client
    ):
        started = (await rec_client.post("/recording/start", json={"name": "sidecar"})).json()
        await asyncio.sleep(0.1)
        await rec_client.post("/recording/stop")

        meta_path = recorder.directory / f"{started['session_id']}.json"
        meta = json.loads(meta_path.read_text())
        assert meta["session_id"] == started["session_id"]
        assert meta["stop_reason"] == "user"
        assert meta["rows"] >= 1


# ── Saved sessions ────────────────────────────────────────────────────────────

class TestSessions:
    @pytest_asyncio.fixture
    async def two_recordings(self, recorder, streaming_manager, rec_client):
        ids = []
        for name in ("first", "second"):
            ids.append((await rec_client.post(
                "/recording/start", json={"name": name}
            )).json()["session_id"])
            await asyncio.sleep(0.1)
            await rec_client.post("/recording/stop")
        return ids

    async def test_sessions_are_listed_newest_first(self, two_recordings, rec_client):
        body = (await rec_client.get("/recording/sessions")).json()
        assert body["total_sessions"] == 2
        assert [s["session_id"] for s in body["sessions"]] == list(reversed(two_recordings))

    async def test_one_session_can_be_fetched(self, two_recordings, rec_client):
        body = (await rec_client.get(f"/recording/sessions/{two_recordings[0]}")).json()
        assert body["name"] == "first"
        assert body["size_bytes"] > 0

    async def test_download_returns_the_csv(self, two_recordings, rec_client):
        resp = await rec_client.get(f"/recording/sessions/{two_recordings[0]}/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert two_recordings[0] in resp.headers["content-disposition"]
        assert resp.text.splitlines()[0].startswith("t_s,iso_time")

    async def test_delete_removes_the_csv_and_the_metadata(
        self, recorder, two_recordings, rec_client
    ):
        sid = two_recordings[0]
        resp = await rec_client.delete(f"/recording/sessions/{sid}")
        assert resp.status_code == 200
        assert not (recorder.directory / f"{sid}.csv").exists()
        assert not (recorder.directory / f"{sid}.json").exists()
        assert (await rec_client.get(f"/recording/sessions/{sid}")).status_code == 404
        assert (await rec_client.get("/recording/sessions")).json()["total_sessions"] == 1

    async def test_the_running_recording_cannot_be_deleted(
        self, recorder, streaming_manager, rec_client
    ):
        sid = (await rec_client.post("/recording/start", json={})).json()["session_id"]
        resp = await rec_client.delete(f"/recording/sessions/{sid}")
        assert resp.status_code == 409
        assert "still running" in resp.json()["detail"]

    async def test_the_running_recording_is_not_in_the_saved_list(
        self, recorder, two_recordings, streaming_manager, rec_client
    ):
        """Its stored row count is zero until it stops — status() has the live one."""
        sid = (await rec_client.post("/recording/start", json={})).json()["session_id"]
        listed = (await rec_client.get("/recording/sessions")).json()
        assert [s["session_id"] for s in listed["sessions"]] == list(reversed(two_recordings))
        assert (await rec_client.get("/recording/status")).json()["session"]["session_id"] == sid

        await asyncio.sleep(0.1)
        await rec_client.post("/recording/stop")
        listed = (await rec_client.get("/recording/sessions")).json()
        assert listed["sessions"][0]["session_id"] == sid
        assert listed["total_sessions"] == 3

    async def test_an_unknown_session_is_404(self, recorder, rec_client):
        for path in ("/recording/sessions/20200101-000000-nope",
                     "/recording/sessions/nope",
                     "/recording/sessions/nope/download"):
            assert (await rec_client.get(path)).status_code == 404

    async def test_a_session_id_cannot_escape_the_recordings_directory(self, recorder):
        """Ids are generated, but they come back as a path parameter."""
        for hostile in ("../secret", "..", "/etc/passwd", "20200101-000000-../x", ""):
            assert recorder.get_session(hostile) is None
            assert recorder.csv_path(hostile) is None


# ── Recorder behaviour with no HTTP layer ─────────────────────────────────────

class TestRecorderDirect:
    @pytest_asyncio.fixture
    async def manager(self):
        manager = SimMM01DeviceManager(device_count=1, poll_interval_ms=50)
        manager.scan()
        manager.start(loop=asyncio.get_running_loop())
        yield manager
        await manager.stop()

    async def test_a_forgotten_recording_stops_itself(self, tmp_path, manager):
        """max_seconds is what keeps a recording left running off the SD card."""
        rec = Recorder(directory=str(tmp_path), default_interval_ms=10, max_seconds=0.2)
        session = rec.start(manager)
        for _ in range(50):
            await asyncio.sleep(0.05)
            if not rec.is_recording:
                break
        assert not rec.is_recording

        saved = rec.get_session(session["session_id"])
        assert saved["stop_reason"] == "max_duration"
        assert saved["duration_s"] >= 0.2
        # It stopped by itself, so there is nothing left to stop.
        with pytest.raises(RecordingError):
            rec.stop()

    async def test_shutdown_closes_an_open_recording(self, tmp_path, manager):
        rec = Recorder(directory=str(tmp_path), default_interval_ms=20)
        session = rec.start(manager)
        await asyncio.sleep(0.1)
        rec.shutdown()

        assert not rec.is_recording
        saved = rec.get_session(session["session_id"])
        assert saved["stop_reason"] == "shutdown"
        assert saved["rows"] >= 1
        rec.shutdown()      # safe to call again

    async def test_a_session_left_running_is_reported_as_interrupted(
        self, tmp_path, manager
    ):
        """A power cut leaves metadata that says "recording". Say so, don't hide it."""
        rec = Recorder(directory=str(tmp_path), default_interval_ms=20)
        session = rec.start(manager)
        assert rec.status()["session"]["stop_reason"] is None

        # A new Recorder over the same directory is what a restarted app sees.
        reopened = Recorder(directory=str(tmp_path))
        assert reopened.get_session(session["session_id"])["stop_reason"] == "interrupted"
        rec.shutdown()

    async def test_a_directory_that_does_not_exist_yet_is_created(self, tmp_path):
        rec = Recorder(directory=str(tmp_path / "a" / "b"))
        assert rec.directory.is_dir()
        assert rec.list_sessions() == []

    async def test_an_out_of_range_interval_is_clamped(self, tmp_path, manager):
        """The API rejects these; the recorder is also used directly."""
        rec = Recorder(directory=str(tmp_path), default_interval_ms=1)
        assert rec.default_interval_ms == 10
        session = rec.start(manager, sample_interval_ms=10**9)
        assert session["sample_interval_ms"] == 60_000
        rec.shutdown()

    async def test_recording_with_no_devices_is_refused(self, tmp_path, test_app):
        empty = SimMM01DeviceManager(device_count=0)
        empty.scan()
        rec = Recorder(directory=str(tmp_path))
        with pytest.raises(RecordingError, match="No MM01 devices"):
            rec.start(empty)
