"""
Device manager for Micro-Measurements MM01 (StudentDAQ / MultiDAQ) instruments.

The MM01 differs from the P3/D4 in two ways that shape this module:

  * It is **single-channel** — one device is one measurement channel, so the
    flat "global channel index" of ``hid_bridge`` is just the device index.
  * It **streams**.  Once the ADC is started the device pushes conversions at a
    fixed 80 samples/second and answers no polling command.  Reading one report
    per 200 ms tick would therefore return progressively staler data, so each
    device gets a reader thread that blocks on the stream (mirroring
    MM01Interface.DeviceReadThread) and a single publisher thread emits frames
    to WebSocket subscribers at the configured interval.

Usage (from FastAPI lifespan):
    manager = MM01DeviceManager(poll_interval_ms=200)
    manager.scan()
    manager.start(loop=asyncio.get_running_loop())
    …
    await manager.stop()
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.mm01_bridge import protocol as proto
from app.mm01_bridge.constants import (
    BRIDGE_NAMES,
    BRIDGE_QUARTER,
    DEFAULT_GAGE_FACTOR,
    PRODUCT_TYPE,
    ZERO_SAMPLE_COUNT,
)
from app.mm01_bridge.transport import (
    MM01Device,
    MM01TransportError,
    enumerate_devices,
    open_device,
    parse_adc_counts,
)

log = logging.getLogger(__name__)


# ── Device lock ───────────────────────────────────────────────────────────────

class _DeviceLock:
    """Per-device lock that gives commands priority over the streaming reader.

    The reader takes this lock for every single read and re-acquires it
    immediately afterwards.  CPython locks are not FIFO, so a plain Lock lets
    the reader win the race essentially every time and a command thread can be
    starved indefinitely — on hardware this showed up as bridge changes
    stalling for over a second, and with a silent device it never completes.

    Commands register their intent before blocking; the reader defers while any
    command is waiting, so a command waits at most one in-flight read.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters = 0
        self._waiters_mutex = threading.Lock()

    def acquire(self, timeout: float = -1) -> bool:
        """Acquire with command priority. Returns False if the timeout expires."""
        with self._waiters_mutex:
            self._waiters += 1
        try:
            return self._lock.acquire(timeout=timeout)
        finally:
            with self._waiters_mutex:
                self._waiters -= 1

    def release(self) -> None:
        self._lock.release()

    @contextmanager
    def for_command(self):
        """Hold the device for a command sequence, ahead of the reader."""
        self.acquire()
        try:
            yield
        finally:
            self.release()

    @contextmanager
    def for_reader(self, defer_s: float = 0.002):
        """Hold the device for one read, yielding to any waiting command."""
        while True:
            with self._waiters_mutex:
                if self._waiters == 0:
                    break
            time.sleep(defer_s)
        self._lock.acquire()
        try:
            yield
        finally:
            self._lock.release()


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MM01DeviceInfo:
    """One connected MM01 — also its single measurement channel."""
    device_index: int
    path: str
    vendor_id: int
    product_id: int
    product_type: str = PRODUCT_TYPE
    serial_number: str = ""
    firmware_version: str = ""
    in_error: bool = False

    # Configuration
    active: bool = True
    bridge: int = BRIDGE_QUARTER
    gage_factor: float = DEFAULT_GAGE_FACTOR
    zero_offset: float = 0.0      # volts, subtracted by adc_to_volts
    label: str = ""

    # Most recent reading
    last_counts: int = 0
    last_microstrain: float = math.nan
    last_mv_per_v: float = math.nan

    @property
    def bridge_name(self) -> str:
        return BRIDGE_NAMES.get(self.bridge, "")


@dataclass
class MM01ScanFrame:
    """One publication tick — the latest reading from every device."""
    timestamp: float                       # time.monotonic()
    readings: dict[int, float]             # device_index → microstrain (NaN if unavailable)
    mv_per_v: dict[int, float] = field(default_factory=dict)
    error_devices: list[int] = field(default_factory=list)


# ── Manager ───────────────────────────────────────────────────────────────────

