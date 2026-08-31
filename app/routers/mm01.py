"""
FastAPI router for Micro-Measurements MM01 (StudentDAQ / MultiDAQ) instruments.

Mounted at /mm01.  Requires MM01_ENABLED=true in .env and at least one MM01
connected via USB (or MM01_SIM_ENABLED=true for virtual devices).

Each MM01 is single-channel, so a device index is also a channel index.

All blocking HID I/O is dispatched to a thread pool via run_in_executor so the
FastAPI event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status

from app.mm01_bridge.manager import MM01DeviceInfo, MM01DeviceManager, MM01ScanFrame
from app.mm01_bridge.transport import MM01TransportError
from app.models.mm01_models import (
    MM01CommandResponse,
    MM01DeviceListResponse,
    MM01DeviceModel,
    MM01ReadingResponse,
    MM01SnapshotResponse,
    MM01ZeroResponse,
    SetBridgeRequest,
    SetGageFactorRequest,
    SetLabelRequest,
)

router = APIRouter(prefix="/mm01", tags=["MM01 Devices (StudentDAQ)"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_manager(request: Request) -> MM01DeviceManager:
    """Retrieve the MM01DeviceManager from app state, or raise 503."""
    manager: MM01DeviceManager | None = getattr(request.app.state, "mm01_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MM01 device support is not enabled. Set MM01_ENABLED=true in .env.",
        )
    return manager


def _clean(value: float) -> Optional[float]:
    """Map NaN/inf to None so the value survives JSON serialisation."""
    return None if (value is None or not math.isfinite(value)) else value


def _to_model(dev: MM01DeviceInfo) -> MM01DeviceModel:
    return MM01DeviceModel(
        device_index=dev.device_index,
        path=dev.path,
        vendor_id=dev.vendor_id,
        product_id=dev.product_id,
        product_type=dev.product_type,
        serial_number=dev.serial_number,
        firmware_version=dev.firmware_version,
        in_error=dev.in_error,
        active=dev.active,
        bridge=dev.bridge,
        bridge_name=dev.bridge_name,
        gage_factor=dev.gage_factor,
        zero_offset=dev.zero_offset,
        label=dev.label,
    )


def _require_device(manager: MM01DeviceManager, dev: int) -> MM01DeviceInfo:
    info = manager.get_device(dev)
    if info is None:
        raise HTTPException(status_code=404, detail=f"MM01 device {dev} not found")
    return info


async def _run(fn, *args):
    """Run a blocking function in the default thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


# ── Device endpoints ──────────────────────────────────────────────────────────

@router.get("/devices", response_model=MM01DeviceListResponse,
            summary="List connected MM01 devices")
async def list_devices(request: Request):
    manager = _get_manager(request)
    devices = manager.devices
    return MM01DeviceListResponse(
        devices=[_to_model(d) for d in devices],
        total_devices=len(devices),
    )


@router.post("/scan", response_model=MM01DeviceListResponse,
             summary="Re-scan USB for MM01 devices")
async def rescan_devices(request: Request):
    """Trigger a fresh USB enumeration. Closes and re-opens all handles."""
    manager = _get_manager(request)
    await _run(manager.scan)
    devices = manager.devices
    return MM01DeviceListResponse(
        devices=[_to_model(d) for d in devices],
        total_devices=len(devices),
    )


@router.get("/devices/{dev}", response_model=MM01DeviceModel,
            summary="Get one MM01 device")
async def get_device(dev: int, request: Request):
    manager = _get_manager(request)
    return _to_model(_require_device(manager, dev))


# ── Readings ──────────────────────────────────────────────────────────────────

@router.get("/readings", response_model=MM01SnapshotResponse,
            summary="Latest reading from every MM01")
async def get_all_readings(request: Request):
    manager = _get_manager(request)
    frame = manager.snapshot()
    return MM01SnapshotResponse(
        timestamp=frame.timestamp,
        readings={k: _clean(v) for k, v in frame.readings.items()},
        mv_per_v={k: _clean(v) for k, v in frame.mv_per_v.items()},
        error_devices=frame.error_devices,
    )


