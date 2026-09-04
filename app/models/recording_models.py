"""
Pydantic models for the /recording API endpoints.

Python 3.9 runtime: annotations here are evaluated by Pydantic, so use
Optional[X], never `X | None`.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.recorder import MAX_INTERVAL_MS, MIN_INTERVAL_MS


# ── Response models ───────────────────────────────────────────────────────────

class RecordedDeviceModel(BaseModel):
    """How one device was configured for the whole of a recording."""
    device_index: int
    serial_number: str = ""
    firmware_version: str = ""
    bridge_name: str = Field("", description='"QB", "HB" or "FB"')
    gage_factor: float = 0.0
    zero_offset: float = Field(0.0, description="Balance offset in volts, at start")
    label: str = ""
    simulated: bool = False


class RecordingSessionModel(BaseModel):
    """One recording — in progress or finished."""
    session_id: str = Field(..., description="Also the CSV filename stem")
    name: str = ""
    note: str = ""
    started_at: str = Field(..., description="ISO-8601 local wall clock")
    stopped_at: Optional[str] = Field(None, description="Null while still recording")
    stop_reason: Optional[str] = Field(
        None, description='"user", "shutdown", "max_duration", "error" or "interrupted"'
    )
    sample_interval_ms: int
    sample_rate_hz: float = Field(..., description="Rows per second, 1000/interval")
    rows: int = 0
    duration_s: float = 0.0
    csv_file: str
    size_bytes: int = 0
    columns: list[str] = Field(default_factory=list)
    devices: list[RecordedDeviceModel] = Field(default_factory=list)


class RecordingStatusResponse(BaseModel):
    recording: bool
    session: Optional[RecordingSessionModel] = Field(
        None, description="The in-progress recording, or null"
    )
    directory: str
    default_interval_ms: int
    max_seconds: float = Field(..., description="A recording auto-stops after this long")


class RecordingListResponse(BaseModel):
    sessions: list[RecordingSessionModel]
    total_sessions: int


class RecordingCommandResponse(BaseModel):
    ok: bool = True
    message: str = ""


# ── Request models ────────────────────────────────────────────────────────────

class StartRecordingRequest(BaseModel):
    name: str = Field("", max_length=48, description="Becomes part of the filename")
    note: str = Field("", max_length=200, description="Stored with the data")
    sample_interval_ms: Optional[int] = Field(
        None, ge=MIN_INTERVAL_MS, le=MAX_INTERVAL_MS,
        description="Rows per second is 1000/this. Omit for the server default.",
    )
    device_indexes: Optional[list[int]] = Field(
        None, description="Devices to record. Omit for all of them."
    )
