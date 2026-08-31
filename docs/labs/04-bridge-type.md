# Lab 4 — Bridge configuration

**Goal:** understand quarter, half and full bridge, and see what a mismatched
setting looks like. **Hardware needed:** MM01 with a quarter-bridge gage.

> Nothing in this lab can damage the instrument. The failure you will produce is
> an electrical one, and it clears as soon as you set the configuration back.

---

## The three configurations

A Wheatstone bridge has four arms. How many of them are active gages is what
distinguishes the configurations:

| | Active gages | Completion by the MM01 | Relative sensitivity |
|---|---|---|---|
| Quarter | 1 | 3 resistors | 1× |
| Half | 2 | 2 resistors | 2× |
| Full | 4 | none | 4× |

When you choose quarter or half bridge, the MM01 **energises internal
completion resistors** to fill the empty arms. When you choose full bridge it
**switches them off**, because your own bridge supplies all four arms.

That switch is a single command:

```python
def set_bridge_excitation(dev, bridge):
    if bridge == BRIDGE_FULL:
        disable_qb_excitation(dev)
    else:
        enable_qb_excitation(dev)
```

## Predict

You have a **quarter bridge** wired — one gage, three arms supplied by the MM01.

**Q1.** If you tell the app the setup is a **full bridge**, the MM01 will switch
off its completion resistors. What will be left connected to the amplifier
input, and what do you expect the reading to do?

## Measure

1. Start in **Quarter bridge**. Note a stable reading.
2. Switch to **Full bridge**. Watch the reading.
3. Switch back to **Quarter bridge**.

**Q2.** What did the reading do in step 2? Did it return to normal in step 3?

You should have seen the reading jump to an extreme value and stay there. With
the completion resistors switched off and only one real gage connected, three
bridge arms are open circuits. Nothing defines the amplifier's input voltage, so
it saturates at the end of its range.

**Q3.** The raw ADC value at saturation is −8,388,608 counts. Where does that
number come from? (The ADC is 24-bit signed — what is the most negative value it
can express?)

**Q4.** A saturated reading is sometimes called "railed". Why is that a *useful*
failure compared with the wrong-gage-factor failure in Lab 3?

## Explain

Notice which way the sensitivity table runs. A full bridge is four times as
sensitive as a quarter bridge, for the same strain, because all four arms
contribute instead of one. That is why load cells — where sensitivity matters
more than convenience — are always full bridges, and why student work is
usually quarter bridge, where you only have to bond one gage.

**Q5.** Half bridge is often used with the second gage bonded to an *unstrained*
piece of the same material, held at the same temperature. What problem does that
solve? (Look back at Q6 of Lab 2.)

---

## Going further

The internal completion is switched through a general-purpose I/O line — the
`USBCase.Gpio` command in `app/mm01_bridge/constants.py`. Setting the gain uses
a different mechanism: an I²C write to a digital potentiometer. Find both in
`app/mm01_bridge/protocol.py`.
