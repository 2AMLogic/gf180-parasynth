# 0017: polyBLAMP on the shark-tooth's corner — the second discontinuity we were not correcting

- **Status**: proposed
- **Date**: 2026-09-26
- **Decided by**: voice agent, issue #48 finding 1, from Esqueda, Bilbao and Välimäki (ISMRA 2016, §3, figs 4–5) and the measurements in `model/test_moog_acceptance.py` and `tools/measure_shark_blamp.py`

## Context

`OscFx.render`'s shark-tooth is the junction of R030 and R031 on drawing 1448 —
10/57 sawtooth plus 47/57 triangle (DR 0012, `docs/minimoog-reference.md` W3) —
and it was band-limited by mixing an **already PolyBLEP-corrected saw** with a
**plain triangle**. That is correct for one of its two alias sources and silent
about the other:

- the saw share **steps** at the wrap: an amplitude discontinuity, and BLEP is
  exactly the correction for it;
- the triangle share **corners** twice per cycle — at the valley (phase 0) and
  the peak (half a cycle): a **slope** discontinuity, which a step correction
  cannot see at all, because the signal is continuous there.

Esqueda, Bilbao and Välimäki analyse this specific waveform and say so directly:
it has both an amplitude and a slope discontinuity, so a generator needs **BLEP
*and* BLAMP**. `model/reference_voice.py` had been *printing* that gap for
months ("we have PolyBLEP only") without closing it.

Two things this is **not**. It is not the pitch-dependent aliasing degradation
of issue #61 — that was traced to a decimation-filter gap on the 2× path, whose
fix is opt-in and scoped to `saw` and the rectangles, so `shark` never saw any
of it. Decimation and BLEP/BLAMP are different alias sources and one does not
substitute for the other. And it is not a change to the 10/57 divider, which is
an independently verified fact and is untouched here.

## The decision

### 1. The residual is the integral of the one we already have, not a new kernel

`blep_fx` is, written against `x` = a sample's time from the discontinuity in
samples,

```
B(x) =  (1 + x)^2    for -1 <= x < 0          (subtracted from a step of -2)
     = -(1 - x)^2    for  0 <= x <  1
```

Integrating once — a slope discontinuity is the integral of a step — gives the
two-point ramp residual

```
R(x) = (1 - |x|)^3 / 3      for |x| <= 1, and 0 outside
```

a non-negative bump peaking at 1/3. This is the published two-point polyBLAMP
residual (the paper's per-sample `d^3/6` form is this with the factor of 2 from
the slope *change* taken out). A naive signal whose slope jumps by `ds` per
sample gains `(ds/2) * R(x)`. For our triangle `_tri_fx` reads `ph >> 7`, so
|slope| = `inc/128` Q1.15 LSB per sample and `ds/2 = inc/128` at each corner:
**the valley is raised and the peak lowered by the same non-negative residual.**

Writing it as the integral of the existing kernel is the whole economy of the
choice: no new table, no new window, and the same `s = 1 - |x|` word PolyBLEP
already forms from the note-on reciprocal is reused unchanged.

### 2. Fixed point from the start, because the RTL has to be bit-exact against it

The existing PolyBLEP is integer end to end and `rtl-sketch/verify_voice.py`
compares model and RTL sample for sample. A float prototype ported afterwards
would have made every rounding decision twice, so the model is *defined* by the
integer expression:

```
BLAMP_THIRD = 21845                       # Q0.16 approximation of 1/3
m3    = (inc * BLAMP_THIRD) >> 15         # |slope|/3, 8 fraction bits below a Q1.15 LSB
s     = 65536 - ph/inc  in Q0.16          # PolyBLEP's own window word
s3    = (((s * s) >> 16) * s) >> 16       # Q0.16 of (1 - |x|)^3
blamp = (m3 * s3) >> 24                   # Q1.15 LSBs, always >= 0
```

`BLAMP_THIRD` is a **constant multiply, not a divider**. The model performs the
same multiply the RTL does, so "exactly 1/3" never enters the bit-exactness
question; the kernel's agreement with the closed form is a separate, measured
claim: worst 1.08 LSB over every phase in the window at note 96 and 1.15 LSB at
note 108, which is floor truncation and nothing else
(`test_the_blamp_residual_is_the_cubic_the_derivation_says_it_is`). `m3` is one
multiply per shark oscillator per frame; `s3` and the scaling are two more per
*active* window.

### 3. Four windows, and the second pair lands on the peak

`rtl-sketch/voice_dp.v` already walks four PolyBLEP windows for the rectangles,
where windows 2 and 3 use the phase shifted by the duty point to reach the
second **edge**. The shark-tooth now uses the same machinery with `dutyv` set to
half a cycle, so windows 2 and 3 reach the second **corner**. Three states are
added (`S_SKM` for `m3`, `S_W3` for the cube, `S_W4` for the scaling) and the
shark mix takes the band-limited triangle instead of `triv`.

**Measured cost**: worst-case frame 148 → **162 cycles** of the 248 the chip
has, mean 86.4 → 91.9, on `verify_voice --only waves3` (which includes three
simultaneous shark-tooths at note 108). One multiplier, one divider, no new
ROM.

