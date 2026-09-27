# D02A on the integrated RTL

The first Drums row on `docs/scorecard/BOARD.md` whose numbers came off the
chip's pins rather than out of `model/drums_fx.py`. Issue #290, one of the four
`integrated-rtl` anchors issue #94 asks for (one drum, one mono, one filter,
one ensemble); the mono one, `M5A`, landed first.

## Why D02A

Three Drums cases were passing when this was built (`D02A` snare 0.58, `D09A`
claves 0.61, `D16A` closed hat 0.96). **`D02A` has the largest margin**, so a
disagreement between the engines would have been attributable to the chip
rather than swamped by a case that was already near its limit. It is also the
only one of the three whose sound uses BOTH drum buses -- the snare's body
resonator and its noise -- so the chip's `DVOL`/`BVOL` mix, the join the
integrated anchor exists to exercise, is actually loaded.

(#94's own text suggested `D01A`, the bass drum, "ideally a passing one (worst
0.70)". That figure is stale: `D01A` is `⚠️ no verdict (invalid: decay)` and
has been for some time.)

## What was run

```
python3 rtl-sketch/verify_synth_top.py --drum-solo SD --simulator verilator \
        --outdir rtl-sketch/build/drum-d02a --wav-out /tmp/d02a-i2s.wav
python3 tools/score_drum_i2s.py --case D02A --wav /tmp/d02a-i2s.wav \
        --verification docs/scorecard/drum-d02a-i2s/verify-drum-solo-sd.log
python3 tools/compare_drum_i2s_candidate.py \
        docs/scorecard/drum-d02a-i2s/fixed-model-twin-D02A.json \
        docs/scorecard/results/D02A.json \
        --out docs/scorecard/drum-d02a-i2s/delta-vs-fixed-model.json
python3 tools/scorecard.py --readme --markdown
```

Every register write -- the kit image, the two drum-bus gains, the accent and
the stop bit -- goes over the SPI **pins** as DR 0007 revision 2 frames, and
every scored sample is decoded from BCLK/LRCLK/SDATA the way a DAC does. The
voice is silent because `VOL` is 0 out of reset and no gate is ever sent, so
the wire carries the drum mix and body buses under the scorecard's
`DVOL = BVOL = 0.45` with `ROUTE = 0`, which is exactly what
`run_case.render_drum_solo` computes.

| file | what it is |
|---|---|
| `verify-drum-solo-sd.log` | the whole-chip run. PASS: 107,409 I2S periods, every one identical to the model |
| `sd-solo-i2s.wav` | the scored window, 105,600 samples, straight off the decode |
| `fixed-model-twin-D02A.json` | the board row this replaced, preserved |
| `delta-vs-fixed-model.json` | what the overwrite changed, per property, and what it is attributable to |
| `controls-run_all.{log,json}` | injected defects run against the new stimulus |

## The result, and the disagreement

Both engines **pass**. No property's verdict flips. All three differ slightly:

| property | fixed-model | integrated-rtl | difference | tolerance | in tolerances |
|---|---:|---:|---:|---:|---:|
| Body/noise balance | -5.0021 dB | -5.2127 dB | -0.2106 dB | 3.0 | -0.070 |
| attack | 4.2292 ms | 4.2917 ms | +0.0625 ms | 2.1655 | +0.029 |
| noise decay | 71.1033 ms | 70.7201 ms | -0.3832 ms | 34.7293 | -0.011 |

worst: 0.58 → 0.65.

**That difference is the stimulus, not the chip, and it is proven rather than
argued.** The decoded window is bit-exact -- 0 of 105,600 samples -- against
the fixed model struck in the frame the SPI link actually put the strike in
(782). It differs from the board's own render (11,411 of 105,600 samples, max
4522 LSB) only because that render strikes in frame 480 and **the drum noise
LFSR free-runs**: a snare struck at a different moment is a different waveform
even when the engine producing it is bit-identical. The comparison tool carries
both comparisons and says which of the two a delta belongs to; it can also say
"the estimator", for the case where the samples are identical and the numbers
are not.

So the readable claim is narrow and true: **on this case the chip and the model
are the same engine**, and the residual spread in the three numbers is how much
this case's metrics move when the snare's noise phase moves -- roughly 0.07 of
a tolerance at worst. That is a fact about the board's numbers that no
fixed-model row could have shown.

## Controls

`controls-run_all.log`. Two of three injected defects are caught by this
stimulus and **one is not**:

| injected defect | outcome |
|---|---|
| `DRUM_LFSR_TAP` | CAUGHT -- first wire mismatch at period 783, core already wrong |
| `I2S_SHIFT` | CAUGHT -- core right, wire wrong: the defect is in `i2s_tx` |
| `DRUM_ENV_FLOOR` | **NOT caught** -- PASS with the defect compiled in |

`DRUM_ENV_FLOOR` lives in the `dec = 0, level > 0` corner and one snare solo
never drives an envelope there. **This is a scoring stimulus, not a coverage
bench**, and the distinction is recorded here and in `drum_solo_script`'s
docstring rather than left for a reader to assume the other way. The coverage
bench is still `verify_synth_top` with no `--drum-solo`, which strikes all
eleven circuits, resets both pages while they sound, and REFUSES if its
stimulus stopped reaching any of that.

## Wrong then right

One measurement here was wrong before it was right, caught by a control rather
than by inspection.

The controlled comparison first read **6659 of 7200 samples differing, max 2414
LSB** -- which looks exactly like a broken drum path, and would have been
published as one. It was a one-period alignment error: `i2s_tx` holds a frame's
word and sends it in the NEXT period, so the wire runs one period behind the
core's sample stream, while the strike frame is found in the core's timeline.
The lag is now MEASURED from the model's own two streams by
`verify_synth_top.i2s_lag()` and refused if they are not a pure delay of each
other, instead of assumed to be zero. With it the same comparison is 0 of
105,600.

The tell was that the *other* comparison, against the board's own render, had a
plausible magnitude (max 4522 LSB) while the controlled one -- which had no
business differing at all -- was almost as large. A difference that should have
been zero and was not is the cheapest defect to notice and the easiest to
explain away.
