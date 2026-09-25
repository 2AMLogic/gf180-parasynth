# M1A phase-cycle capture: does the Mini V3 darken near ψ ≈ 0?

**Diagnostic only.** This folder does not modify the frozen M1A reference
(`../manifest.json`, `../m1a-repeat-*.wav`, `../control-*.wav`), the scorer, the
patch, the engine or any tolerance. It answers the question PR #218 left open
(`../harmonic-diagnosis/README.md` §6).

## Pre-registration (committed before any capture was taken)

This section was committed on its own, before the capture or the analysis
tool had produced a single number. Its commit is the evidence of ordering.

### The question

The reference's oscillator 2 free-runs about 3.49 cents flat of the octave, so
the relative phase ψ (oscillator 2 minus twice oscillator 1, degrees of
oscillator 2) cycles every ~3.80 s. The reference was only ever observed at
ψ = 156°, −53° and 43°. In the model, odd partials h5/h7/h9/h11 swing 4.5–12.6 dB
with ψ and are darkest within about ±20° of 0°. Does the Mini V3 darken the same
way near 0°?

### Capture (Mini V3 only; the instrument of the frozen reference)

One held MIDI 36 note, 12 s gate, velocity and patch as the frozen reference,
through the existing renderer `measure_mono_m1a_reference.render(overrides, events, seconds)`.
Three conditions, two takes each:

| condition | overrides (same as the frozen controls) |
|---|---|
| `full` | none -- the complete M1A patch |
| `osc1_open` | `osc2_level=0, filter_cutoff=1, filter_contour=0` |
| `osc2_open` | `osc1_level=0, filter_cutoff=1, filter_contour=0` |

Take A starts the note at 0.1 s after instantiation. Take B starts it at 2.0 s,
roughly half a ψ cycle later, so the two takes see ψ at different times since
note-on. If ψ explains the level changes, the level-versus-ψ curves agree; if
something time-dependent does (envelope settling, a slow LFO), they do not.

### Preconditions -- each asserted at the point of use; any failure REFUSES

1. Plugin bundle version and binary SHA-256 equal the frozen manifest's identity.
2. The frozen reference phrase re-renders **bit-exactly** (all three repeats)
   in this host before any new take is trusted.
3. The existing reference-integrity qualification passes: 40 s held note,
   zero unprompted transients, and the injected-click control is detected.
4. Every take: finite, not clipped, non-silent; the MAD transient detector finds
   zero events in the held sustain, and finds the injected clicks in the same
   take (control); no run of ≥ 1 ms of exact digital zeros inside the gate; the
   isolated takes' 40 ms RMS varies < 0.5 dB across the sustain (no dropouts).
5. Parameter names and readbacks, before and after every render, as the
   renderer already enforces; the full settings dict of each condition equals
   the frozen manifest's recorded settings for that condition.
6. Octave: oscillator 1 within 5 c of 65.406 Hz; oscillator 2 within 10 c of
   twice that.
7. Sample rate 48 kHz and host block 16 samples, as the frozen manifest.
8. The isolated captures describe the full capture's oscillators: a take of
   both oscillators with the filter open equals the sum of the two isolated
   takes (residual ≤ −40 dB relative to the sum). Otherwise ψ from the isolated
   takes cannot be attributed to the full-patch take.

### Analysis (identical on both sides)

- Window: 250 ms (12 000 samples), the scorer's length and its 4-term
  Blackman-Harris coherent projection (`windowed_tone_amplitude`). Hop 50 ms.
  Windows lie wholly inside note-on + 1.0 s .. gate-off − 0.05 s.
- Level: `20·log10` of one partial's amplitude, dBFS (a full-scale sine reads
  0 dBFS). f0 per window is `refine_f0` on the full-patch window. Bin and zone
  averages are power means (mean of amplitude²) reported in dB.
- ψ per window from the isolated open-filter takes, with the #218 estimator
  (`relative_phase` logic: θ_j of oscillator 2 partial j against oscillator 1
  partial 2j, fitted to j·ψ; a window whose fit residual exceeds 2° is refused;
  more than 10 % refused windows REFUSES the take).
- Bins: 18 bins of 20° centred on 0°, ±20°, … 180°. Each needs ≥ 3 windows
  or the take is REFUSED for coverage.
- range(h) = max − min of the binned levels. dark(h) = power mean over
  |ψ| ≤ 20° minus power mean over |ψ| ≥ 60°; negative means darker near 0°.