@router.get("/devices/{dev}/reading", response_model=MM01ReadingResponse,
            summary="Latest reading for one MM01")
async def get_reading(dev: int, request: Request):
    manager = _get_manager(request)
    info = _require_device(manager, dev)
    return MM01ReadingResponse(
        device_index=info.device_index,
        microstrain=_clean(info.last_microstrain),
        mv_per_v=_clean(info.last_mv_per_v),
        counts=info.last_counts,
        in_error=info.in_error,
    )


# ── Configuration ─────────────────────────────────────────────────────────────

@router.post("/devices/{dev}/bridge", response_model=MM01CommandResponse,
             summary="Set bridge configuration")
async def set_bridge(dev: int, body: SetBridgeRequest, request: Request):
    """Set quarter, half or full bridge.

    Quarter and half bridges energise the internal completion; full bridge
    disables it.
    """
    manager = _get_manager(request)
    _require_device(manager, dev)
    try:
        await _run(manager.cmd_set_bridge, dev, int(body.bridge))
    except MM01TransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return MM01CommandResponse(message=f"Device {dev} set to {body.bridge.name}")


@router.post("/devices/{dev}/gage-factor", response_model=MM01CommandResponse,
             summary="Set gage factor")
async def set_gage_factor(dev: int, body: SetGageFactorRequest, request: Request):
    """Set the gage factor used to convert volts to microstrain.

    Applied host-side — the MM01 stores no gage factor of its own.
    """
    manager = _get_manager(request)
    _require_device(manager, dev)
    try:
        await _run(manager.cmd_set_gage_factor, dev, body.gage_factor)
    except MM01TransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return MM01CommandResponse(message=f"Device {dev} gage factor set to {body.gage_factor}")


@router.post("/devices/{dev}/zero", response_model=MM01ZeroResponse,
             summary="Balance the device (store current reading as zero)")
async def zero_device(dev: int, request: Request):
    """Average 50 conversions and store the result as the balance offset."""
    manager = _get_manager(request)
    _require_device(manager, dev)
    try:
        offset = await _run(manager.cmd_zero, dev)
    except MM01TransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return MM01ZeroResponse(device_index=dev, zero_offset=offset)


@router.delete("/devices/{dev}/zero", response_model=MM01CommandResponse,
               summary="Clear the balance offset")
async def clear_zero(dev: int, request: Request):
    manager = _get_manager(request)
    _require_device(manager, dev)
    try:
        await _run(manager.cmd_clear_zero, dev)
    except MM01TransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return MM01CommandResponse(message=f"Device {dev} balance cleared")


@router.post("/devices/{dev}/label", response_model=MM01CommandResponse,
             summary="Set a display label")
async def set_label(dev: int, body: SetLabelRequest, request: Request):
    manager = _get_manager(request)
    _require_device(manager, dev)
    try:
        await _run(manager.cmd_set_label, dev, body.label)
    except MM01TransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return MM01CommandResponse(message=f"Device {dev} label set")


# ── WebSocket stream ──────────────────────────────────────────────────────────

@router.websocket("/ws")
async def mm01_websocket(websocket: WebSocket):
    """Stream MM01 readings as JSON objects.

    Each message has the shape of MM01SnapshotResponse.  Frames are published
    at the configured interval (default 200 ms); the devices themselves convert
    at a fixed 80 samples/second.
    """
    manager: MM01DeviceManager | None = getattr(websocket.app.state, "mm01_manager", None)
    if manager is None:
        await websocket.close(code=1011, reason="MM01 not enabled")
        return

    await websocket.accept()
    queue: asyncio.Queue[MM01ScanFrame] = asyncio.Queue(maxsize=64)
    manager.subscribe(queue)

    try:
        while True:
            frame: MM01ScanFrame = await queue.get()
            await websocket.send_json({
                "timestamp": frame.timestamp,
                "readings": {str(k): _clean(v) for k, v in frame.readings.items()},
                "mv_per_v": {str(k): _clean(v) for k, v in frame.mv_per_v.items()},
                "error_devices": frame.error_devices,
            })
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(queue)
