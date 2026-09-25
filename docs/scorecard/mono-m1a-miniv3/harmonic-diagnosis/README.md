# M1A harmonic-shape diagnosis: persistent or phase-dependent?

**Diagnosis only.** Nothing changed in the patch (`m1a-gain-minus4db-v2`), the engine, the
frozen Mini V3 reference, the estimators, the scorer (`m1a-envelope-score-v3`), the windows or
the tolerances. The official score reproduces exactly: every scored ratio error below equals
`../../results/M1A.json` to within 1e-6 dB. Harmonic shape still **fails at 19.82331 dB**.

**Verdict: it is a mix, and the evidence cannot yet separate the parts well enough to support a
sound change.** The worst errors are driven by the relative phase of the two oscillators. The
reference's oscillators run freely and are detuned; the model's are locked. In the model this
phase also moves the *odd* partials by up to 11 dB through a nonlinearity. Once that phase is
matched, a smaller odd-partial deficit (−1.4 to −3.2 dB on the MIDI 36 pair) remains. Near the
reference's cancellation notches the even partials are **unresolved**. Following the stop
condition, this note does **not** recommend a sound change. It names one minimal measurement
that would settle the question (§6).

Instrument: `tools/diagnose_m1a_harmonic_phase.py`. Tests: `tools/test_diagnose_m1a_harmonic_phase.py`.
Record: `report.json`. It holds the source and input hashes, every render's PCM hash, the phase
measurements, the full table, the static-phase sweep and the control.

## Conventions

- **Level:** `20·log10` of one partial's amplitude, measured by the scorer's own primitive
  (`windowed_tone_amplitude`, a Blackman-Harris coherent projection). A full-scale sine reads 0 dBFS.
- **Ratio:** the scorer's `harmonic_signature` value, partial amplitude over fundamental amplitude, in dB.
- **Averages:** where levels are averaged, the mean is taken over amplitude squared and reported
  on the same dB scale.
- **Window:** onset + 0.30 s to onset + 0.55 s, which is the scorer's window. The relative phase
  ψ is referenced to the window centre.
- **ψ (relative phase):** oscillator 2's phase minus twice oscillator 1's phase, in degrees of
  oscillator 2. At mix partial 2j the relative phase is θ_j = j·ψ.
- **Note history:** event 0 is MIDI 36 at 0.1 s, the first note after reset. Event 1 is MIDI 43
  at 2.1 s. Event 2 is MIDI 36 at 4.1 s, played 1.4 s after MIDI 43 was released.

## 1. The repeated MIDI 36 notes first

The two notes have the same pitch, patch and window. Only the oscillator phase state and the
note history differ.

| | fundamental dBFS (ev0 → ev2) | odd partials h3–h11, change | even partials h2–h12, change |
|---|---|---|---|
| reference | −25.19 → −25.17 | ≤ 0.72 dB | h2 +13.16, h4 −2.08, h6 −2.83, **h8 −19.69**, h10 −9.48, h12 +5.50 |
| model | −26.05 → −26.04 | 0.48–3.15 dB | ≤ 0.87 dB |
| isolated reference oscillators (open filter) | — | each contributor ≤ 0.0013 dB | each contributor ≤ 0.0013 dB |

The fundamental's absolute level does not move on either side (≤ 0.02 dB). So the ratio swings
between the repeats are changes in the partials, not in the fundamental. The model's fundamental
sits 0.86–1.22 dB below the reference's at every event. That offset is the separately scored
Gain property, and it cancels in the ratios.

## 2. Reference oscillators: free-running and detuned, not reset at note-on

The isolated controls give one relative phase per event: θ_j equals j·ψ to within 0.03° for
j = 1..6. The measured values are:

| event | f2 − 2·f1 | octave offset | ψ measured | free-running prediction from event 0 | reset-at-note-on prediction |
|---|---:|---:|---:|---:|---:|
| MIDI 36 @0.1 | −0.26340 Hz | −3.4896 c | +156.22° | (anchor) | (anchor) |
| MIDI 43 @2.1 | −0.39462 Hz | −3.4892 c | −53.41° | −53.54° (error 0.12°) | — |
| MIDI 36 @4.1 | −0.26346 Hz | −3.4903 c | +42.61° | +42.40° (error 0.20°) | +156.22° (**error 113.6°**) |

