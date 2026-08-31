"""
Simulated MM01DeviceManager — injects VirtualMM01Device instances instead of
opening real USB HID handles.

Activated when MM01_ENABLED=true and MM01_SIM_ENABLED=true in .env.  Neither
physical hardware nor the `hid` package is required when the simulator is used.

Usage (.env):
    MM01_ENABLED=true
    MM01_SIM_ENABLED=true
    MM01_SIM_COUNT=2      # number of simulated MM01 devices
"""

from __future__ import annotations

import logging

from app.mm01_bridge.constants import PRODUCT_ID, PRODUCT_TYPE, VENDOR_ID
from app.mm01_bridge.manager import MM01DeviceInfo, MM01DeviceManager, _DeviceLock
from app.mm01_bridge.virtual_device import VirtualMM01Device
from app.mm01_bridge import protocol as proto

log = logging.getLogger(__name__)


class SimMM01DeviceManager(MM01DeviceManager):
    """MM01DeviceManager variant populated with virtual devices.

    All streaming, publishing and command behaviour is inherited unchanged —
    only scan() is overridden to skip USB enumeration.
    """

    def __init__(self, device_count: int = 1, poll_interval_ms: int = 200) -> None:
        super().__init__(poll_interval_ms=poll_interval_ms)
        self._device_count = max(0, device_count)

    def scan(self) -> list[MM01DeviceInfo]:
        with self._scan_lock:
            self._stop_readers()
            self._close_all_handles()
            self._devices.clear()
            self._device_locks.clear()

            for dev_idx in range(self._device_count):
                serial = f"VMM01-{dev_idx + 1:04d}"
                path = f"virtual://mm01/{dev_idx}"
                vdev = VirtualMM01Device(
                    path,
                    serial_number=serial,
                    # Give each simulated gage a distinct amplitude so a
                    # multi-device stream is visibly different per device.
                    amplitude_ue=1000.0 + 250.0 * dev_idx,
                )
                vdev.open()
                self._handles[dev_idx] = vdev  # type: ignore[assignment]  — duck-typed
                self._device_locks[dev_idx] = _DeviceLock()

                info = MM01DeviceInfo(
                    device_index=dev_idx,
                    path=path,
                    vendor_id=VENDOR_ID,
                    product_id=PRODUCT_ID,
                    product_type=PRODUCT_TYPE,
                    serial_number=serial,
                    firmware_version=vdev.firmware_version,
                )
                proto.init_device(vdev, info.bridge)  # type: ignore[arg-type]
                self._devices.append(info)

            log.info("Sim MM01: %d virtual device(s)", len(self._devices))
            return list(self._devices)
