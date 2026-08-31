# 2. Your first reading

Work through this once before starting the labs.

## Start the app

```bash
cd ~/mm01-student-daq
./run.sh
```

Open **http://localhost:8110** on the Pi. From a laptop on the same network, use
`http://<pi-ip>:8110` — run `hostname -I` on the Pi to find the address.

You should see a green dot, the word **streaming**, and a large number in µε
that changes several times a second.

## Reading the display

```
  MM01   serial 215460 · fw 2.0                        QB · GF 2.0

        78.5 µε

  mV/V 0.03911 · min 78.2  max 78.6 · offset 0.000000 V
```

| Field | Meaning |
|---|---|
| **78.5 µε** | Microstrain — the headline measurement |
| **mV/V** | The bridge's raw output per volt of excitation, before any strain maths |
| **min / max** | The smallest and largest values seen since the page loaded |
| **offset** | The balance offset stored by *Zero*, in volts |
| **QB** | Bridge setting — quarter, half or full |
| **GF 2.0** | The gage factor being used to convert volts into strain |

## What "microstrain" means

Strain is a ratio: how much a material stretches divided by its original length.
It is a very small number, so it is quoted in **microstrain**, where
1 µε = 1 × 10⁻⁶.

A reading of 78.5 µε means the material has stretched by 78.5 parts per million
— for a 100 mm gage length, about 0.008 mm. That is why this needs an
instrument rather than a ruler.

## The signal path

The MM01 sends 24-bit signed ADC counts. The app converts them in three steps:

```
counts ──► volts ──► microstrain
```

1. **counts → volts** — the ADC spans ±2.048 V over its 24-bit signed range,
   so one count is 2.048 / 2²³ volts. The sign is inverted to match the
   instrument's polarity, and the stored balance offset is subtracted.
2. **volts → microstrain** — multiplied by the amplifier gain (100) and an
   instrument constant (80), then scaled by 2 / gage factor.

Both steps are in `app/mm01_bridge/protocol.py`, as `adc_to_volts()` and
`volts_to_microstrain()`. They are short, and worth reading.

Note where the work happens: **the gage factor and the zero offset are applied
by the software, not by the instrument.** The MM01 stores neither. Changing
either one re-scales the numbers on screen; it does not reconfigure the
hardware. That fact is the subject of Labs 2 and 3.

## Stopping

Press `Ctrl-C` in the terminal. The app stops the ADC on the way out.
