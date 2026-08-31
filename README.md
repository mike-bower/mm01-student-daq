# MM01 StudentDAQ — Raspberry Pi kit

Read real strain measurements from a Micro-Measurements **MM01 StudentDAQ**
using a Raspberry Pi and a web browser.

The MM01 is a single-channel strain gage instrument. It connects over USB,
converts at a fixed 80 samples per second, and this app turns that stream into
a live microstrain readout and a strip chart.

---

## What you need

- Raspberry Pi 4 running 64-bit Raspberry Pi OS (Bullseye, Python 3.9)
- An MM01 StudentDAQ and its USB cable
- A strain gage wired to the MM01 (see `docs/01-hardware-setup.md`)
- Internet on the Pi **once**, for setup only

You can do Labs 1–4 with no MM01 at all, using simulator mode.

---

## Setup

Copy this folder to the Pi (a USB stick is fine), then:

```bash
cd ~/mm01-student-daq
bash setup_pi.sh
```

That installs the system packages, creates a Python virtual environment,
installs the Python packages, adds the USB permission rule, and runs the test
suite to confirm everything works.

**Unplug and replug the MM01 afterwards** so the new USB permissions apply.

### Run it

```bash
./run.sh
```

Then open **http://localhost:8110** on the Pi, or **http://\<pi-ip\>:8110** from
any other machine on the same network (`hostname -I` shows the Pi's address).

### No MM01 yet?

```bash
cp .env.example .env
# set MM01_SIM_ENABLED=true
./run.sh
```

The app then generates a smooth synthetic signal, and the page shows a
**SIMULATOR** badge so nobody mistakes it for a measurement.

### No internet on the Pi?

Prepare an offline package first, on the computer you are copying from:

```bash
bash tools/build_wheelhouse.sh
```

That downloads Raspberry Pi wheels into `wheelhouse/`. Copy the whole folder to
the USB stick; `setup_pi.sh` finds it and installs without a network.

---

## Learn it

| Document | |
|---|---|
| `docs/01-hardware-setup.md` | Wiring the gage and connecting the MM01 |
| `docs/02-first-reading.md`  | Your first live measurement, start to finish |
| `docs/labs/01-first-light.md`    | Lab 1 — get a reading, understand what it means |
| `docs/labs/02-zero-balance.md`   | Lab 2 — why the reading isn't zero, and what to do |
| `docs/labs/03-gage-factor.md`    | Lab 3 — how gage factor scales the answer |
| `docs/labs/04-bridge-type.md`    | Lab 4 — quarter, half and full bridge |
| `docs/labs/05-cantilever-beam.md`| Lab 5 — measure a beam, check it against theory |
| `docs/troubleshooting.md`   | When it doesn't work |

---

## How it works

```
MM01 hardware
   │  USB HID, 80 samples/second, 24-bit signed ADC counts
   ▼
app/mm01_bridge/     the driver: framing, scaling, one reader thread per device
   ▼
app/routers/mm01.py  REST endpoints + a WebSocket that pushes readings
   ▼
static/              a single page: live readout, strip chart, controls
```

The driver is deliberately small and readable — it imports only the Python
standard library plus `hid`. Start at `app/mm01_bridge/protocol.py`, which has
one function per device operation.

### The API

With the app running, the interactive API docs are at
**http://localhost:8110/docs**. You can drive every control from there, which is
useful for the labs.

```
GET    /mm01/devices              list connected MM01s
GET    /mm01/readings             latest reading from each
POST   /mm01/devices/0/bridge     {"bridge": 0|1|2}  quarter / half / full
POST   /mm01/devices/0/gage-factor{"gage_factor": 2.0}
POST   /mm01/devices/0/zero       balance: average 50 readings, store as zero
DELETE /mm01/devices/0/zero       clear the balance offset
WS     /mm01/ws                   live stream
```

---

## Checking the install

```bash
./.venv/bin/python -m pytest tests/ -q
```

72 tests, no hardware required. If these pass, the software is fine and any
problem is in the wiring or the USB connection.

---

## Notes for instructors

- **Do not upgrade the Python packages.** `requirements.txt` is pinned so that
  every package installs as a prebuilt binary on a Pi. Newer `pydantic-core`
  has dropped Python 3.9 support, and `pip install -U` will try to compile Rust
  on the Pi — which takes hours. The reason is written at the top of that file.
- **No authentication.** The app listens on all network interfaces so students
  can reach it from a laptop. That is fine on a classroom network and not fine
  on an open one.
- **The driver is a frozen copy** from the System 8000 API project. If it is
  fixed upstream, run `bash tools/sync_bridge.sh` and re-run the tests.
