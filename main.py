"""
MM01 StudentDAQ — a small FastAPI app that reads a Micro-Measurements MM01
(StudentDAQ / MultiDAQ) over USB and serves a live strain readout.

Run it:
    ./run.sh
        or
    uvicorn main:app --host 0.0.0.0 --port 8110

Then open http://localhost:8110 on the Pi, or http://<pi-ip>:8110 from another
machine on the same network.

There is no authentication — this is meant for a classroom network.
"""

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.config import get_settings
from app.routers import mm01

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("mm01")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the MM01 device manager, and stop it cleanly on shutdown."""
    settings = get_settings()
    app.state.settings = settings

    manager = None
    try:
        if settings.mm01_sim_enabled:
            from app.mm01_bridge.sim_manager import SimMM01DeviceManager
            manager = SimMM01DeviceManager(
                device_count=settings.mm01_sim_count,
                poll_interval_ms=settings.mm01_poll_interval_ms,
            )
            log.info("Simulator mode — %d virtual device(s)", settings.mm01_sim_count)
        else:
            from app.mm01_bridge.manager import MM01DeviceManager
            manager = MM01DeviceManager(poll_interval_ms=settings.mm01_poll_interval_ms)

        if settings.mm01_auto_scan:
            manager.scan()
        manager.start(loop=asyncio.get_running_loop())
        app.state.mm01_manager = manager

        if not manager.devices:
            log.warning(
                "No MM01 found. Check the USB cable, run `lsusb` and look for "
                "275f:f002, and confirm the udev rule is installed "
                "(see docs/01-hardware-setup.md). To work without hardware, "
                "set MM01_SIM_ENABLED=true in .env."
            )
    except Exception:
        log.exception("MM01 bridge failed to start — the page will show an error")
        manager = None

    yield

    if manager is not None:
        await manager.stop()


app = FastAPI(
    title="MM01 StudentDAQ",
    description="Live strain readout for the Micro-Measurements MM01.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(mm01.router)


@app.get("/", include_in_schema=False)
async def index():
    return RedirectResponse(url="/app/")


_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/app", StaticFiles(directory=_static_dir, html=True), name="static")
