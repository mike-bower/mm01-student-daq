"""
Pydantic models for the /mm01 API endpoints (StudentDAQ / MultiDAQ).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────

class MM01Bridge(IntEnum):
    QuarterBridge = 0
    HalfBridge    = 1
    FullBridge    = 2


# ── Response models ───────────────────────────────────────────────────────────

class MM01DeviceModel(BaseModel):
    """One connected MM01 — also its single measurement channel."""
    device_index: int = Field(..., description="0-based device number, ordered by serial")
    path: str
    vendor_id: int
    product_id: int
    product_type: str = Field(..., description='Always "MM01"')
    serial_number: str
    firmware_version: str
    in_error: bool
    active: bool
    bridge: MM01Bridge
    bridge_name: str = Field(..., description='"QB", "HB" or "FB"')
    gage_factor: float
    zero_offset: float = Field(..., description="Balance offset in volts")
    label: str


class MM01DeviceListResponse(BaseModel):
    devices: list[MM01DeviceModel]
    total_devices: int


class MM01ReadingResponse(BaseModel):
    """Most recent streamed reading for one device."""
    device_index: int
    microstrain: Optional[float] = Field(None, description="Null if not yet streaming")
    mv_per_v: Optional[float] = None
    counts: int = Field(0, description="Raw 24-bit signed ADC counts")
    in_error: bool = False


class MM01SnapshotResponse(BaseModel):
    """Latest reading from every device."""
    timestamp: float = Field(..., description="Server monotonic timestamp (seconds)")
    readings: dict[int, Optional[float]] = Field(
        ..., description="Map of device_index → microstrain (null = unavailable)"
    )
    mv_per_v: dict[int, Optional[float]] = Field(default_factory=dict)
    error_devices: list[int] = Field(default_factory=list)


class MM01CommandResponse(BaseModel):
    ok: bool = True
    message: str = ""


class MM01ZeroResponse(BaseModel):
    ok: bool = True
    device_index: int
    zero_offset: float = Field(..., description="New balance offset in volts")


# ── Request models ────────────────────────────────────────────────────────────

class SetBridgeRequest(BaseModel):
    bridge: MM01Bridge


class SetGageFactorRequest(BaseModel):
    gage_factor: float = Field(..., gt=0, le=9.999)


class SetLabelRequest(BaseModel):
    label: str = Field("", max_length=32)