- **Model, moving phase.** The model is rendered with the selected M1A patch
  and oscillator 2 detuned by the reference's **measured** octave offset from
  this capture (diagnostic only), held the same 12 s, with its own isolated
  open-filter renders for ψ, and analysed by the same code with the same
  windows. The model's measured drift must equal the reference's within 5 %,
  or it REFUSES. The static-phase curves of #218 are not used for the decision.

### Decision rule (h ∈ {h5, h7, h9, h11})

Evaluated in this order:

1. **Take agreement** (reference): for every h, |dark_A − dark_B| ≤ 1.0 dB and
   the largest per-bin |A − B| ≤ 1.5 dB. Otherwise **INCONCLUSIVE**.
2. **Model premise**: the moving-phase model has dark(h) ≤ −3 dB for at least
   3 of the 4. Otherwise **INCONCLUSIVE** -- the model's static darkening does
   not survive equivalent windowing, and neither branch applies.
3. **SUPPORTS-DRIVE**: for every h, the reference (takes pooled) has
   range(h) < 1.0 dB and |dark(h)| ≤ 1.0 dB. This supports investigating the
   model's filter-input level and drive; it does not uniquely prove drive.
4. **REDIRECT-TO-PHASE**: for at least 3 of the 4, the reference has
   dark_ref(h) ≤ 0.5 · dark_model(h) (at least half the model's darkening,
   same sign) and its darkest bin lies within |ψ| ≤ 40°. The model's odd
   partials are then phase-appropriate. This does **not** establish that
   permanently locked oscillators are appropriate; the next task goes to
   oscillator phase behaviour (free-running, detune).
5. Anything else is **INCONCLUSIVE**, with what would resolve it.

Mini V4 and Model D are not substitutes for the V3 reference and play no part
in this rule.

---

## Results (written after the capture; the rule above is unchanged)

**Verdict under the pre-registered rule: INCONCLUSIVE** (stopped at step 2).
It would be INCONCLUSIVE at any step order: step 3 fails on h7/h9/h11 ranges and step 4
matches 0 of 4 partials. **Descriptively**, the Mini V3 does **not** darken near ψ ≈ 0 the
way the model does. Its odd partials move 0.7–2.2 dB over the whole cycle, and are only
0.2–0.75 dB darker near 0°. The moving-phase model moves 9.8–10.4 dB. What fails is the
rule's model premise. Under moving phase, the model's h9 and h11 have several dips. Their
deepest dips are near +60°, not 0°, so the near-versus-far "dark" summary does not capture
them. See "What would resolve it".

Record: `report.json` (every window's ψ and h1–h12, binned curves, decision rows, control,
hashes). Figure: `odd-partials-vs-psi.png`. Capture record: `capture.json`.

### Preconditions asserted (all held on the recorded run; evidence in `capture.json`)

