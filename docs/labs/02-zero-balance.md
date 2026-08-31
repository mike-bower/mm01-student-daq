# Lab 2 — Zero and balance

**Goal:** understand why an unloaded gage does not read zero, and what "zeroing"
really does. **Hardware needed:** MM01 with a gage, unloaded. (Simulator works
for the mechanics, but the physics discussion needs a real gage.)

---

## The problem

In Lab 1 you recorded a non-zero reading with no load applied. A perfect
Wheatstone bridge with an unstrained gage would output exactly zero volts. Real
ones never do, because:

- the gage's resistance is only within a tolerance of its nominal value,
- the completion resistors have their own tolerances,
- the lead wires add resistance,
- the gage may already be slightly strained by being bonded to the specimen.

None of these are measurement *errors* in the usual sense — they are a constant
offset. The fix is to measure the offset once and subtract it.

## Predict

**Q1.** Before you press anything: after zeroing, what will the reading be?

**Q2.** The app reports the offset in **volts**, not microstrain. Why is volts
the more sensible unit to store it in? (Hint: what else would have to be true
for microstrain to be the right choice?)

## Measure

1. With the gage unloaded and settled, note the reading.
2. Press **Zero**. It takes about three-quarters of a second.
3. Note the new reading and the new offset value.
4. Press **Clear**. Note the reading again.

**Q3.** Did the reading return to its original value after *Clear*? What does
that tell you about whether *Zero* changed anything inside the instrument?

## Explain

*Zero* averages **50 consecutive conversions**, converts the average to volts,
and stores it. Every later reading has that value subtracted:

```python
def adc_to_volts(counts, zero_offset=0.0):
    return counts * ADC_VOLTS_PER_COUNT * -1.0 - zero_offset
```

Two things follow, and both matter:

- **The subtraction happens in software.** The bridge is still just as
  unbalanced as it was. Nothing was trimmed, and no hardware changed. If you
  restart the app, the offset is gone.
- **It averages 50 samples, not 1.** A single sample carries the noise you
  measured in Lab 1. Averaging 50 reduces the random part by roughly √50 ≈ 7.

**Q4.** The ADC runs at 80 samples/second, so 50 samples take about 0.6 s — yet
zeroing takes roughly 0.73 s. Where does the extra time go? (Look at `avg_adc`
in `app/mm01_bridge/protocol.py`.)

**Q5.** Zeroing *stops* the ADC while it averages, then restarts it. What would
go wrong if the software forgot to restart it?

---

## Going further

Zero the gage, then warm it gently — a hand held near it is enough — and watch
the reading. You are seeing **thermal output**: the gage and the specimen expand
at different rates, so the bridge sees an apparent strain that has nothing to do
with load. Serious measurements either hold temperature constant or compensate
for this.

**Q6.** You zeroed at room temperature. If the room warms by 5 °C during a
one-hour experiment, is your zero still valid? What would you do about it?