### 4. What is deliberately NOT corrected

The plain `tri` shape keeps its uncorrected corners. Its harmonics fall off as
1/n², so its alias energy is far below the shark-tooth's, and
`test_the_model_d_waveform_set_is_complete` has always exempted it on exactly
that ground. Extending BLAMP to it is a separate question with its own cost in
cycles and its own measurement, and folding it in here would have re-baselined
every `tri` number in the suite for no measured need.

## Measured consequence

`tools/measure_shark_blamp.py` (the instrument, committed with its own ground
truth in `tools/test_measure_shark_blamp.py`). The **before** arm is
`BLAMP_THIRD` forced to 0, which makes every residual truncate to zero and is
therefore the pre-#48 expression *exactly* — one implementation A/B'd, not two
copies compared.

| note | f0 | inharmonic, BLEP only | + BLAMP | gain | headroom over the estimator's own floor |
|------|------|------|------|------|------|
| 57 | 220 Hz | −52.30 | −52.38 | 0.08 dB | 84.3 / 84.2 dB |
| 69 | 440 Hz | −48.89 | −49.21 | 0.33 dB | 98.6 / 98.3 dB |
| 81 | 880 Hz | −45.28 | −46.42 | 1.14 dB | 97.3 / 96.3 dB |
| 93 | 1760 Hz | −39.64 | −42.70 | 3.06 dB | 100.7 / 97.8 dB |
| 105 | 3520 Hz | −30.75 | −37.07 | **6.33 dB** | 117.7 / 111.5 dB |

Four octaves, so the pitch dependence is a trend and not two points, and it is
the pitch dependence that identifies the corner as the source: a corner's alias
energy grows with the number of its images that fold. `foldback_alias_db` — a
different estimator, reading only the predicted image bins — agrees to 0.01 dB
on every row where it can answer, and **refuses** note 57 outright because the
images there collide with real harmonics.

**The estimator's own floor is read per row**, which is the discipline #61 had to
learn the hard way (a Hann-window floor hid a real 3 dB effect for a whole
comment thread). Every row above clears its measured floor by more than 84 dB;
the tool reports `REFUSED` rather than a number for any row that does not.

**The shape does not move.** h2..h7 at 110 Hz shift by less than 0.05 dB and
still match the closed form of 10/57 saw + 47/57 triangle to 0.1 dB, so the
divider and the correction remain independent facts
(`test_the_blamp_does_not_move_the_shark_tooths_harmonics`).

## The controls, and what each one catches

| control | what it does | what turns red |
|---|---|---|
| `BLAMP_THIRD = 0` | DR 0017 never happened | `test_control_the_shark_tooths_triangle_left_uncorrected` |
| `BLAMP_THIRD` negated | the two corners swapped — sharpened, not rounded | note 105 goes to **−29.09 dB**, *worse than no BLAMP at all*; `test_control_the_shark_tooths_blamp_with_its_two_corners_swapped` |
| `INJECT_BUG_VOICE_SHARK_BLAMP_SIGN` | the same defect in the RTL | `verify_voice --only waves3 --inject SHARK_BLAMP_SIGN` fails, as required |
| the pre-#48 RTL against the new model | the RTL half is load-bearing | 1451 of 2880 samples differ |

The negated-kernel control is the one that matters: a BLAMP with its signs
swapped *looks* like a correction from every angle except the measurement, and
it makes the instrument measurably worse.

## Wrong before it was right

The scale of the residual was swept rather than argued about
(0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0 × `BLAMP_THIRD`). **The empirical
optimum is near 1.25, not the derived 1.0** — at note 105, −37.07 dB derived
against −37.91 dB fitted, and the curve is flat within 0.9 dB from 1.0 to 1.5.
The derived value is what ships anyway. A two-point polynomial residual is an
approximation to an infinitely long kernel and the fitted extra almost certainly
also buys a partial cancellation against the saw share's own BLEP residual,
which is a coincidence of this particular 10/57 mix and not a principle. Fitting
0.84 dB out of our own estimator, on our own model, is the exact shape of the
failure `docs/failure-modes.md` is about: **an estimator calibrated on our own
model is not validated.** The sweep is recorded here because the number someone
will otherwise re-derive in six months is 1.25.

## Consequences

- `spec/NUMERIC-CONTRACT.md` is at **revision 12**, whose one normative change
  is this: 6.6 gains **6.6.5**, 6.4's shark-tooth row is marked naive-only, and
  the sentence saying the shark-tooth "needs no second correction" is replaced.
  Any other implementation of shape 5 must carry the BLAMP too, and must use the
  constant multiply by 21845 rather than an exact division by 3, or it will not
  be bit-exact.
- `model/reference_voice.py`'s shark-tooth target text no longer describes a gap
  we have.
- Finding 4 of issue #48 (Pekonen et al.'s measured-waveform IIR coloration) is
  **explicitly deferred, not dropped** — it is a post-oscillator stage evaluated
  against harmonic-amplitude measurements, independent of this, and its fitted
  parameters come from a *Voyager* and are not Model D ground truth.
