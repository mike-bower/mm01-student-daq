"""
Protocol constants for the Micro-Measurements MM01 (StudentDAQ / MultiDAQ).

Recovered from MM01InterfaceLib.dll (V2.0.2), shipped in the vendor's MultiDaq
distribution.  The MM01 is **not** a P3/D4 variant: it shares the VPG vendor ID
but speaks an entirely different protocol over Silicon Labs' SLABHIDDevice
layer, and it has exactly one measurement channel.

Report wire format
------------------
OUTPUT (host → device), sent on the **control** pipe (the device exposes no
interrupt OUT endpoint):

    Byte 0 : HID Report ID     — always 0x00
    Byte 1 : USBCase           — command selector
    Byte 2 : param 1
    Byte 3 : param 2
    Byte 4 : param 3
    Rest   : zero padding

INPUT (device → host), 5 bytes on the interrupt IN endpoint.  Windows prepends
a report-ID byte; **Linux hidraw does not**, so every offset below is one lower
than the corresponding index in the C# source (the same shift documented for
the P3 in ``hid_bridge``):

    Bytes 0-2 : 24-bit signed ADC counts, big-endian (bit 23 = sign)
    Byte  3   : firmware version major   (ReadVersion reply)
    Byte  4   : firmware version minor   (ReadVersion reply)
"""

from __future__ import annotations

# ── Device identification ─────────────────────────────────────────────────────
VENDOR_ID = 0x275F           # shared with the current P3/D4
PRODUCT_ID = 0xF002          # MM01 — P3 is 0xF000, D4 is 0xF001

DEVICE_TYPES: dict[tuple[int, int], str] = {
    (VENDOR_ID, PRODUCT_ID): "MM01",
}

PRODUCT_TYPE = "MM01"
CHANNELS_PER_DEVICE = 1      # the MM01 is single-channel

# ── Report geometry ───────────────────────────────────────────────────────────
# The descriptor declares a 17-byte OUT report; the vendor library always sends
# a 64-byte buffer and lets the HID layer truncate.  17 is what the kernel
# accepts on Linux — a 65-byte write is rejected with EPIPE.
OUTPUT_REPORT_SIZE = 17
INPUT_REPORT_SIZE = 5

REPORT_ID_DEFAULT = 0x00

# ── USBCase — the command selector in byte 1 ─────────────────────────────────
USB_CASE_I2C_WRITE_DCP = 0x01
USB_CASE_ADC           = 0x02
USB_CASE_READ_VERSION  = 0x03
USB_CASE_GPIO          = 0x04
USB_CASE_WRITE_ID      = 0x06
USB_CASE_READ_ID       = 0x07

# ── USBCase.Adc sub-commands (param 1) ───────────────────────────────────────
ADC_STOP            = 0x00
ADC_START           = 0x01
ADC_SET_SAMPLE_RATE = 0x02

# SetSampleRate issues Adc(2, 0, 5) — a fixed 80 samples/second.
SAMPLE_RATE_CODE = 0x05
SAMPLE_RATE_HZ = 80.0

# ── Bridge selection ──────────────────────────────────────────────────────────
BRIDGE_QUARTER = 0
BRIDGE_HALF    = 1
BRIDGE_FULL    = 2

BRIDGE_NAMES: dict[int, str] = {
    BRIDGE_QUARTER: "QB",
    BRIDGE_HALF:    "HB",
    BRIDGE_FULL:    "FB",
}

# ── I2C targets used by Initdevice / SetGain ─────────────────────────────────
I2C_ADDR_ADC = 0x50          # ADC configuration
I2C_ADDR_DCP = 0xA0          # digital potentiometer (gain)
DCP_WIPER_VALUE = 0x40       # both wipers, for every bridge type

# ── Scaling ───────────────────────────────────────────────────────────────────
# AdcToVolts: counts * ADC_VOLTS_PER_COUNT * -1.0 - zero_offset
# 2.048 V reference over a 24-bit signed range (2**23).
ADC_VOLTS_PER_COUNT = 2.441406395519161e-07

# VoltsToUe: volts * GAIN * GAIN_FACTOR * (2.0 / gage_factor)
GAIN = 100.0
GAIN_FACTOR = 80.0

# SingleReadADCAsVolts returns volts * MV_PER_V_SCALE as mV/V.
MV_PER_V_SCALE = 4.0

# Defaults from the MM01Interface constructor.
DEFAULT_GAGE_FACTOR = 2.0

# Zero() averages this many samples.
ZERO_SAMPLE_COUNT = 50