class MM01DeviceManager:
    """Manages all connected MM01 devices and their streaming readers."""

    # Blocking read timeout for a reader thread. A reader holds the per-device
    # lock for the duration of one read, so this value is also the worst-case
    # extra latency a command (set bridge, zero, …) waits to acquire that lock.
    # At 80 S/s a conversion is due every 12.5 ms, so 100 ms tolerates 8x jitter
    # while keeping UI commands responsive — a longer timeout was measured
    # stalling bridge changes for over a second whenever a conversion was missed.
    _READ_TIMEOUT_MS = 100

    def __init__(self, poll_interval_ms: int = 200) -> None:
        self._publish_interval_s = poll_interval_ms / 1000.0
        self._devices: list[MM01DeviceInfo] = []
        self._handles: dict[int, MM01Device] = {}

        # One lock per device. The reader thread takes it for each single read;
        # commands that need the stream to themselves take it for the whole
        # sequence. Reads return in ~12 ms so command latency stays low.
        self._device_locks: dict[int, _DeviceLock] = {}

        # Held for the duration of scan() so readers cannot touch handles that
        # are being closed and reopened.
        self._scan_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._reader_threads: dict[int, threading.Thread] = {}
        self._publisher_thread: threading.Thread | None = None

        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def scan(self) -> list[MM01DeviceInfo]:
        """Enumerate, open and initialise every connected MM01."""
        with self._scan_lock:
            self._stop_readers()
            self._close_all_handles()
            self._devices.clear()
            self._device_locks.clear()

            for dev_idx, raw in enumerate(enumerate_devices()):
                path = raw["path"]
                info = MM01DeviceInfo(
                    device_index=dev_idx,
                    path=path.decode() if isinstance(path, bytes) else path,
                    vendor_id=raw["vendor_id"],
                    product_id=raw["product_id"],
                    product_type=raw["product_type"],
                    serial_number=raw.get("serial_number", ""),
                )
                self._device_locks[dev_idx] = _DeviceLock()
                try:
                    handle = open_device(path)
                    handle.flush_input()
                    self._handles[dev_idx] = handle
                    try:
                        info.firmware_version = proto.read_version(handle)
                    except Exception as exc:
                        log.warning("MM01 %s: version query failed: %s", info.path, exc)
                    proto.init_device(handle, info.bridge)
                except Exception as exc:
                    log.warning("Could not open MM01 %s: %s", info.path, exc)
                    info.in_error = True
                self._devices.append(info)

            log.info("MM01 scan found %d device(s)", len(self._devices))
            return list(self._devices)

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the ADC on every device and begin streaming."""
        self._loop = loop
        self._stop_event.clear()

        for info in self._devices:
            handle = self._handles.get(info.device_index)
            if handle is None or not handle.is_open or not info.active:
                continue
            try:
                proto.start_adc(handle)
            except Exception as exc:
                log.warning("MM01 %d: could not start ADC: %s", info.device_index, exc)
                info.in_error = True
                continue
            self._start_reader(info.device_index)

        self._publisher_thread = threading.Thread(
            target=self._publish_loop, name="mm01-publish", daemon=True
        )
        self._publisher_thread.start()
        log.info(
            "MM01 streaming started (%d device(s), publish every %.0f ms)",
            len(self._reader_threads), self._publish_interval_s * 1000,
        )

    async def stop(self) -> None:
        """Stop all readers, halt the ADCs and close every handle."""
        self._stop_event.set()
        if self._publisher_thread is not None:
            self._publisher_thread.join(timeout=5.0)
            self._publisher_thread = None
        self._stop_readers()
        for dev_idx, handle in self._handles.items():
            try:
                proto.stop_adc(handle)
            except Exception:
                log.debug("MM01 %d: ADC stop failed during shutdown", dev_idx)
        self._close_all_handles()
        log.info("MM01 manager stopped")

    # ── Threads ───────────────────────────────────────────────────────────────

    def _start_reader(self, device_index: int) -> None:
        thread = threading.Thread(
            target=self._reader_loop,
            args=(device_index,),
            name=f"mm01-read-{device_index}",
            daemon=True,
        )
        self._reader_threads[device_index] = thread
        thread.start()

    def _stop_readers(self) -> None:
        """Signal readers to exit and wait for them.

        Leaves _stop_event set only if it already was — scan() reuses this
        while the manager is otherwise running.
        """
        was_set = self._stop_event.is_set()
        self._stop_event.set()
        for thread in self._reader_threads.values():
            thread.join(timeout=2.0)
        self._reader_threads.clear()
        if not was_set:
            self._stop_event.clear()

    def _reader_loop(self, device_index: int) -> None:
        """Continuously consume the conversion stream from one device."""
        info = self.get_device(device_index)
        while not self._stop_event.is_set():
            handle = self._handles.get(device_index)
            if info is None or handle is None or not handle.is_open:
                break
            lock = self._device_locks.get(device_index)
            if lock is None:
                break
            try:
                with lock.for_reader():
                    raw = handle.read_report(timeout_ms=self._READ_TIMEOUT_MS)
                if not raw:
                    continue
                counts = parse_adc_counts(raw)
                volts = proto.adc_to_volts(counts, info.zero_offset)
                info.last_counts = counts
                info.last_microstrain = proto.volts_to_microstrain(volts, info.gage_factor)
                info.last_mv_per_v = proto.volts_to_mv_per_v(volts)
                info.in_error = False
            except MM01TransportError as exc:
                log.warning("MM01 %d: read failed: %s", device_index, exc)
                info.in_error = True
                info.last_microstrain = math.nan
                time.sleep(0.1)
            except Exception:
                log.exception("MM01 %d: unexpected reader error", device_index)
                info.in_error = True
                time.sleep(0.1)

    def _publish_loop(self) -> None:
        """Emit a MM01ScanFrame to every subscriber at the publish interval."""
        while not self._stop_event.is_set():
            time.sleep(self._publish_interval_s)
            if self._stop_event.is_set():
                break
            frame = self.snapshot()
            if not self._subscribers or self._loop is None:
                continue
            for queue in list(self._subscribers):
                try:
                    self._loop.call_soon_threadsafe(queue.put_nowait, frame)
                except (asyncio.QueueFull, RuntimeError):
                    pass

    # ── Properties and lookup ─────────────────────────────────────────────────

    @property
    def devices(self) -> list[MM01DeviceInfo]:
        return list(self._devices)

    def get_device(self, device_index: int) -> MM01DeviceInfo | None:
        for d in self._devices:
            if d.device_index == device_index:
                return d
        return None

    def snapshot(self) -> MM01ScanFrame:
        """Current reading from every device, without touching the hardware."""
        return MM01ScanFrame(
            timestamp=time.monotonic(),
            readings={d.device_index: d.last_microstrain for d in self._devices},
            mv_per_v={d.device_index: d.last_mv_per_v for d in self._devices},
            error_devices=[d.device_index for d in self._devices if d.in_error],
        )

    # ── WebSocket subscription ────────────────────────────────────────────────

    def subscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.add(queue)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # ── Commands (called from the router via run_in_executor) ────────────────

    def _require(self, device_index: int) -> tuple[MM01DeviceInfo, MM01Device, _DeviceLock]:
        info = self.get_device(device_index)
        if info is None:
            raise KeyError(f"MM01 device {device_index} not found")
        handle = self._handles.get(device_index)
        if handle is None or not handle.is_open:
            raise MM01TransportError(f"MM01 device {device_index} is not open")
        return info, handle, self._device_locks[device_index]

    def cmd_set_bridge(self, device_index: int, bridge: int) -> None:
        info, handle, lock = self._require(device_index)
        with lock.for_command():
            proto.set_bridge_excitation(handle, bridge)
            proto.set_gain(handle, bridge)
        info.bridge = bridge

    def cmd_set_gage_factor(self, device_index: int, gage_factor: float) -> None:
        info, _handle, _lock = self._require(device_index)
        # Gage factor is applied host-side in volts_to_microstrain, exactly as
        # MM01Interface.SetGageFactor does — nothing is written to the device.
        info.gage_factor = gage_factor

    def cmd_zero(self, device_index: int) -> float:
        """Balance the device: store the current reading as the zero offset.

        Returns the new zero offset in volts.
        """
        info, handle, lock = self._require(device_index)
        with lock.for_command():
            info.zero_offset = 0.0
            try:
                counts = proto.avg_adc(handle, ZERO_SAMPLE_COUNT)
            finally:
                # avg_adc stops the ADC; restart it so the reader keeps streaming.
                try:
                    proto.start_adc(handle)
                except Exception:
                    log.warning("MM01 %d: could not restart ADC after zero", device_index)
            info.zero_offset = proto.adc_to_volts(counts, 0.0)
        return info.zero_offset

    def cmd_clear_zero(self, device_index: int) -> None:
        info, _handle, _lock = self._require(device_index)
        info.zero_offset = 0.0

    def cmd_set_label(self, device_index: int, label: str) -> None:
        info, _handle, _lock = self._require(device_index)
        info.label = label

    def read_single(self, device_index: int) -> MM01DeviceInfo:
        """Return the most recent streamed reading for one device."""
        info = self.get_device(device_index)
        if info is None:
            raise KeyError(f"MM01 device {device_index} not found")
        return info

    # ── Internal ──────────────────────────────────────────────────────────────

    def _close_all_handles(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
