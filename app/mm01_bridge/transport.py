"""
Low-level HID transport for the MM01 (StudentDAQ / MultiDAQ).

Mirrors ``app.hid_bridge.transport`` in shape, but speaks the MM01 report
format described in ``app.mm01_bridge.constants``: a five-byte command written
to the control pipe, and a five-byte input report carrying 24-bit signed ADC
counts.

All I/O is synchronous.  Callers in an asyncio context must wrap these calls
with run_in_executor.
"""

from __future__ import annotations

import struct
import threading

try:
    import hid  # python-hid (hidapi binding)
except ImportError:
    hid = None  # type: ignore[assignment]

from app.mm01_bridge.constants import (
    DEVICE_TYPES,
    INPUT_REPORT_SIZE,
    OUTPUT_REPORT_SIZE,
    PRODUCT_TYPE,
    REPORT_ID_DEFAULT,
)


class MM01TransportError(Exception):
    """Raised on an I/O failure or an unusable response from an MM01."""


# Reads are only meaningful once the ADC is running; the device replies to
# ReadVersion within a millisecond or two.
DEFAULT_READ_TIMEOUT_MS = 1000


# ── Report builders / parsers ─────────────────────────────────────────────────

def build_output_report(
    usb_case: int,
    p1: int = 0,
    p2: int = 0,
    p3: int = 0,
    report_size: int = OUTPUT_REPORT_SIZE,
) -> bytes:
    """Build an MM01 output report.

    Layout is ``[report_id][usb_case][p1][p2][p3]`` zero-padded to
    ``report_size`` — exactly what MM01InterfaceLib's WriteToControl sends.

    Args:
        usb_case:    USBCase selector (see constants).
        p1, p2, p3:  Command parameters; meaning depends on usb_case.
        report_size: Total output report length in bytes.

    Returns:
        Bytes object of exactly ``report_size`` bytes.
    """
    if report_size < 5:
        raise ValueError(f"report_size must be at least 5, got {report_size}")
    buf = bytearray(report_size)
    buf[0] = REPORT_ID_DEFAULT
    buf[1] = usb_case & 0xFF
    buf[2] = p1 & 0xFF
    buf[3] = p2 & 0xFF
    buf[4] = p3 & 0xFF
    return bytes(buf)


def parse_adc_counts(raw: bytes) -> int:
    """Decode 24-bit signed ADC counts from an MM01 input report.

    On Linux hidraw the report-ID byte is not prepended, so the counts occupy
    bytes 0-2 big-endian.  Bit 23 is the sign bit.

    Raises:
        MM01TransportError if the report is too short to contain a reading.
    """
    if len(raw) < 3:
        raise MM01TransportError(f"Input report too short: {len(raw)} bytes")
    counts = (raw[0] << 16) | (raw[1] << 8) | raw[2]
    if raw[0] & 0x80:
        counts -= 0x01000000
    return counts


def parse_version(raw: bytes) -> str:
    """Decode a ReadVersion reply into a ``major.minor`` string.

    The vendor library formats bytes 4 and 5 of a Windows report; on Linux the
    report-ID strip puts those at indices 3 and 4.
    """
    if len(raw) < 5:
        return ""
    return f"{raw[3]}.{raw[4]}"


# ── Device enumeration ────────────────────────────────────────────────────────

def enumerate_devices() -> list[dict]:
    """Return a list of dicts describing all connected MM01 devices.

    Each dict contains: path, vendor_id, product_id, product_type,
    manufacturer, product, serial_number.
    """
    if hid is None:
        raise ImportError(
            "The 'hid' package is not installed. "
            "Run: pip install hid>=1.0.5"
        )
    found: list[dict] = []
    for info in hid.enumerate():
        key = (info["vendor_id"], info["product_id"])
        if key not in DEVICE_TYPES:
            continue
        found.append({
            "path":          info["path"],
            "vendor_id":     info["vendor_id"],
            "product_id":    info["product_id"],
            "product_type":  DEVICE_TYPES[key],
            "manufacturer":  info.get("manufacturer_string", ""),
            "product":       info.get("product_string", ""),
            "serial_number": info.get("serial_number", ""),
        })

    # Stable ordering across rescans — the path changes after a replug but the
    # serial number is fixed. Matches MM01Interface.GetOrderedDeviceIndex,
    # which orders the device list by serial number.
    def _sort_key(d: dict) -> str:
        sn = d.get("serial_number", "")
        if sn:
            return sn
        p = d["path"]
        return p.decode() if isinstance(p, bytes) else p

    found.sort(key=_sort_key)
    return found