| # | precondition | evidence |
|---|---|---|
| 1 | plugin identity | Mini V3 3.12.0.3422, binary SHA-256 `6b11ae9c…`, Info.plist `f98d1a28…`: equal to the frozen manifest |
| 2 | host state reproduces the frozen one | all three frozen phrase repeats and both isolated controls re-rendered **bit-exactly** (dawdreamer 0.8.3, the frozen host version; a probe under the venv's dawdreamer 0.9.0 was also bit-exact) |
| 3 | licensed, no demo noise | repo integrity qualification: 40 s held note, 0 unprompted transients, 5 injected clicks detected |
| 4 | per take | 0 transient events (max detrended residual ≤ 0.16 dB against a 1 dB floor); 5 of 5 injected clicks detected in each take; longest exact-zero run inside the gate is 8–20 samples, all at the note-on (the 1 ms limit is 48); isolated RMS flat to 0.007 dB (osc 1) and 0.043 dB (osc 2) |
| 5 | settings | each condition's full settings dict equals the frozen manifest's, and names and readbacks were checked before and after every render |
| 6 | octave | osc 1 65.405 Hz (−0.03 c); osc 2 130.547 Hz (−3.52 c from the octave) |
| 7 | rate / block | 48 kHz / 16 samples |
| 8 | isolated takes describe the full take | both-open minus the isolated sum: −43.7 / −44.0 dB overall, −40.0 dB worst 2-period block; the injected-click control raises it to −20.4 dB |

### Relative phase

ψ was measured in 215 windows per take, with 0 windows refused. The worst fit residual was
0.03°, against the 2° limit. The drift was −0.26328 / −0.26329 Hz, an offset of −3.488 c
(#218 measured −3.49 c from the frozen controls). ψ moves 23.7° per 250 ms window, and
every 20° bin holds at least 8 windows. Take A's first window sits at ψ = +90° and take B's
at −45°, so the two takes reach each phase at a different time since note-on.

The model was rendered with detune₂ = 11.96512 semitones (−3.488 c). Before that, the same
render function reproduced the committed `m1a-model.wav` bit-exactly with the locked patch.
The model's ψ drift is −0.2575 Hz, 2.2 % below the reference because of the 24-bit
increment quantisation and the 2x path's LSB loss (#218 §3). That gives a rotation of
23.2° per window, within the 5 % limit. Model ψ came from its own isolated open-filter
renders (worst residual 0.80°).

### Odd partials against ψ (dBFS, power means over 20° bins; takes A / B)

| partial | ref range A / B | ref range pooled | ref dark | ref darkest bin | take diff: dark / worst bin | model range | model dark | model darkest bin |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| h3 | 0.38 / 0.38 | 0.38 | −0.04 | −60° | 0.01 / 0.01 | 4.46 | −1.42 | −60° |
| h5 | 0.71 / 0.70 | 0.70 | −0.21 | −20° | 0.01 / 0.03 | 10.42 | **−4.47** | −20° |
| h7 | 1.17 / 1.24 | 1.19 | −0.48 | −20° | 0.01 / 0.05 | 10.36 | **−4.01** | 0° |
| h9 | 1.67 / 1.63 | 1.65 | −0.71 | 0° | 0.03 / 0.18 | 9.79 | −1.45 | +60° |
| h11 | 2.18 / 2.18 | 2.17 | −0.75 | 0° | 0.07 / 0.28 | 9.99 | −0.94 | +60° |

h1 is flat on both sides (range 0.03 dB for the reference, 0.14 dB for the model), so these
ranges are also the ratio-to-fundamental ranges. "dark" is the power mean over |ψ| ≤ 20°
minus the power mean over |ψ| ≥ 60°.

**Take-to-take agreement:** the dark difference is ≤ 0.07 dB (limit 1.0) and the worst
per-bin difference ≤ 0.28 dB (limit 1.5). The takes agree, and they do so on different
time-since-onset trajectories. The level is a function of ψ, not of time.

**Cross-check (not part of the rule): even partials.** These are the interference terms, and
the two sides agree on them. The binned curves correlate 0.94–1.00 for h2–h10 (0.75 for h12),
the notches fall at the same ψ, and the ranges are similar (h2 25.2 vs 24.1 dB, h8 12.2 vs
12.1 dB). So ψ means the same thing on both sides, and the windows and drift are equivalent.
The odd partials differ; the even partials do not.

### The rule, step by step

1. Take agreement: **pass** (above).
2. Model premise, dark ≤ −3 dB on at least 3 of 4: **fail**. Only h5 (−4.47) and h7 (−4.01)
   qualify. h9 (−1.45) and h11 (−0.94) swing about 10 dB, but their deepest dips are at
   about +60° and in several other lobes, not concentrated near 0°. → **INCONCLUSIVE**.
3. (For information) SUPPORTS-DRIVE needs every reference range < 1.0 dB: h5 0.70 passes,
   but h7 1.19, h9 1.65 and h11 2.17 fail. Every |dark| is ≤ 1.0 (it passes that half).
4. (For information) REDIRECT-TO-PHASE needs dark_ref ≤ 0.5·dark_model on 3 of 4. It is met
   on **0 of 4**. For example, h5 is −0.21 against the required −2.23.

### What would resolve it

- The rule's "dark near 0" summary came from #218's **static** curves. Under equal moving-phase
  windowing, the model's h9 and h11 carry several dips (for h11: near −130°, 0°, +55°, +115° and +165°). The
  question that remains is about **magnitude**: 9.8–10.4 dB in the model against 0.7–2.2 dB in
  the reference. It is not about location. This capture has now been seen, so a magnitude rule
  cannot be pre-registered against it. The resolving test is a range-based rule (for example,
  model odd-partial range ≥ 4× the reference's on 3 of 4), fixed **before** a fresh capture
  under an independent condition. MIDI 43, or a different oscillator-2 level, would change the
  interference pattern without changing the question.
- The 1.0 dB flatness threshold is not met by the reference's own h9 and h11. The reference
  has a mild phase sensitivity of its own, growing with k: 0.38, 0.70, 1.19, 1.65 and 2.17 dB
  for h3–h11. A future rule should state its threshold relative to that measured spread, not
  as an absolute.
- What this capture does settle: #218's hypothesis (ii), "the reference darkens the same way
  near ψ ≈ 0", is not supported. Near 0° the reference is 0.2–0.75 dB darker, where the model
  is 4.0–4.5 dB darker (h5, h7). Its odd partials move about a fifth as much as the model's
  (0.7–2.2 dB against 9.8–10.4 dB). Those observations point the same way as SUPPORTS-DRIVE,
  but **the pre-registered rule does not return that verdict and this note does not claim it.**

### Known-answer control (`synthetic_control`; runs before every analysis)

Two ideal saws, with oscillator 2 at 2·f1 − 0.2634 Hz, and a **known** ψ-dependent gain on the
odd partials, g(ψ) = D·exp(−(ψ/25°)²) dB. The control is run through the full path. All cases
pass:

| case | expected → got |
|---|---|
| flat reference, dipping model (D = −10) | SUPPORTS-DRIVE ✓ |
| reference dips like the model | REDIRECT-TO-PHASE ✓ |
| model does not dip | INCONCLUSIVE ✓ |
| reference dips a third as much (D = −3) | INCONCLUSIVE ✓ |
| reference takes disagree (one take's odd partials −3 dB) | INCONCLUSIVE ✓ |
| dark(h5), from the construction alone | −8.046 → −8.046 dB ✓ |
| flat odd partials beside beating even partials | range 0.00005 dB (< 0.3) ✓ |
| drift | −0.2634 → −0.26340 Hz ✓ |

Each of these injected analysis defects turns the control red: ψ offset by 180°, and ignoring
take agreement. The precondition tests synthesise each known apparatus failure: clicks, a
dropout, digital silence, a sub-audio octave, a moving isolated level, a click hidden in the
both-open take, and isolated takes whose phase differs. Each must REFUSE.

### Wrong-then-right (this work)

Six incidents. All were caught by a refusal, a synthetic test or an injected control, and none
by inspection. **No published number was withdrawn.** All six were in the apparatus's click
detection, not in the measurement.

1. **5 ms click blocks at MIDI 36.** The synthetic clean-saw test flagged every period: a saw
   edge falls in one 5 ms block of three. Blocks were made period-synchronous.
2. **40 ms RMS flatness** rippled 2 dB on a steady 65 Hz saw (synthetic test). It now uses a
   four-period RMS.
3. **First capture REFUSED** (exit 2): take A's full patch showed "3 transient events" at
   1.47, 5.26 and 9.11 s. The spacing is the 3.8 s ψ period. It was a ~2 dB, ~0.7 s swell of
   the high band, and a deterministic render's MAD made 12 MAD only 1.3 dB. The fix is to
   detrend the block levels by a running median.
4. **Two detector variants measured and dropped.** A ~1 s detrend left a 2.6 dB residual swell.
   One-period cancellation does not cancel on the Mini V3 (−23 dB residual) and lost the
   injected clicks.
5. **Second capture REFUSED:** four-MIDI-36-period blocks detected only 4 of 5 injected clicks
   on oscillator 2 (0.65 dB). Blocks are now two periods of each take's own fundamental. On all
   six takes the clean residual is ≤ 0.16 dB and the injected clicks are ≥ 2.69 dB.
6. **Third capture REFUSED:** both-open at 8.65 s. With the filter open, the two saws' edges
   align once per ψ cycle. That take is now click-tested blockwise against the isolated sum
   instead, with its own injected-click control.

The fourth capture run held every precondition. The analysis ran once, and its first output
is the one reported.

### Scope and what was not done

- The frozen reference, the scorer, the patch, the engine and the tolerances are untouched.
  The model's detune is a diagnostic render only.
- Mini V4 and Model D: **not captured**. The corroboration was optional, and it is not cheap.
  Neither has an existing patch map for this rig. Both would need their own name and readback
  pins and their own octave and licence checks. In any case they could not enter the decision.

## Reproduce

```sh
/opt/homebrew/anaconda3/bin/python3 tools/capture_m1a_phase_cycle.py   # ~2 min; refuses unless every precondition holds
.venv/bin/python tools/analyse_m1a_phase_cycle.py                      # ~2 min; control, model render, rule
.venv/bin/python tools/analyse_m1a_phase_cycle.py --control            # known-answer control only
.venv/bin/python -m pytest -q tools/test_capture_m1a_phase_cycle.py tools/test_analyse_m1a_phase_cycle.py
```

Test verdicts are recorded by `tools/run_all.py` in `run_all.json`.