The free-running prediction uses only ψ at event 0 and the independently measured frequencies,
with the pitch switching at each note-on. It lands within 0.2°. The reset hypothesis misses by
113.6°. This pins down how the oscillators behave. It does not by itself show that ψ causes the
level changes in the full nonlinear path; three events cannot establish that.

## 3. The model's phase state is locked

The model's registers hold an exact octave (inc 22861/45722 at MIDI 36 and 34253/68506 at
MIDI 43), and both accumulators start at 0 from reset. The 2x path advances each accumulator by
`(inc//2)*2`, so oscillator 1's odd increment loses one LSB. That leaves a residual drift of
+2 LSB per sample, or +0.0057 Hz. The integer arithmetic predicts ψ = 0.88°, 5.00° and 9.12° at
the three events. The model's own isolated oscillators, rendered with the filter open, measure
0.95°, 5.08° and 9.19°. The model's even partials therefore sit near fully constructive
interference on every note.

## 4. Controlled diagnostic: the model's full path against ψ

These renders are diagnostic only. The only knob set is oscillator 2's starting accumulator
phase. Before any number was used, the offset-0 render reproduced the committed `m1a-model.wav`
PCM and the committed `detune-diagnostic.wav` bit-exactly. The static-phase sweep used 16
starting phases on the selected patch. In the matched renders, the phase was set so that the
model's ψ equals the reference's **measured** ψ at each event centre. They use the existing
−3.49 c detune diagnostic so that the phase also rotates inside the window. The matched ψ was
checked by isolated renders, with a worst error of 0.17°.

This is **not** a proposed fix. It does not select a phase to fit a notch: ψ comes from the
isolated recordings, not from the error. Detuning is not proposed either; the earlier
static-detune candidate lost six harmonic passes.

What the sweep shows, with every contributor amplitude held fixed:

- **Model odd partials are phase-sensitive by 4.5–12.6 dB.** The ranges are h3 4.5, h5 11.0,
  h7 10.9, h9 12.6 and h11 11.2 dB. A linear sum cannot do this, because oscillator 2 contributes
  nothing at odd partials. The mixer's peak moves from 31435 to 21261 (Q1.15) with ψ, while the
  mixer never clips (0 saturated samples). The level-dependent stage is the ladder input. Its
  phase is also level-dependent: isolated oscillators measured *through* the 1056 Hz patch filter
  scatter by 14.6° from j·ψ.
- **The model's locked ψ ≈ 1° sits at the dark end of that range.** h7 = −26.8 dB is the sweep
  minimum, and h9 = −28.5 dB is also at the dark end.
- The fundamental's absolute level moves only 0.14 dB across the sweep.

## 5. Per-note, per-partial table

The scored error is the model-minus-reference ratio error (the official quantity). The next
column is the same error at the matched phase. The phase term is the difference between the two.
Bold means more than the 1 dB screening limit. The "lives in" column says whether the ratio error
is carried by the partial's own absolute level or by the fundamental's.

