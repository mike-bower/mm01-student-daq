# Troubleshooting

## "No MM01 found"

Work down this list in order.

**1. Does the Pi see the USB device at all?**

```bash
lsusb | grep 275f
```

- *Nothing:* it is a cable or port problem, not a software one. Try another
  cable — some USB cables are charge-only and carry no data. Try another port.
- *`275f:f002` listed:* the hardware is fine; continue to step 2.

**2. Are the permissions right?**

```bash
ls -l /dev/hidraw*
```

You need a line ending `rw-rw-rw-`. If everything is `rw-------`, the udev rule
is missing or has not been applied:

```bash
sudo cp 99-mm01.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger
```

Then **unplug and replug the MM01**. Permissions are assigned when the device is
connected, so an already-plugged device keeps the old ones.

**3. Is another copy of the app already running?**

Only one process can hold the device. A second one finds nothing, or produces
garbled readings.

```bash
pgrep -af uvicorn
```

Stop the extra one and try again.

## The reading is stuck, or the chart flatlines

- Is the green **streaming** dot still lit? If it went grey, the WebSocket
  dropped; the page reconnects on its own after about five seconds.
- Check the terminal for `read failed` messages. Repeated failures usually mean
  a marginal power supply or a loose USB connector.

## The reading is pinned at a huge value

The input is open — the amplifier is saturated. Either the gage is
disconnected, or the bridge setting does not match the wiring. The classic case
is selecting **full bridge** with only a quarter bridge wired: the MM01 switches
off its internal completion, nothing terminates the input, and the reading rails.
Set the bridge back to match your wiring. This is Lab 4.

## The reading looks plausible but is the wrong size

- **Consistently too small by a fixed ratio?** Check the gage factor against the
  number printed on the gage package (Lab 3).
- **Reads low, and worse with long leads?** You are probably wired two-wire
  instead of three-wire. See `docs/01-hardware-setup.md`.
- **Drifts steadily over minutes?** Likely thermal — either the specimen warming
  or the gage self-heating. Let it settle before recording.

## The app will not start

```bash
./.venv/bin/python -m pytest tests/ -q
```

- *Tests pass:* the software is fine; the problem is hardware or configuration.
- *Tests fail:* the install is broken. Re-run `bash setup_pi.sh`.
- *`No module named ...`:* the virtual environment is missing. Re-run
  `bash setup_pi.sh`.

## pip tries to compile something, and takes forever

Someone has upgraded the pinned packages. Recent `pydantic-core` no longer ships
Python 3.9 wheels for the Pi, so pip falls back to building it from Rust source.
Restore the pins:

```bash
./.venv/bin/pip install --force-reinstall -r requirements.txt
```
