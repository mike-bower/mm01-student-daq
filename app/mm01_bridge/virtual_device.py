"""
Virtual MM01 device — pure-Python simulation of the MM01 HID protocol.

Implements the same interface as ``MM01Device`` so it can be dropped into
MM01DeviceManager with no real hardware and no ``hid`` package installed.

The simulated gage produces a sinusoidal strain signal with a little Gaussian
noise, encoded back through the real scaling constants so that a caller
decoding it with ``protocol.adc_to_volts`` / ``volts_to_microstrain`` recovers
the intended microstrain.
"""

from __future__ import annotations

import math
import random
import threading
import time

from app.mm01_bridge.constants import (
    ADC_SET_SAMPLE_RATE,
    ADC_START,
    ADC_STOP,
    ADC_VOLTS_PER_COUNT,
    BRIDGE_QUARTER,
    DEFAULT_GAGE_FACTOR,
    GAIN,
    GAIN_FACTOR,
    INPUT_REPORT_SIZE,
    OUTPUT_REPORT_SIZE,
    SAMPLE_RATE_HZ,
    USB_CASE_ADC,
    USB_CASE_GPIO,
    USB_CASE_I2C_WRITE_DCP,
    USB_CASE_READ_VERSION,
)


def microstrain_to_counts(microstrain: float, gage_factor: float = DEFAULT_GAGE_FACTOR) -> int:
    """Inverse of adc_to_volts + volts_to_microstrain, for signal synthesis."""
    volts = microstrain * gage_factor / (GAIN * GAIN_FACTOR * 2.0)
    return int(round(volts / (ADC_VOLTS_PER_COUNT * -1.0)))


def encode_counts(counts: int, size: int = INPUT_REPORT_SIZE) -> bytes:
    """Pack signed 24-bit counts into an input report (big-endian, bytes 0-2)."""
    raw = counts & 0x00FFFFFF
    buf = bytearray(size)
    buf[0] = (raw >> 16) & 0xFF
    buf[1] = (raw >> 8) & 0xFF
    buf[2] = raw & 0xFF
    return bytes(buf)


class VirtualMM01Device:
    """Simulated MM01 with the same call surface as MM01Device."""

    def __init__(
        self,
        path: str,
        serial_number: str = "VMM01-0001",
        firmware_version: str = "2.0",
        amplitude_ue: float = 1000.0,
        period_s: float = 10.0,
        noise_ue: float = 5.0,
    ) -> None:
        self._path = path
        self.serial_number = serial_number
        self.firmware_version = firmware_version

        self._amplitude = amplitude_ue
        self._period = period_s
        self._noise = noise_ue
        self._t0 = time.monotonic()

        self._lock = threading.Lock()
        self._open = False

        # Device state, mutated by the command handlers below.
        self.adc_running = False
        self.sample_rate_set = False
        self.bridge = BRIDGE_QUARTER
        self.qb_excitation = True
        self.gage_factor = DEFAULT_GAGE_FACTOR
        self.dcp_writes: list[tuple[int, int, int]] = []
        self.adc_config_writes: list[tuple[int, int, int]] = []

    # -- lifecycle --

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False
        self.adc_running = False

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def path(self) -> str:
        return self._path

    @property
    def output_report_size(self) -> int:
        return OUTPUT_REPORT_SIZE

    @property
    def input_report_size(self) -> int:
        return INPUT_REPORT_SIZE

    # -- simulated signal --

    def current_microstrain(self) -> float:
        """The strain the simulated gage is reading right now."""
        t = time.monotonic() - self._t0
        value = self._amplitude * math.sin(2.0 * math.pi * t / self._period)
        if self._noise:
            value += random.gauss(0.0, self._noise)
        return value

    # -- I/O --

    def flush_input(self) -> None:
        return None

    def write_command(self, usb_case: int, p1: int = 0, p2: int = 0, p3: int = 0) -> None:
        if not self._open:
            from app.mm01_bridge.transport import MM01TransportError
            raise MM01TransportError("Device is not open")
        with self._lock:
            self._dispatch(usb_case, p1, p2, p3)

    def read_report(self, timeout_ms: int = 1000) -> bytes:
        if not self._open:
            from app.mm01_bridge.transport import MM01TransportError
            raise MM01TransportError("Device is not open")
        # A stopped ADC sends nothing — the caller sees a timeout, as on hardware.
        if not self.adc_running:
            return b""
        # Pace reads at the device's fixed conversion rate.
        time.sleep(min(1.0 / SAMPLE_RATE_HZ, max(timeout_ms, 0) / 1000.0))
        return encode_counts(microstrain_to_counts(self.current_microstrain(), self.gage_factor))

    def command_read(
        self,
        usb_case: int,
        p1: int = 0,
        p2: int = 0,
        p3: int = 0,
        timeout_ms: int = 1000,
    ) -> bytes:
        if not self._open:
            from app.mm01_bridge.transport import MM01TransportError
            raise MM01TransportError("Device is not open")
        with self._lock:
            reply = self._dispatch(usb_case, p1, p2, p3)
        if reply is not None:
            return reply
        return self.read_report(timeout_ms=timeout_ms)

    # -- command dispatch --

    def _dispatch(self, usb_case: int, p1: int, p2: int, p3: int) -> bytes | None:
        """Apply one command. Returns a reply report, or None if it has no reply."""
        if usb_case == USB_CASE_READ_VERSION:
            buf = bytearray(INPUT_REPORT_SIZE)
            major, _, minor = self.firmware_version.partition(".")
            buf[3] = int(major) & 0xFF if major.isdigit() else 0
            buf[4] = int(minor) & 0xFF if minor.isdigit() else 0
            return bytes(buf)

        if usb_case == USB_CASE_ADC:
            if p1 == ADC_START:
                self.adc_running = True
            elif p1 == ADC_STOP:
                self.adc_running = False
            elif p1 == ADC_SET_SAMPLE_RATE:
                self.sample_rate_set = True
            return None

        if usb_case == USB_CASE_GPIO:
            # (1, 1, 1) enables quarter-bridge completion, (1, 0, 0) disables it.
            if p1 == 1:
                self.qb_excitation = bool(p2)
            return None

        if usb_case == USB_CASE_I2C_WRITE_DCP:
            from app.mm01_bridge.constants import I2C_ADDR_ADC, I2C_ADDR_DCP
            if p1 == I2C_ADDR_DCP:
                self.dcp_writes.append((p1, p2, p3))
            elif p1 == I2C_ADDR_ADC:
                self.adc_config_writes.append((p1, p2, p3))
            return None

        return None