| MIDI @ s | k | model dBFS | ref dBFS | scored ratio err | at matched phase | phase term | model ratio over 16 static phases | lives in |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 36 @0.1 | 1 | -26.05 | -25.19 | (fundamental; abs err -0.86) | | | -26.16 to -26.02 dBFS | |
| 36 @0.1 | 2 | -26.09 | -38.67 | **+13.44** | -0.81 | +14.25 | -27.5 to -0.0 | partial |
| 36 @0.1 | 3 | -36.99 | -34.53 | **-1.61** | +0.41 | -2.01 | -13.4 to -8.9 | partial |
| 36 @0.1 | 4 | -34.28 | -31.87 | **-1.55** | -0.35 | -1.20 | -30.1 to -6.3 | partial |
| 36 @0.1 | 5 | -46.09 | -39.63 | **-5.60** | **-2.01** | -3.60 | -23.4 to -12.4 | partial |
| 36 @0.1 | 6 | -40.08 | -40.25 | **+1.03** | **-1.75** | +2.79 | -34.1 to -11.8 | partial |
| 36 @0.1 | 7 | -52.86 | -43.74 | **-8.26** | **-3.18** | -5.08 | -26.8 to -15.9 | partial |
| 36 @0.1 | 8 | -43.19 | -42.91 | +0.58 | **-2.15** | +2.72 | -39.9 to -16.1 | within tolerance |
| 36 @0.1 | 9 | -54.51 | -47.68 | **-5.97** | **-1.39** | -4.58 | -32.4 to -19.8 | partial |
| 36 @0.1 | 10 | -46.85 | -45.15 | -0.84 | **-1.69** | +0.86 | -44.3 to -19.9 | within tolerance |
| 36 @0.1 | 11 | -57.62 | -52.45 | **-4.31** | **-1.72** | -2.58 | -34.6 to -23.4 | partial |
| 36 @0.1 | 12 | -50.07 | -58.36 | **+9.16** | **-5.03** | +14.19 | -37.7 to -23.2 | partial |
| 36 @4.1 | 1 | -26.04 | -25.17 | (fundamental; abs err -0.87) | | | -26.16 to -26.02 dBFS | |
| 36 @4.1 | 2 | -26.15 | -25.50 | +0.22 | -0.46 | +0.68 | -20.2 to -0.0 | within tolerance |
| 36 @4.1 | 3 | -36.51 | -34.52 | **-1.11** | +0.10 | -1.21 | -13.4 to -8.9 | partial |
| 36 @4.1 | 4 | -34.49 | -33.93 | +0.32 | **-2.66** | +2.98 | -28.5 to -6.4 | within tolerance |
| 36 @4.1 | 5 | -44.36 | -39.43 | **-4.06** | **-1.43** | -2.63 | -23.9 to -12.5 | partial |
| 36 @4.1 | 6 | -40.42 | -43.06 | **+3.52** | **-2.54** | +6.06 | -31.1 to -11.7 | partial |
| 36 @4.1 | 7 | -50.21 | -43.76 | **-5.58** | **-2.66** | -2.92 | -26.9 to -15.8 | partial |
| 36 @4.1 | 8 | -43.63 | -62.58 | **+19.82** | **+10.52** | +9.30 | -27.4 to -16.6 | partial |
| 36 @4.1 | 9 | -51.92 | -48.36 | **-2.68** | **-1.56** | -1.12 | -28.9 to -20.4 | partial |
| 36 @4.1 | 10 | -47.50 | -54.62 | **+7.99** | **-7.31** | +15.30 | -35.5 to -19.7 | partial |
| 36 @4.1 | 11 | -54.47 | -53.16 | -0.44 | **-2.06** | +1.62 | -38.7 to -23.2 | within tolerance |
| 36 @4.1 | 12 | -50.93 | -52.84 | **+2.79** | **-8.68** | +11.47 | -40.8 to -23.3 | partial |
| 43 @2.1 | 1 | -26.26 | -25.05 | (fundamental; abs err -1.22) | | | -26.35 to -26.20 dBFS | |
| 43 @2.1 | 2 | -26.65 | -25.86 | +0.42 | -0.32 | +0.75 | -23.1 to -0.3 | within tolerance |
| 43 @2.1 | 3 | -38.03 | -35.10 | **-1.72** | **-5.66** | +3.93 | -15.9 to -9.2 | both |
| 43 @2.1 | 4 | -36.62 | -36.09 | +0.69 | **-3.22** | +3.91 | -47.4 to -7.4 | within tolerance |
| 43 @2.1 | 5 | -50.67 | -40.56 | **-8.90** | -0.05 | -8.85 | -26.9 to -13.3 | both |
| 43 @2.1 | 6 | -41.91 | -52.74 | **+12.05** | **+2.18** | +9.86 | -30.9 to -14.5 | both |
| 43 @2.1 | 7 | -52.50 | -45.04 | **-6.25** | **+1.01** | -7.26 | -29.3 to -19.0 | both |
| 43 @2.1 | 8 | -46.58 | -52.77 | **+7.41** | **-5.66** | +13.07 | -41.2 to -19.8 | both |
| 43 @2.1 | 9 | -56.12 | -50.15 | **-4.76** | +0.85 | -5.61 | -37.2 to -23.8 | both |
| 43 @2.1 | 10 | -51.66 | -51.09 | +0.65 | **-3.00** | +3.65 | -44.3 to -25.0 | within tolerance |
| 43 @2.1 | 11 | -58.64 | -55.91 | **-1.51** | +0.84 | -2.35 | -42.5 to -27.8 | both |
| 43 @2.1 | 12 | -55.91 | -53.70 | **-1.00** | **-2.16** | +1.16 | -50.9 to -28.7 | both |

