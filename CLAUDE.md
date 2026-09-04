# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A teaching kit: a FastAPI app that reads a Micro-Measurements **MM01 StudentDAQ**
strain-gage instrument over USB HID and serves a live microstrain readout, strip
chart, CSV recording and REST/WebSocket API. Target deployment is a Raspberry Pi 4 on 64-bit
Raspberry Pi OS Bullseye (**CPython 3.9**), on a classroom network, with students
as the users. `docs/labs/` holds five student lab guides that the API surface and
the UI must keep matching.

## Commands

```bash
bash setup_pi.sh                          # one-time Pi setup (apt, venv, udev rule, vendored JS, tests)
./run.sh                                  # serve on 0.0.0.0:8110  (uvicorn main:app)
./.venv/bin/python -m pytest tests/ -q    # 103 tests, no hardware needed
```

Tests need no venv if `fastapi`/`pytest`/`httpx` are importable — `python3 -m pytest tests/ -q`
works on a dev machine. Run one test or class with the usual node id:

```bash
python3 -m pytest tests/test_api_mm01.py::TestZero -q
python3 -m pytest tests/test_api_mm01.py::TestZero::test_zero_leaves_the_adc_running -q
```

Interactive API docs at `http://localhost:8110/docs` while the app runs. There is
no linter or formatter configured; don't introduce one.

Supporting scripts: `tools/build_wheelhouse.sh` (offline Pi install — download
aarch64/cp39 wheels), `tools/vendor_assets.py` (re-download Alpine + uPlot into
`static/vendor/`), `tools/sync_bridge.sh` (see "Frozen driver" below).

Working without hardware: `cp .env.example .env` and set `MM01_SIM_ENABLED=true`.
The UI then shows a SIMULATOR badge. Config lives in `app/config.py` (pydantic-settings,
reads `.env`); the only knobs are `MM01_AUTO_SCAN`, `MM01_POLL_INTERVAL_MS`,
`MM01_SIM_ENABLED`, `MM01_SIM_COUNT`, `MM01_RECORD_DIR`,
`MM01_RECORD_INTERVAL_MS`, `MM01_RECORD_MAX_SECONDS`.

## Architecture

```
MM01 hardware  ── USB HID, 80 S/s, 24-bit signed ADC counts
  transport.py     open/read/write reports, enumerate, parse counts   (sync, thread-safe)
  protocol.py      one function per vendor operation + scaling math   (pure, no state)
  manager.py       device registry, reader threads, publisher thread, commands
  routers/mm01.py  REST + /mm01/ws WebSocket, all blocking I/O via run_in_executor
  recorder.py      writer thread → recordings/<id>.csv + .json   (outside the driver)
  routers/recording.py  /recording start, stop, status, list, download, delete
  static/          one page: Alpine stores (mm01, rec) + uPlot strip chart
```

**The device streams; it is not polled.** Once the ADC starts, the MM01 pushes
conversions at a fixed 80 S/s and answers no read command. So `MM01DeviceManager`
runs one blocking **reader thread per device** that keeps `MM01DeviceInfo.last_*`
current, plus one **publisher thread** that fans a `MM01ScanFrame` out to WebSocket
subscriber queues every `poll_interval_ms`. `poll_interval_ms` controls only how
often the browser is updated — never the conversion rate.

**`_DeviceLock` gives commands priority over the reader.** The reader re-acquires
the per-device lock immediately after each read, and CPython locks are not FIFO,
so a plain `Lock` starves command threads — on real hardware this stalled bridge
changes for over a second. Commands register as waiters before blocking and the
reader defers while any waiter exists. Keep `_READ_TIMEOUT_MS` short (100 ms) for
the same reason: it bounds worst-case command latency.

**One device is one channel.** The MM01 is single-channel, so `device_index` is
also the channel index — there is no flat global-channel mapping like the P3/D4.

**Gage factor and zero offset are host-side.** Nothing is written to the device
for either; `adc_to_volts` subtracts the stored offset and `volts_to_microstrain`
applies the gage factor. `cmd_zero` calls `avg_adc`, which *stops* the ADC — it
must restart it or the stream dies (there is a test for exactly this).

