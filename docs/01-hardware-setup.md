# 1. Hardware setup

## What the MM01 is

The MM01 StudentDAQ is a **single-channel** strain gage instrument. One MM01
measures one gage. (A "MultiDAQ" setup is several MM01 units side by side, not
one multi-channel box.) It draws all its power from USB, converts continuously
at **80 samples per second**, and has no display or buttons — the computer is
its user interface.

## Wiring a gage

The MM01 accepts three bridge configurations. Pick the one that matches how you
have wired the gage, and set it in the app — the setting and the wiring must
agree or the reading will be wrong.

| Configuration | You supply | The MM01 supplies | Typical use |
|---|---|---|---|
| **Quarter bridge** | 1 active gage | 3 completion resistors | Most student work |
| **Half bridge** | 2 active gages | 2 completion resistors | Temperature compensation, bending |
| **Full bridge** | 4 active gages | nothing | Load cells, maximum sensitivity |

For quarter and half bridge the MM01 energises its internal completion
resistors. For full bridge it switches them off, because your own bridge
provides all four arms.

### Quarter bridge, three-wire (recommended)

Use **three wires** to the gage, not two. Two-wire hookup puts the resistance of
both lead wires in series with the gage, which makes the reading read low — the
longer the leads, the worse it gets. Three-wire hookup puts one lead in each of
two adjacent bridge arms, and the errors cancel.

```
        MM01 terminal            gage
            S+  ──────────────────┐
                                 [ ]  active gage
            S-  ──────────────────┘
            D   ──────────────────┘   (third wire, same point as S-)
```

Follow the terminal markings printed on your MM01; the labels vary by revision.

## Connecting to the Raspberry Pi

1. Plug the MM01 into any USB port on the Pi.
2. Check the Pi can see it:

   ```bash
   lsusb
   ```

   You should get a line containing **`275f:f002`**:

   ```
   Bus 003 Device 003: ID 275f:f002 Intersil HID Device MM01-350
   ```

   `275f` is the Micro-Measurements vendor ID and `f002` identifies the MM01.
   (`f000` is a P3 and `f001` is a D4 — different instruments, different
   protocol. This app only handles `f002`.)

3. Check the permissions are right:

   ```bash
   ls -l /dev/hidraw*
   ```

   One of them should end in `rw-rw-rw-`. If they are all `rw-------`, the udev
   rule is not installed or has not taken effect — see
   `docs/troubleshooting.md`.

## Power

The MM01 is powered entirely from the USB port. Use the official Raspberry Pi
power supply. An underpowered Pi does not report a clean error; instead
conversions are dropped and the reading stutters or freezes.