At MIDI 43 the fundamental's own absolute error (−1.22 dB) is just outside 1 dB, so those cells
read "both". Its partial terms still dominate.

**Classification over the MIDI 36 repeat pair** (`report.json: repeat_pair`). In every row, the
isolated contributors' own energy changes by ≤ 0.0013 dB between the two notes.

| row | label | evidence |
|---|---|---|
| h2, h3 | **phase-sensitive in a controlled diagnostic** | scored +13.44/+0.22 and −1.61/−1.11 → within ±0.81 dB at the matched phase |
| h5, h7, h9, h11 | **phase-sensitive, with a persistent remainder** | at the matched phase, the same sign at both notes: h5 −2.01/−1.43, h7 −3.18/−2.66, h9 −1.39/−1.56, h11 −1.72/−2.06. The remainder is "persistent over the observed events" only at MIDI 36: at MIDI 43 the matched odd residuals are −0.05, +1.01, +0.85 and +0.84 |
| h6 | phase-sensitive, with a persistent remainder | the scored error is positive (+1.03/+3.52), while the matched residual is negative (−1.75/−2.54) |
| h4, h8, h10, h12 | **unresolved** | the matched residual changes sign or size between the notes. h8 goes from −2.15 to **+10.52**: the reference's −62.58 dBFS at 4.1 s is a notch between two contributors whose isolated powers agree within 0.12 dB (existing cross-term −18.54 dB), and its depth depends on the post-filter amplitude balance. Summing isolated recordings is not the input to the nonlinear filter, so the notch depth cannot be attributed from these recordings |

The worst scored error, h8 at 4.1 s (19.82 dB), is therefore *unresolved*: the phase state
accounts for 9.30 dB of it and 10.52 dB remains.

## 6. Recommendation: no sound change yet. One minimal discriminating measurement

**What cannot be told apart.** The reference was only observed at ψ = 156°, −53° and 43°. At
exactly those phases the model's odd partials barely differ either: h7 at the matched phase is
−21.73, −18.98 and −21.25 dB, and on the sweep −21.5 dB at both 158° and 46°. The model's odd-
partial dip is concentrated within about ±20° of ψ = 0, which is where the model is locked and
where the reference was never observed. The reference's 0.72 dB odd-partial stability therefore
does **not** show that the reference lacks the model's phase sensitivity. Two explanations remain:

- **(i)** The model's ladder input stage is too nonlinear at this patch level. That would be a
  persistent model property, shown in the model by the 11 dB odd-partial swing and the
  level-dependent filter phase.
- **(ii)** The reference darkens the same way near ψ ≈ 0. Then the odd deficit is the model's
  locked phase state, and single-instance ratio errors measure phase luck, not timbre.

The existing recordings cannot discriminate between them. Event 2's gate only reaches ψ ≈ 17°.
The onset of MIDI 43 passes ψ ≈ 7°, but only during the attack and the filter-envelope transient,
where the model's provisional envelopes confound any comparison.

**Minimal measurement.** One additional Mini V3 capture with the frozen M1A patch and the existing
apparatus (`tools/measure_mono_m1a_reference.py`; no new plugin):

- a single held MIDI 36 note of at least 4.0 s (one full ψ cycle at −0.2634 Hz is 3.80 s);
- the two open-filter isolated controls in the same deterministic session, so ψ(t) is measured
  and not assumed.

