# Lab 1 — First light

**Goal:** get a live reading and understand what the instrument is actually
doing. **Hardware needed:** none (simulator works).

---

## Part A — with no hardware

Run the app in simulator mode:

```bash
cp .env.example .env      # then set MM01_SIM_ENABLED=true
./run.sh
```

Open the page. You should see an amber **SIMULATOR** badge and a reading that
sweeps smoothly up and down.

**Q1.** The simulated signal is a sine wave of about ±1000 µε with a 10-second
period. Watch the `min` and `max` fields. How long does it take before they stop
changing, and why does that happen?

**Q2.** The badge exists so nobody mistakes generated numbers for measurements.
Why does that matter more in a lab report than in a demo?

## Part B — with real hardware

Stop the app, set `MM01_SIM_ENABLED=false`, plug in the MM01, and restart.

**Q3.** Record the serial number and firmware version shown on the page. These
identify your specific instrument — you will want them in your report.

**Q4.** With nothing loading the gage, the reading is almost certainly not zero.
Write down the value. Do not correct it yet; Lab 2 is about exactly this.

**Q5.** Watch the reading for 30 seconds without touching anything. Note the
smallest and largest values. The difference is your **noise floor** — no
measurement you make with this setup can resolve anything smaller.

---

## What is happening underneath

The MM01 converts at a fixed **80 samples per second**. The app does not ask for
each reading; once started, the instrument streams continuously and a background
thread reads it as fast as it arrives.

The browser is updated only about 5 times a second. That is deliberate — the eye
cannot use 80 updates a second, and the chart would be unreadable. The full rate
is still captured internally.

**Q6.** If the instrument produces 80 readings a second and the display updates
5 times a second, what happens to the other 75? (Look at `_reader_loop` and
`_publish_loop` in `app/mm01_bridge/manager.py`.)

---

## Going further

Open **http://localhost:8110/docs** — the interactive API documentation. Try
`GET /mm01/readings` and press *Execute*. That is the same data the page uses.
