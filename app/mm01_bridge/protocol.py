"""
MM01 device-level operations.

Each function here mirrors the identically-named method in MM01InterfaceLib
(V2.0.2) so the wire behaviour matches the vendor software exactly.  The
scaling constants and command sequences were recovered from that assembly.
"""

from __future__ import annotations

import logging
import time

from app.mm01_bridge.constants import (
    ADC_SET_SAMPLE_RATE,
    ADC_START,
    ADC_STOP,
    ADC_VOLTS_PER_COUNT,
    BRIDGE_FULL,
    BRIDGE_QUARTER,
    DCP_WIPER_VALUE,
    GAIN,
    GAIN_FACTOR,
    I2C_ADDR_ADC,
    I2C_ADDR_DCP,
    MV_PER_V_SCALE,
    SAMPLE_RATE_CODE,
    USB_CASE_ADC,
    USB_CASE_GPIO,
    USB_CASE_I2C_WRITE_DCP,
    USB_CASE_READ_VERSION,
)
from app.mm01_bridge.transport import MM01Device, MM01TransportError, parse_adc_counts, parse_version

log = logging.getLogger(__name__)


class MM01ProtocolError(Exception):
    """Raised when an MM01 returns an unusable response."""


# ── Device info ───────────────────────────────────────────────────────────────

def read_version(dev: MM01Device) -> str:
    """Return the device firmware version as ``major.minor``."""
    dev.flush_input()
    raw = dev.command_read(USB_CASE_READ_VERSION)
    return parse_version(raw)


# ── ADC control ───────────────────────────────────────────────────────────────

def start_adc(dev: MM01Device) -> None:
    """Start free-running conversions. The device streams until stopped."""
    dev.write_command(USB_CASE_ADC, ADC_START, 0, 0)


def stop_adc(dev: MM01Device) -> None:
    """Stop conversions."""
    dev.write_command(USB_CASE_ADC, ADC_STOP, 0, 0)


def set_sample_rate(dev: MM01Device) -> None:
    """Set the sample rate. The MM01 supports one rate: 80 samples/second."""
    dev.write_command(USB_CASE_ADC, ADC_SET_SAMPLE_RATE, 0, SAMPLE_RATE_CODE)


# ── Bridge excitation and gain ────────────────────────────────────────────────

def enable_qb_excitation(dev: MM01Device) -> None:
    """Energise the internal quarter-bridge completion."""
    dev.write_command(USB_CASE_GPIO, 1, 1, 1)


def disable_qb_excitation(dev: MM01Device) -> None:
    """De-energise the internal quarter-bridge completion."""
    dev.write_command(USB_CASE_GPIO, 1, 0, 0)


def set_bridge_excitation(dev: MM01Device, bridge: int) -> None:
    """Enable internal completion for quarter and half bridges, disable for full."""
    if bridge == BRIDGE_FULL:
        disable_qb_excitation(dev)
    else:
        enable_qb_excitation(dev)


def set_gain(dev: MM01Device, bridge: int = BRIDGE_QUARTER) -> None:
    """Program the gain digital potentiometer.

    The vendor library takes a bridge argument but writes the same wiper value
    for every bridge type — its comment notes gains of 100 (QB) and 50 (FB)
    were only used "originally".  The argument is kept for signature parity.
    """
    dev.write_command(USB_CASE_I2C_WRITE_DCP, I2C_ADDR_DCP, 1, DCP_WIPER_VALUE)
    dev.write_command(USB_CASE_I2C_WRITE_DCP, I2C_ADDR_DCP, 0, DCP_WIPER_VALUE)


def init_device(dev: MM01Device, bridge: int = BRIDGE_QUARTER) -> None:
    """Run the vendor power-on initialisation sequence.

    Reproduces MM01Interface.Initdevice: repeated ADC configuration writes
    interleaved with input-buffer flushes, then sample rate, excitation and
    gain.  The repetition and the flushes are deliberate — the ADC does not
    reliably latch its configuration on the first write.
    """
    for _ in range(2):
        dev.write_command(USB_CASE_I2C_WRITE_DCP, I2C_ADDR_ADC, 2, 0xC0)
    dev.flush_input()
    dev.flush_input()
    dev.write_command(USB_CASE_I2C_WRITE_DCP, I2C_ADDR_ADC, 2, 0xC0)
    dev.flush_input()
    for _ in range(2):
        dev.write_command(USB_CASE_I2C_WRITE_DCP, I2C_ADDR_ADC, 2, 0xC0)
    dev.write_command(USB_CASE_I2C_WRITE_DCP, I2C_ADDR_ADC, 0, 2)

    set_sample_rate(dev)
    set_bridge_excitation(dev, bridge)
    set_gain(dev, bridge)


# ── Readings ──────────────────────────────────────────────────────────────────

def read_adc_counts(dev: MM01Device, timeout_ms: int = 1000) -> int:
    """Read one conversion. The ADC must already be running.

    Raises:
        MM01ProtocolError if no report arrives before the timeout.
    """
    raw = dev.read_report(timeout_ms=timeout_ms)
    if not raw:
        raise MM01ProtocolError("Timed out waiting for an MM01 conversion")
    return parse_adc_counts(raw)


def avg_adc(dev: MM01Device, count: int = 1, timeout_ms: int = 1000) -> int:
    """Start the ADC, average ``count`` conversions, and stop it.

    Mirrors MM01Interface.AvgAdc, including the 100 ms settle before the ADC is
    stopped.  Returns the mean in ADC counts, truncated to an int.

    Raises:
        ValueError if count < 1.
        MM01ProtocolError if no conversion arrives.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    dev.flush_input()
    set_sample_rate(dev)
    start_adc(dev)
    try:
        total = 0
        got = 0
        for _ in range(count):
            raw = dev.read_report(timeout_ms=timeout_ms)
            if not raw:
                continue
            total += parse_adc_counts(raw)
            got += 1
        time.sleep(0.1)
    finally:
        try:
            stop_adc(dev)
        except MM01TransportError:
            log.warning("Failed to stop the MM01 ADC after averaging")

    if got == 0:
        raise MM01ProtocolError("Timed out waiting for an MM01 conversion")
    return int(total / got)


# ── Scaling ───────────────────────────────────────────────────────────────────

def adc_to_volts(counts: int, zero_offset: float = 0.0) -> float:
    """Convert raw ADC counts to volts.

    ``2.048 V`` reference over the 24-bit signed range, with the sign inverted
    to match the instrument's polarity, less the stored zero (balance) offset.
    """
    return counts * ADC_VOLTS_PER_COUNT * -1.0 - zero_offset


def volts_to_microstrain(volts: float, gage_factor: float) -> float:
    """Convert volts to microstrain for the configured gage factor.

    Raises:
        ValueError if gage_factor is zero.
    """
    if gage_factor == 0:
        raise ValueError("gage_factor must be non-zero")
    return volts * GAIN * GAIN_FACTOR * (2.0 / gage_factor)


def volts_to_mv_per_v(volts: float) -> float:
    """Convert volts to mV/V."""
    return volts * MV_PER_V_SCALE
