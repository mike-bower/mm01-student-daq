# Lab 3 — Gage factor

**Goal:** see how gage factor scales the answer, and why using the wrong one
quietly ruins a measurement. **Hardware needed:** none (simulator works).

---

## What gage factor is

A strain gage works because stretching a wire changes its resistance. **Gage
factor** is the constant of proportionality:

```
ΔR/R = GF × ε
```

It is a property of the gage's alloy and construction, measured by the
manufacturer and **printed on the gage package**. For common foil gages it is
close to 2.0, but it is not exactly 2.0, and it varies from batch to batch.

## Predict

The app converts volts to microstrain like this:

```python
def volts_to_microstrain(volts, gage_factor):
    return volts * GAIN * GAIN_FACTOR * (2.0 / gage_factor)
```

**Q1.** From that expression alone: if you double the gage factor, what happens
to the displayed microstrain? Write your answer down before measuring.

**Q2.** Suppose the real gage factor is 2.10 but you leave the app set to 2.00.
Will your measurements read high or low, and by what percentage?

## Measure

Run in simulator mode so the underlying signal is steady and repeatable.

1. Set the gage factor to **2.0**. Record the reading.
2. Set it to **4.0**. Record the reading.
3. Set it to **1.0**. Record the reading.

| Gage factor | Reading (µε) | Ratio to the GF 2.0 reading |
|---|---|---|
| 2.0 | | 1.00 |
| 4.0 | | |
| 1.0 | | |

**Q3.** Do your ratios match the prediction from Q1?

## Explain

The gage factor is applied **entirely in software**. Nothing is sent to the
MM01 when you change it — you can confirm this by reading `cmd_set_gage_factor`
in `app/mm01_bridge/manager.py`, which writes to a field and talks to no
hardware at all.

That has a practical consequence worth internalising: **the instrument cannot
detect a wrong gage factor.** A wrong value produces a perfectly stable,
plausible-looking reading that is simply the wrong size. There is no warning, no
error, and nothing in the data that looks unusual. The only defence is to read
the number off the gage package and enter it correctly.

**Q4.** Contrast this with a disconnected gage, which produces an obviously
absurd reading. Which of the two failures is more dangerous in a real
experiment, and why?

---

## Going further

The relationship is `2.0 / gage_factor`, not `1.0 / gage_factor`. The 2.0 is a
reference value baked into the instrument's calibration — the MM01's electronics
are calibrated as though the gage factor were exactly 2.

**Q5.** If a manufacturer released a gage with GF = 2.0 exactly, what would the
scale factor become, and what does that tell you about why 2.0 was chosen as the
reference?
