"""
Unit tests for the MM01 (StudentDAQ / MultiDAQ) bridge.

Covers report framing, ADC decoding, the scaling chain, and the command
dispatch of VirtualMM01Device. No USB hardware and no `hid` package required.
"""

from __future__ import annotations

import pytest

from app.mm01_bridge import protocol as proto
from app.mm01_bridge.constants import (
    ADC_START,
    ADC_STOP,
    BRIDGE_FULL,
    BRIDGE_HALF,
    BRIDGE_QUARTER,
    DCP_WIPER_VALUE,
    I2C_ADDR_ADC,
    I2C_ADDR_DCP,
    INPUT_REPORT_SIZE,
    OUTPUT_REPORT_SIZE,
    SAMPLE_RATE_CODE,
    USB_CASE_ADC,
    USB_CASE_GPIO,
    USB_CASE_READ_VERSION,
)
from app.mm01_bridge.transport import (
    MM01TransportError,
    build_output_report,
    parse_adc_counts,
    parse_version,
)
from app.mm01_bridge.virtual_device import (
    VirtualMM01Device,
    encode_counts,
    microstrain_to_counts,
)


# ── Output report framing ─────────────────────────────────────────────────────

class TestBuildOutputReport:
    """WriteToControl lays out [report_id][usb_case][p1][p2][p3], zero-padded."""

    def test_layout(self):
        rep = build_output_report(USB_CASE_ADC, 1, 2, 3)
        assert rep[0] == 0x00          # report ID is always zero
        assert rep[1] == USB_CASE_ADC
        assert rep[2] == 1
        assert rep[3] == 2
        assert rep[4] == 3

    def test_padded_to_report_size(self):
        rep = build_output_report(USB_CASE_ADC, 1)
        assert len(rep) == OUTPUT_REPORT_SIZE
        assert rep[5:] == bytes(OUTPUT_REPORT_SIZE - 5)

    def test_params_default_to_zero(self):
        rep = build_output_report(USB_CASE_READ_VERSION)
        assert rep[2:5] == b"\x00\x00\x00"

    def test_values_masked_to_a_byte(self):
        rep = build_output_report(USB_CASE_ADC, 0x1FF, 0x100, -1)
        assert rep[2] == 0xFF
        assert rep[3] == 0x00
        assert rep[4] == 0xFF

    def test_report_size_below_five_rejected(self):
        with pytest.raises(ValueError):
            build_output_report(USB_CASE_ADC, report_size=4)


# ── Input report decoding ─────────────────────────────────────────────────────

class TestParseAdcCounts:
    """Counts are 24-bit signed big-endian in bytes 0-2 (Linux hidraw offsets)."""

    def test_zero(self):
        assert parse_adc_counts(bytes([0x00, 0x00, 0x00, 0, 0])) == 0

    def test_positive(self):
        assert parse_adc_counts(bytes([0x01, 0x02, 0x03, 0, 0])) == 0x010203

    def test_largest_positive(self):
        assert parse_adc_counts(bytes([0x7F, 0xFF, 0xFF, 0, 0])) == 0x7FFFFF

    def test_negative_one(self):
        assert parse_adc_counts(bytes([0xFF, 0xFF, 0xFF, 0, 0])) == -1

    def test_most_negative(self):
        assert parse_adc_counts(bytes([0x80, 0x00, 0x00, 0, 0])) == -0x800000

    def test_sign_bit_boundary(self):
        """0x7FFFFF is positive; one count more wraps to the negative range."""
        assert parse_adc_counts(bytes([0x7F, 0xFF, 0xFF])) > 0
        assert parse_adc_counts(bytes([0x80, 0x00, 0x00])) < 0

    def test_short_report_raises(self):
        with pytest.raises(MM01TransportError):
            parse_adc_counts(b"\x01\x02")


def test_parse_version_reads_bytes_three_and_four():
    """Windows formats buffer[4]/[5]; Linux hidraw strips the report ID byte."""
    assert parse_version(bytes([0x00, 0x00, 0x00, 0x02, 0x00])) == "2.0"


def test_parse_version_short_report_returns_empty():
    assert parse_version(b"\x00\x00") == ""


# ── Scaling chain ─────────────────────────────────────────────────────────────

class TestScaling:
    def test_adc_to_volts_inverts_sign(self):
        """The instrument's polarity is inverted relative to raw counts."""
        assert proto.adc_to_volts(1000) < 0
        assert proto.adc_to_volts(-1000) > 0

    def test_adc_to_volts_zero_counts(self):
        assert proto.adc_to_volts(0) == 0.0

    def test_adc_to_volts_full_scale_is_2048_mv(self):
        """2.048 V reference across the 24-bit signed range."""
        assert proto.adc_to_volts(-(2 ** 23)) == pytest.approx(2.048, rel=1e-4)

    def test_adc_to_volts_subtracts_zero_offset(self):
        assert proto.adc_to_volts(0, zero_offset=0.5) == pytest.approx(-0.5)

    def test_volts_to_microstrain_scales_by_gain_and_gage_factor(self):
        # volts * GAIN(100) * GAIN_FACTOR(80) * (2 / GF)
        assert proto.volts_to_microstrain(1.0, 2.0) == pytest.approx(8000.0)

    def test_volts_to_microstrain_inverse_in_gage_factor(self):
        """Halving the gage factor doubles the reported strain."""
        assert proto.volts_to_microstrain(1.0, 1.0) == pytest.approx(
            2 * proto.volts_to_microstrain(1.0, 2.0)
        )

    def test_volts_to_microstrain_zero_gage_factor_raises(self):
        with pytest.raises(ValueError):
            proto.volts_to_microstrain(1.0, 0.0)

    def test_volts_to_mv_per_v(self):
        assert proto.volts_to_mv_per_v(1.5) == pytest.approx(6.0)