# ── Per-device handle wrapper ─────────────────────────────────────────────────

class MM01Device:
    """Thread-safe wrapper around a single open MM01 handle."""

    def __init__(
        self,
        path: str,
        output_report_size: int = OUTPUT_REPORT_SIZE,
        input_report_size: int = INPUT_REPORT_SIZE,
    ) -> None:
        self._path = path
        self._out_size = output_report_size
        self._in_size = input_report_size
        self._lock = threading.Lock()
        self._dev: "hid.Device | None" = None

    # -- lifecycle --

    def open(self) -> None:
        if hid is None:
            raise ImportError(
                "The 'hid' package is not installed. "
                "Run: pip install hid>=1.0.5  "
                "(Linux also needs: sudo apt install libhidapi-hidraw0)"
            )
        path = self._path.encode() if isinstance(self._path, str) else self._path
        dev = hid.Device(path=path)
        dev.nonblocking = 0
        self._dev = dev

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    @property
    def path(self) -> str:
        return self._path

    @property
    def output_report_size(self) -> int:
        return self._out_size

    @property
    def input_report_size(self) -> int:
        return self._in_size

    # -- I/O --

    def flush_input(self) -> None:
        """Drain stale packets from the kernel HID input buffer.

        The MM01 streams continuously while its ADC is running, so a handle
        that has just been opened — or one whose ADC was left running — can
        have several conversions queued.  Reading those as a command reply
        would return a measurement instead.
        """
        if self._dev is None:
            return
        with self._lock:
            self._dev.nonblocking = 1
            try:
                for _ in range(64):
                    if not self._dev.read(self._in_size):
                        break
            except Exception:
                pass
            finally:
                self._dev.nonblocking = 0

    def write_command(self, usb_case: int, p1: int = 0, p2: int = 0, p3: int = 0) -> None:
        """Send a command report. Does not read a reply."""
        if self._dev is None:
            raise MM01TransportError("Device is not open")
        report = build_output_report(usb_case, p1, p2, p3, self._out_size)
        with self._lock:
            written = self._dev.write(report)
            if written < 0:
                raise MM01TransportError("HID write failed")

    def read_report(self, timeout_ms: int = DEFAULT_READ_TIMEOUT_MS) -> bytes:
        """Read one input report. Returns b"" on timeout."""
        if self._dev is None:
            raise MM01TransportError("Device is not open")
        with self._lock:
            return bytes(self._dev.read(self._in_size, timeout_ms))

    def command_read(
        self,
        usb_case: int,
        p1: int = 0,
        p2: int = 0,
        p3: int = 0,
        timeout_ms: int = DEFAULT_READ_TIMEOUT_MS,
    ) -> bytes:
        """Send a command and read the single report it produces.

        The write and the read are performed under one lock acquisition so a
        concurrent caller cannot claim this command's reply.
        """
        if self._dev is None:
            raise MM01TransportError("Device is not open")
        report = build_output_report(usb_case, p1, p2, p3, self._out_size)
        with self._lock:
            written = self._dev.write(report)
            if written < 0:
                raise MM01TransportError("HID write failed")
            raw = bytes(self._dev.read(self._in_size, timeout_ms))
        if not raw:
            raise MM01TransportError("No response from MM01 device")
        return raw


def open_device(
    path: str | bytes,
    output_report_size: int = OUTPUT_REPORT_SIZE,
    input_report_size: int = INPUT_REPORT_SIZE,
) -> MM01Device:
    """Open the MM01 at ``path`` and return the handle."""
    dev = MM01Device(
        path if isinstance(path, str) else path.decode(),
        output_report_size,
        input_report_size,
    )
    dev.open()
    return dev
