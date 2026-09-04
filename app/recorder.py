"""
Recording — capture the live MM01 stream to a CSV file on the Pi.

The MM01 converts at a fixed 80 samples/second and the browser is only updated
about five times a second, so anything a student wants to keep has to be written
down on the server.  ``Recorder`` runs one writer thread that samples the most
recent conversion of each selected device at a fixed interval and appends a row
to a CSV file under ``recordings/``.

    recorder = Recorder(directory="recordings")
    session  = recorder.start(manager, name="cantilever", sample_interval_ms=50)
    ...
    session  = recorder.stop()          # or it auto-stops at max_seconds

What the interval does, and does not, mean: the recorder reads
``MM01DeviceInfo.last_*``, which the reader thread overwrites for every
conversion.  Sampling at 12 ms therefore captures approximately every
conversion, and sampling slower deliberately keeps one conversion in that
window and discards the rest — it is not an average.  The raw ADC ``counts``
column is written alongside the scaled values so a repeated sample (a tick that
landed between two conversions) is visible in the data rather than hidden.

Two files are written per session:

    recordings/<id>.csv     the data — plain CSV, no preamble, opens in Excel
    recordings/<id>.json    provenance — gage factor, bridge, zero offset, …

The metadata file is written when recording starts and rewritten when it stops,
so a session interrupted by a power cut is still listed (as "interrupted")
rather than lost.

This module is deliberately outside ``app/mm01_bridge`` — that driver is a
frozen copy from the parent project and local edits there are overwritten
(see tools/sync_bridge.sh).  The recorder only reads the manager's public
surface: ``devices``, ``get_device()``.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# A conversion is due every 12.5 ms, so nothing is gained below 10 ms; the
# upper bound keeps a mistyped interval from looking like a hung recording.
MIN_INTERVAL_MS = 10
MAX_INTERVAL_MS = 60_000

# Session ids are generated, never supplied by a client — but they arrive back
# as a path parameter, so every lookup is matched against this before it is
# joined to the recordings directory.
SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-[a-z0-9-]{1,40})?$")

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


class RecordingError(RuntimeError):
    """A recording could not be started, stopped or deleted."""


# ── Session records ───────────────────────────────────────────────────────────

@dataclass
class RecordedDevice:
    """How one device was configured for the whole of a session.

    Gage factor, bridge and zero offset are host-side scaling, so they are what
    turn the stored counts into microstrain — a recording without them cannot be
    checked afterwards.
    """
    device_index: int
    serial_number: str
    firmware_version: str
    bridge_name: str
    gage_factor: float
    zero_offset: float
    label: str
    simulated: bool


@dataclass
class RecordingSession:
    session_id: str
    name: str
    note: str
    started_at: str                     # ISO-8601 local wall clock
    sample_interval_ms: int
    csv_file: str
    columns: list
    devices: list
    rows: int = 0
    duration_s: float = 0.0
    stopped_at: Optional[str] = None
    stop_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    """"Beam test #3" → "beam-test-3", for a filename a student can recognise."""
    return _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-")[:40]


def _fmt(value: Optional[float], places: int) -> str:
    """Format a reading, or return "" for NaN/inf so the CSV cell is blank.

    A blank is what Excel and pandas both read as "no data"; the string "nan"
    would quietly turn a numeric column into text.
    """
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return ""
    return f"{value:.{places}f}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


# ── Recorder ──────────────────────────────────────────────────────────────────

class Recorder:
    """Writes the live stream to CSV. One recording at a time, one thread."""

    def __init__(
        self,
        directory: str = "recordings",
        default_interval_ms: int = 50,
        max_seconds: float = 3600.0,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.default_interval_ms = self._clamp_interval(default_interval_ms)
        self.max_seconds = max_seconds

        # Guards _session, _thread, _handle and _finished together. Never held
        # across the thread join in stop(), or the writer thread's own call to
        # _finish() would deadlock against it.
        self._lock = threading.Lock()
        self._session: Optional[RecordingSession] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._handle = None
        self._writer = None
        self._finished = True
        self._reason = "user"

        self._manager = None
        self._device_indexes: list = []
        self._t0 = 0.0

    # ── State ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _clamp_interval(interval_ms: int) -> int:
        return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, int(interval_ms)))

    @property
    def is_recording(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def status(self) -> dict:
        """The in-progress session, or None. Cheap enough to poll every second."""
        with self._lock:
            if not self.is_recording or self._session is None:
                return {"recording": False, "session": None}
            return {"recording": True, "session": self._decorate(self._session.to_dict())}

    # ── Start / stop ──────────────────────────────────────────────────────────

    def start(
        self,
        manager,
        name: str = "",
        note: str = "",
        sample_interval_ms: Optional[int] = None,
        device_indexes: Optional[list] = None,
    ) -> dict:
        """Begin recording. Returns the new session; raises RecordingError."""
        with self._lock:
            if self.is_recording:
                raise RecordingError(
                    "Already recording — stop the current recording first."
                )

            devices = list(manager.devices)
            if device_indexes is not None:
                wanted = list(dict.fromkeys(int(i) for i in device_indexes))
                missing = [i for i in wanted if all(d.device_index != i for d in devices)]
                if missing:
                    raise RecordingError(
                        "No such device: " + ", ".join(str(i) for i in missing)
                    )
                devices = [d for d in devices if d.device_index in wanted]
            if not devices:
                raise RecordingError("No MM01 devices to record.")

            interval = self._clamp_interval(
                self.default_interval_ms if sample_interval_ms is None else sample_interval_ms
            )
            self._device_indexes = [d.device_index for d in devices]

            columns = ["t_s", "iso_time"]
            for idx in self._device_indexes:
                columns += [f"microstrain_{idx}", f"mv_per_v_{idx}", f"counts_{idx}"]

            session_id = self._new_session_id(name)
            csv_path = self.directory / f"{session_id}.csv"

            session = RecordingSession(
                session_id=session_id,
                name=name.strip(),
                note=note.strip(),
                started_at=_now_iso(),
                sample_interval_ms=interval,
                csv_file=csv_path.name,
                columns=columns,
                devices=[
                    RecordedDevice(
                        device_index=d.device_index,
                        serial_number=d.serial_number,
                        firmware_version=d.firmware_version,
                        bridge_name=d.bridge_name,
                        gage_factor=d.gage_factor,
                        zero_offset=d.zero_offset,
                        label=d.label,
                        simulated=str(d.path).startswith("virtual://"),
                    )
                    for d in devices
                ],
            )

            try:
                self._handle = csv_path.open("w", newline="", encoding="utf-8")
            except OSError as exc:
                raise RecordingError(f"Could not open {csv_path.name} for writing: {exc}")
            self._writer = csv.writer(self._handle)
            self._writer.writerow(columns)
            self._handle.flush()

            self._session = session
            self._manager = manager
            self._finished = False
            self._reason = "user"
            self._stop_event.clear()
            self._write_metadata(session)

            self._t0 = time.monotonic()
            self._thread = threading.Thread(
                target=self._write_loop, name="mm01-record", daemon=True
            )
            self._thread.start()

            log.info(
                "Recording %s started — device(s) %s every %d ms",
                session_id,
                ", ".join(str(i) for i in self._device_indexes),
                interval,
            )
            return self._decorate(session.to_dict())

    def stop(self, reason: str = "user") -> dict:
        """Stop the current recording and return the finished session."""
        with self._lock:
            thread = self._thread
            if thread is None or self._session is None or (self._finished and not thread.is_alive()):
                raise RecordingError("Not recording.")
            self._reason = reason

        self._stop_event.set()
        thread.join(timeout=5.0)

        with self._lock:
            # Normally the writer thread has already finished on its way out;
            # this covers a thread that overran the join.
            self._finish(reason)
            self._thread = None
            session = self._session.to_dict() if self._session else {}
        return self._decorate(session)

    def shutdown(self) -> None:
        """Stop a recording in progress, if any. Safe to call unconditionally."""
        if self.is_recording:
            try:
                self.stop(reason="shutdown")
            except RecordingError:
                pass

    # ── Writer thread ─────────────────────────────────────────────────────────

    def _write_loop(self) -> None:
        interval_s = self._session.sample_interval_ms / 1000.0 if self._session else 0.05
        reason = "user"
        tick = 0
        last_flush = self._t0
        try:
            while True:
                # Anchor every tick to t0 rather than sleeping a fixed interval,
                # so the sample times do not drift over a long recording.
                tick += 1
                due = self._t0 + tick * interval_s
                if self._stop_event.wait(max(0.0, due - time.monotonic())):
                    break

                elapsed = time.monotonic() - self._t0
                self._writer.writerow(self._sample_row(elapsed))
                if self._session is not None:
                    self._session.rows += 1
                    self._session.duration_s = round(elapsed, 3)

                # Flushed about once a second so a recording can be downloaded
                # or inspected while it is still running.
                now = time.monotonic()
                if now - last_flush >= 1.0:
                    self._handle.flush()
                    last_flush = now

                if self.max_seconds and elapsed >= self.max_seconds:
                    reason = "max_duration"
                    log.info(
                        "Recording %s hit the %.0f s limit — stopping",
                        self._session.session_id if self._session else "?",
                        self.max_seconds,
                    )
                    break
        except Exception:
            reason = "error"
            log.exception("Recording writer failed")
        finally:
            with self._lock:
                self._finish(self._reason if self._stop_event.is_set() else reason)

    def _sample_row(self, elapsed: float) -> list:
        """One row: the most recent conversion of each recorded device."""
        row = [f"{elapsed:.3f}", _now_iso()]
        for idx in self._device_indexes:
            info = self._manager.get_device(idx) if self._manager else None
            if info is None or info.in_error:
                # A device that dropped off the bus leaves blanks, not a stale
                # reading repeated for the rest of the recording.
                row += ["", "", ""]
                continue
            row += [
                _fmt(info.last_microstrain, 2),
                _fmt(info.last_mv_per_v, 6),
                str(info.last_counts),
            ]
        return row

    # ── Finishing ─────────────────────────────────────────────────────────────

    def _finish(self, reason: str) -> None:
        """Close the file and rewrite the metadata. Idempotent; holds _lock."""
        if self._finished or self._session is None:
            return
        self._finished = True
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except OSError:
                log.warning("Recording %s: closing the CSV failed", self._session.session_id)
        self._handle = None
        self._writer = None

        self._session.stopped_at = _now_iso()
        self._session.stop_reason = reason
        self._session.duration_s = round(time.monotonic() - self._t0, 3)
        self._write_metadata(self._session)
        log.info(
            "Recording %s stopped (%s) — %d row(s) in %.1f s",
            self._session.session_id, reason, self._session.rows, self._session.duration_s,
        )

    def _write_metadata(self, session: RecordingSession) -> None:
        path = self.directory / f"{session.session_id}.json"
        try:
            with path.open("w", encoding="utf-8") as fh:
                json.dump(session.to_dict(), fh, indent=2)
        except OSError as exc:
            log.warning("Recording %s: could not write metadata: %s", session.session_id, exc)

    def _new_session_id(self, name: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = _slug(name)
        base = f"{stamp}-{slug}" if slug else stamp
        candidate, n = base, 1
        while (self.directory / f"{candidate}.csv").exists():
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    # ── Saved sessions ────────────────────────────────────────────────────────

    def list_sessions(self) -> list:
        """Every finished session, newest first.

        The recording in progress is left out: its metadata was written when it
        started, so its row count and duration are still zero. Ask status() for
        that one — it reads the live counters.
        """
        live = self._session.session_id if (self.is_recording and self._session) else None
        sessions = []
        for path in self.directory.glob("*.json"):
            data = self._load_metadata(path)
            if data is not None and data.get("session_id") != live:
                sessions.append(self._decorate(data))
        sessions.sort(key=lambda s: s.get("started_at", ""), reverse=True)
        return sessions

    def get_session(self, session_id: str) -> Optional[dict]:
        if not SESSION_ID_RE.match(session_id or ""):
            return None
        data = self._load_metadata(self.directory / f"{session_id}.json")
        return None if data is None else self._decorate(data)

    def csv_path(self, session_id: str) -> Optional[Path]:
        if self.get_session(session_id) is None:
            return None
        path = self.directory / f"{session_id}.csv"
        return path if path.is_file() else None

    def delete_session(self, session_id: str) -> None:
        if self.get_session(session_id) is None:
            raise RecordingError(f"No recording {session_id}")
        with self._lock:
            if self.is_recording and self._session and self._session.session_id == session_id:
                raise RecordingError("That recording is still running — stop it first.")
        for suffix in (".csv", ".json"):
            path = self.directory / f"{session_id}{suffix}"
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise RecordingError(f"Could not delete {path.name}: {exc}")
        log.info("Recording %s deleted", session_id)

    def _load_metadata(self, path: Path) -> Optional[dict]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("session_id") else None

    def _decorate(self, data: dict) -> dict:
        """Add the fields the API reports but does not store: rate, size, state."""
        out: dict = dict(data)
        interval = out.get("sample_interval_ms") or self.default_interval_ms
        out["sample_rate_hz"] = round(1000.0 / interval, 2)

        csv_file = self.directory / str(out.get("csv_file") or "")
        try:
            out["size_bytes"] = csv_file.stat().st_size
        except OSError:
            out["size_bytes"] = 0

        # A session whose metadata says "running" but which is not the live one
        # was interrupted — the app was stopped or the Pi lost power.
        if out.get("stopped_at") is None:
            live = self._session.session_id if (self.is_recording and self._session) else None
            if out.get("session_id") != live:
                out["stop_reason"] = "interrupted"
        return out


def build_recorder(settings: Any) -> Optional["Recorder"]:
    """Create a Recorder from settings, or None if its directory is unusable."""
    try:
        return Recorder(
            directory=settings.mm01_record_dir,
            default_interval_ms=settings.mm01_record_interval_ms,
            max_seconds=settings.mm01_record_max_seconds,
        )
    except OSError as exc:
        log.warning(
            "Recording disabled — cannot use %s: %s", settings.mm01_record_dir, exc
        )
        return None