**Linux hidraw drops the report-ID byte.** Every input-report offset in
`constants.py`/`transport.py` is one lower than the corresponding index in the
vendor's C# source. The constants were recovered from `MM01InterfaceLib.dll` v2.0.2;
`constants.py` is the reference for the wire format — read it before touching
protocol code.

**Recording samples, it does not capture every conversion.** `Recorder` runs one
writer thread that reads `MM01DeviceInfo.last_*` on a tick anchored to the start
time (no drift) and appends a CSV row. At 12 ms that is approximately every
conversion; slower intervals discard the conversions in between — it is not an
average, and the raw `counts_N` column is written so a repeated sample is visible
in the data. Two files per session: `<id>.csv` (clean, no preamble, opens in
Excel) and `<id>.json` (gage factor, bridge, zero offset — microstrain is
meaningless without them). The metadata is written at start *and* rewritten at
stop, so a session killed by a power cut lists as `interrupted` instead of
vanishing; the live session is excluded from `list_sessions()` because its stored
row count is still zero. `Recorder._lock` is never held across the thread join in
`stop()` — the writer thread's own `_finish()` takes it.

**The recorder lives outside `app/mm01_bridge/` on purpose.** That whole
directory is overwritten by `tools/sync_bridge.sh` (below), so recording is built
on the manager's public surface only — `devices`, `get_device()` — and nothing in
the driver knows it exists. Put recording changes in `app/recorder.py`,
`app/routers/recording.py`, `app/models/recording_models.py`,
`static/js/recording.js` or `tests/test_recording.py`; none of those are synced.

**Simulation mirrors the real path.** `SimMM01DeviceManager` overrides only `scan()`;
`VirtualMM01Device` duck-types `MM01Device` and encodes synthetic microstrain back
through the *real* scaling constants, so the whole manager/router/UI stack is
exercised unchanged. Tests use it — hardware behaviour changes belong in
`virtual_device.py` too, or the tests stop meaning anything.

Front end has **no build step**: `static/index.html` loads `vendor/*` then four
plain scripts that define globals (`API`, `Charts`, `MM01WS` + the `mm01` and
`rec` Alpine stores).
Vendored libraries are committed on purpose so a Pi with no internet still works.

## Constraints specific to this repo

- **Do not upgrade `requirements.txt`.** Pins are chosen so every package and
  transitive dep has a prebuilt aarch64/cp39 wheel. `pydantic-core` 2.48+ dropped
  cp39, so an upgrade makes pip compile Rust on the Pi for hours. The reasoning is
  at the top of the file — preserve it.
- **Python 3.9 runtime.** Modules rely on `from __future__ import annotations` for
  `X | None` in signatures; that does not help Pydantic models, whose annotations
  are evaluated. Use `Optional[X]` in `app/models/`, never `X | None`.
- **Frozen driver.** `tools/sync_bridge.sh` copies `app/mm01_bridge/*.py`,
  `app/models/mm01_models.py`, `app/routers/mm01.py`, `static/js/mm01.js`,
  `tests/test_mm01_protocol.py` and `tests/test_api_mm01.py` from the upstream
  `system_8000_api` project — **local edits to those files are overwritten**. Fix
  driver bugs upstream, then sync and re-run the tests.
- **No authentication, binds `0.0.0.0`.** Deliberate, so students reach the Pi from
  a laptop. Fine on a classroom network; don't add auth without being asked, and
  don't widen exposure further.
- Several docstrings and the 503 detail string reference an `MM01_ENABLED` setting
  inherited from the parent project. It does not exist in `app/config.py`, and a
  test asserts on that exact string — don't "fix" one without the other.
- `tests/conftest.py` disables `.env` and scrubs `MM01_*` env vars so a student who
  set simulator mode for a lab gets the same test results as everyone else. The
  scrub list is explicit — add new settings to it.
- **Recording tests write to `tmp_path`**, never to `recordings/`. `recordings/`
  is gitignored and holds student data; nothing in the repo should clean it up
  automatically.
