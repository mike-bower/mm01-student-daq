# Lab 5 — Measure a cantilever beam

**Goal:** predict a strain from beam theory, measure it, and account for the
difference. This is the lab where everything so far gets used at once.
**Hardware needed:** MM01, a quarter-bridge gage bonded to a cantilever beam,
and a known mass.

---

## Set up

1. Clamp the beam firmly at one end so it cantilevers horizontally.
2. The gage should be bonded on the top or bottom surface, near the clamped
   end, aligned **along** the beam's length.
3. Measure and record:

   | Symbol | Quantity | Your value |
   |---|---|---|
   | `b` | beam width | m |
   | `h` | beam thickness | m |
   | `L` | distance from the **gage centre** to the **load point** | m |
   | `E` | Young's modulus of the beam material | Pa |
   | `P` | applied load (mass × 9.81) | N |

   Typical `E`: aluminium ≈ 69 GPa, steel ≈ 200 GPa.

> Measure `h` carefully. It is **squared** in the formula, so a 2% error in
> thickness becomes a 4% error in your predicted strain — usually the largest
> single source of disagreement in this lab.

## Predict

For a cantilever with a point load, the bending moment at the gage is `M = P·L`.
For a rectangular cross-section the section modulus is `S = b·h²/6`, so the
surface stress is `σ = M/S`, and strain is `σ/E`:

```
        6 · P · L
ε  =  ─────────────         (multiply by 10⁶ for microstrain)
       E · b · h²
```

### Worked example

An aluminium beam, `b` = 25 mm, `h` = 3 mm, gage `L` = 150 mm from the load,
`E` = 69 GPa, loaded with a 500 g mass (`P` = 4.905 N):

```
ε = (6 × 4.905 × 0.150) / (69e9 × 0.025 × 0.003²)
  = 4.4145 / 15525
  = 2.843e-4
  = 284 µε
```

**Q1.** Compute the predicted strain for **your** beam and load. Write it down
before you measure anything.

## Measure

1. With the beam **unloaded and still**, press **Zero**. This removes the
   bridge offset *and* the strain caused by the beam's own weight, so you
   measure only the effect of the load you add.
2. Confirm the gage factor matches the gage package (Lab 3).
3. Confirm the bridge is set to **Quarter bridge** (Lab 4).
4. Apply the mass at the load point. Wait for the reading to settle.
5. Record the reading. Remove the mass and confirm it returns near zero.
6. Repeat three times and average.

| Trial | Reading (µε) |
|---|---|
| 1 | |
| 2 | |
| 3 | |
| **Mean** | |

## Compare

```
                 measured − predicted
percent error =  ────────────────────  × 100
                      predicted
```

**Q2.** What is your percent error? Agreement within about 10% is a good result
for a student setup.

**Q3.** Is your measured value **larger or smaller** than predicted? The sign is
informative — work through the list below and decide which effects apply to your
setup and which direction each pushes the result.

## Account for the difference

| Source | Effect |
|---|---|
| **Gage position** | `L` is to the gage *centre*. If you measured to the edge, `L` is wrong. |
| **Thickness** | Squared in the formula — the usual dominant error. |
| **Young's modulus** | A handbook value for "aluminium" may be several percent off your actual alloy. |
| **Clamp stiffness** | A clamp that flexes makes the real `L` longer than you measured, so the measured strain reads high. |
| **Gage alignment** | A gage rotated by angle θ reads roughly `cos²θ` of the true axial strain — always low. |
| **Temperature** | Drift during the run (Lab 2). Work quickly, or re-zero. |
| **Beam self-weight** | Removed by zeroing while unloaded — but only if it was genuinely still. |

**Q4.** Rank these for *your* setup, largest contributor first. Justify the top
one with a number, not just an assertion.

---

## Going further

**Linearity.** Repeat with several masses — say 100 g, 200 g, 500 g, 1 kg. Plot
measured strain against load. Beam theory says it is a straight line through the
origin.

**Q5.** Fit a line to your points. Does the slope match `6L/(E·b·h²)`? Does the
intercept pass through zero — and if not, what does a non-zero intercept tell
you about your zeroing?

**Q6.** Use the API directly to log data rather than reading numbers off the
screen:

```bash
while true; do
  curl -s localhost:8110/mm01/readings \
    | python3 -c "import sys,json,time; d=json.load(sys.stdin); print(time.time(), d['readings']['0'])"
  sleep 0.5
done | tee beam_data.txt
```