Analyse it with this tool's estimators in the scorer's 250 ms window, hopped through the sustain.
Plot the reference's h3–h11 level against the measured ψ, next to the model's committed
static-phase curves (`report.json: model_phase_sweep`).

**Decision rule, fixed now.** If the reference's h5, h7, h9 and h11 vary by less than 1 dB over the
full ψ cycle, including |ψ| < 20°, while the model's vary by 10–13 dB, then (i) holds. The
supported intervention is then the model's ladder-input level and drive mapping for this patch,
tested before and after with the full M1A property vector, signed per-note/per-partial changes
and preserved passes, plus the M5A/M5B regressions a shared-DSP change requires. If the reference
dips comparably near ψ ≈ 0, then (ii) holds: park M1A's harmonic property as a phase-state
question rather than a sound change, as the plan says.

## 7. Known-answer control

`synthetic_control()` builds two ideal 12-partial saws with a known per-partial gain and runs
them through the same pipeline. It passes all four cases:

| case | construction | expected → got |
|---|---|---|
| persistent partial deficit | same phase trajectory, h5/h7 −6 dB in every instance | h5, h7 persistent; h8 within → ✓. Error lives in the partial ✓ |
| interference only | same partial energies, model locked, reference free-running | h2, h8 phase-dependent (scored h8 +3.50/**+21.21**, matched ≈ 0); h5 within → ✓ |
| genuine amplitude change | oscillator 2's partial 4 at −12 dB in the repeat, same phase | h8 amplitude change (scored **−12.31**, matched −12.31, not phase) → ✓ |
| changing fundamental | oscillator 1's fundamental at −3 dB in the repeat | every ratio moves 3.0 dB, and the error lives in the fundamental, not the partial → ✓ |

The interference and amplitude-change cases produce the same kind of mixed-signal swing. Only the
matched residual and the contributor energies separate them. The power decomposition itself
reuses the existing analytic two-sine control
(`tools/test_measure_m1a_volume_mapping.py::test_interference_power_known_constructive_and_destructive_signals`).
These injected defects each turn a test red:

- a classifier that ignores the matched residual fails two cases;
- a sign-flipped phase estimator breaks the free-running prediction by more than 45°;
- attributing errors from ratios only misattributes the changing fundamental.

## Correction to earlier evidence

`../volume-mapping/README.md` cites the static-detune render's h8 repeat change of +0.23 dB
(against the reference's −19.69 dB). That small change was the render's own starting phase. It
was not an absence of phase dependence: the same render's h2 changes by −9.64 dB and its h10 by
+15.59 dB between the repeats. The caution drawn there still holds.

## Wrong-then-right (this diagnosis)

1. **Model isolated phase measured through the patch filter.** θ_j departed from j·ψ by 14.6°.
   `fit_psi` refused, and the model's isolation was re-measured with the filter open, as the
   reference controls are (residual 0.8–1.2°, integer agreement 0.08°). The refusal itself became
   evidence that the model's filter phase depends on level.
2. **Runner status.** The first full run *refused* (exit 1, recorded in its log). The background
   wrapper reported "exit code 0" because the command ended with `echo`. The log's captured `$?`
   caught it. Every later pass/fail count in this work comes from `tools/run_all.py` or from an
   explicitly captured `$?`.
3. **Hypothesis refuted, not published.** The working hypothesis going in was that "the odd-partial
   deficit is a persistent filter darkness". The static-phase sweep refuted it: most of the deficit
   is phase-sensitive in the model.

No published number was withdrawn.

## Reproduce

```sh
cd tools
python diagnose_m1a_harmonic_phase.py              # renders; about 4 min; refuses unless committed audio reproduces
python diagnose_m1a_harmonic_phase.py --reanalyse  # table, classification and control from report.json, no renders
python -m pytest -q test_diagnose_m1a_harmonic_phase.py
```

Renders: 29 in total, each hash-recorded in `report.json`. Two are reproduction checks, 16 form
the static sweep, 3 are matched renders and 8 are isolated renders with the filter open. No
reference audio was re-captured.