def test_scaling_round_trip():
    """Synthesised microstrain survives encode → decode → scale."""
    for target in (-1500.0, -1.0, 0.0, 250.0, 1000.0):
        counts = microstrain_to_counts(target, gage_factor=2.0)
        raw = encode_counts(counts)
        decoded = parse_adc_counts(raw)
        volts = proto.adc_to_volts(decoded)
        assert proto.volts_to_microstrain(volts, 2.0) == pytest.approx(target, abs=0.01)


def test_encode_counts_report_length():
    assert len(encode_counts(1234)) == INPUT_REPORT_SIZE


# ── Virtual device command dispatch ───────────────────────────────────────────

@pytest.fixture
def vdev():
    dev = VirtualMM01Device("virtual://mm01/0", serial_number="VMM01-0001")
    dev.open()
    yield dev
    dev.close()


class TestVirtualDeviceDispatch:
    def test_read_version(self, vdev):
        assert proto.read_version(vdev) == "2.0"

    def test_start_and_stop_adc(self, vdev):
        proto.start_adc(vdev)
        assert vdev.adc_running is True
        proto.stop_adc(vdev)
        assert vdev.adc_running is False

    def test_set_sample_rate(self, vdev):
        proto.set_sample_rate(vdev)
        assert vdev.sample_rate_set is True

    def test_no_reading_while_adc_stopped(self, vdev):
        """A stopped MM01 sends nothing — the caller must see a timeout."""
        assert vdev.read_report(timeout_ms=10) == b""

    def test_reading_available_once_started(self, vdev):
        proto.start_adc(vdev)
        assert len(vdev.read_report(timeout_ms=100)) == INPUT_REPORT_SIZE

    def test_read_adc_counts_raises_when_stopped(self, vdev):
        with pytest.raises(proto.MM01ProtocolError):
            proto.read_adc_counts(vdev, timeout_ms=10)

    def test_quarter_bridge_enables_excitation(self, vdev):
        proto.set_bridge_excitation(vdev, BRIDGE_QUARTER)
        assert vdev.qb_excitation is True

    def test_half_bridge_enables_excitation(self, vdev):
        proto.set_bridge_excitation(vdev, BRIDGE_HALF)
        assert vdev.qb_excitation is True

    def test_full_bridge_disables_excitation(self, vdev):
        proto.set_bridge_excitation(vdev, BRIDGE_FULL)
        assert vdev.qb_excitation is False

    def test_set_gain_writes_both_dcp_wipers(self, vdev):
        proto.set_gain(vdev, BRIDGE_QUARTER)
        assert vdev.dcp_writes == [
            (I2C_ADDR_DCP, 1, DCP_WIPER_VALUE),
            (I2C_ADDR_DCP, 0, DCP_WIPER_VALUE),
        ]

    def test_set_gain_identical_for_every_bridge(self, vdev):
        """The vendor library writes the same wiper value regardless of bridge."""
        proto.set_gain(vdev, BRIDGE_QUARTER)
        quarter = list(vdev.dcp_writes)
        vdev.dcp_writes.clear()
        proto.set_gain(vdev, BRIDGE_FULL)
        assert vdev.dcp_writes == quarter

    def test_init_device_configures_adc_and_gain(self, vdev):
        proto.init_device(vdev, BRIDGE_QUARTER)
        # Five 0xC0 configuration writes, then the register-0 latch.
        assert vdev.adc_config_writes == [
            (I2C_ADDR_ADC, 2, 0xC0),
            (I2C_ADDR_ADC, 2, 0xC0),
            (I2C_ADDR_ADC, 2, 0xC0),
            (I2C_ADDR_ADC, 2, 0xC0),
            (I2C_ADDR_ADC, 2, 0xC0),
            (I2C_ADDR_ADC, 0, 2),
        ]
        assert vdev.sample_rate_set is True
        assert vdev.dcp_writes  # gain programmed

    def test_init_device_leaves_adc_stopped(self, vdev):
        proto.init_device(vdev, BRIDGE_QUARTER)
        assert vdev.adc_running is False


class TestAvgAdc:
    def test_averages_requested_sample_count(self, vdev):
        counts = proto.avg_adc(vdev, count=4)
        assert isinstance(counts, int)

    def test_stops_the_adc_when_done(self, vdev):
        proto.avg_adc(vdev, count=2)
        assert vdev.adc_running is False

    def test_rejects_zero_count(self, vdev):
        with pytest.raises(ValueError):
            proto.avg_adc(vdev, count=0)

    def test_constant_signal_averages_to_that_value(self, vdev):
        """With noise disabled and a flat signal, the mean is the signal."""
        vdev._amplitude = 0.0
        vdev._noise = 0.0
        expected = microstrain_to_counts(0.0, vdev.gage_factor)
        assert proto.avg_adc(vdev, count=3) == expected
