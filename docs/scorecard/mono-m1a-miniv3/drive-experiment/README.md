# M1A filter-drive experiment (one bounded round)

**Experiment only; nothing is promoted.** The selected patch (`m1a-gain-minus4db-v2`), the
engine, the frozen reference, the scorer (`m1a-envelope-score-v3`), the windows and the
tolerances are unchanged.

## Why

In `../phase-cycle-capture/` (#219), under equal moving-phase windowing, the model's odd
partials h5/h7/h9/h11 move 9.8–10.4 dB with relative phase ψ. The Mini V3's move
0.7–2.2 dB, and its takes differ by ≤ 0.28 dB per bin. That is ~7.8–9.7 dB of excess
phase-dependent range. A likely mechanism is the level-dependent `tanh` input stage of the
ladder (#218 §4). This observation does **not** uniquely identify drive as the cause.

**Data status.** The #219 captures are **development data** here. They have been inspected,
so no rule written after that inspection may treat them as unseen confirmation. Confirmation
would need a fresh capture (see the MIDI 43 rule below, written only if a candidate is chosen).

## Pre-declaration (committed before the first candidate render)

### Candidates

`drive` ∈ {**0.75** (baseline = the selected patch), **0.50**, **0.25**}. Everything else in
`bass.patch_for_reference(manifest)` is held fixed except the output volume `vol`, which is
compensated as below.

Where drive acts: `LadderFx.regs(res, drive)` (`model/fixed.py`) sets the ladder input-gain
register `gain = drive·vpu/2Vt` (Q4.16). The output register `ogain` does not depend on
drive, so lowering drive lowers the output level as well as the nonlinearity.

### Output-volume compensation rule

A candidate must not win by getting quieter. For each candidate:

1. Render the complete original M1A phrase once with the candidate drive and the baseline
   `vol`. This is the "uncompensated" render.
2. Δ = mean over the three M1A events of (baseline RMS dBFS − uncompensated RMS dBFS). Both
   are measured in the scorer's Gain window (onset + 0.30 s to + 0.55 s), which is the
   quantity the Gain property scores.
3. `vol_candidate = vol_baseline · 10^(Δ/20)`. This is applied **in the patch**, before
   rendering. It is set once and never iterated, never fitted to the reference, and never
   applied as post-normalisation of audio.

The analytic small-signal ratio 0.75/drive is reported next to Δ for comparison, but not
used. The baseline's compensation is exactly 1.

### Measurements per candidate

- **Moving-phase curves.** The same condition as #219: 12 s MIDI 36, oscillator 2 detuned by
  the reference's measured −3.488 c, and the same windows, bins and estimators
  (`analyse_m1a_phase_cycle.measure/summarise`). ψ is measured through the candidate's
  **own** isolated open-filter renders, which use the same drive and vol. Report h1–h12
  binned dBFS, the range and dark of each odd partial, harmonic ratios, and the peak and
  rail-clipped sample fraction.
- **The complete original M1A phrase**, rendered from the patch through the scorer's
  `compare_audio` exactly as `mono_m1a_score.run` does. First, the baseline must reproduce
  `results/M1A.json` properties to within 1e-6, or the tool REFUSES. Report the full property
  vector and every per-note, per-partial error, signed, as a change from baseline.

### Preservation policy (a regression is any of these)

- a baseline-passing property (Pitch, Envelope release, Gain, Clipping) that fails;
- a per-note, per-partial harmonic cell with |error| ≤ 1 dB at baseline that exceeds 1 dB;
- any property that changes validity (valid ↔ unqualified). Attack stays unqualified and is
  not graded. The official rubric is not changed.

### Choice rule (at most one candidate)

A candidate is **eligible** iff all three hold:

- (a) it has **zero** regressions;
- (b) its official Harmonic shape value (max |error|) is lower than the baseline's
  19.82331 dB by at least 0.5 dB;
- (c) its mean moving-phase odd-partial range over h5/h7/h9/h11 is lower than the baseline's.

Among eligible candidates, choose the largest reduction in (b). If none is eligible:
**STOP, choose none**, and report so.

### If a candidate is chosen

Its frozen acceptance rule for a fresh MIDI 43 Mini V3 capture is written and committed here
**before** any such capture. There is no MIDI 43 capture and no promotion in this round.

---

## Results (after the pre-declaration above; nothing in it was changed)

**Choice: NONE. STOP.** Neither non-baseline candidate is eligible. Both fail (a), the
regressions rule, and (b), the harmonic-shape rule. Both pass (c): drive does remove the
model's excess phase sensitivity. Record: `report.json`. Instrument:
`tools/measure_m1a_drive.py`. Rules tests: `tools/test_measure_m1a_drive.py`.

Preconditions held before any candidate was used:
- The baseline render reproduces the committed `m1a-model.wav` bit-exactly.
- The baseline's valid properties equal `results/M1A.json` to within 1e-6.
- The baseline moving-phase curves equal #219's model curves exactly.

### Compensation (pre-declared rule, applied in the patch)

| drive | ladder `gain` register | Δ applied | analytic 0.75/drive | vol (Q0.15) | Gain error uncompensated → compensated |
|---:|---:|---:|---:|---:|---:|
| 0.75 | 127 795 | 0 | 0 | 0.2839 (9304) | −1.044 (baseline) |
| 0.50 | 85 197 | +3.01 dB | +3.52 dB | 0.4016 (13 158) | −3.957 → −0.946 |
| 0.25 | 42 598 | +8.73 dB | +9.54 dB | 0.7758 (25 422) | −9.656 → −0.925 |

Without compensation, drive 0.25 would have dropped 8.6 dB in level. With it, every
candidate's Gain sits within 0.12 dB of the baseline's. So no candidate "wins" by getting
quieter, and none gains an advantage from loudness either.

### Moving-phase curves (#219 condition; ψ from each candidate's own isolated renders)

Range and dark are in dB. The ratio is the mean partial level minus the mean h1 level, in dB.
h1 is the mean dBFS. The Mini V3 reference (pooled takes) is the development data.

| partial | ref range / dark / ratio | drive 0.75 | drive 0.50 | drive 0.25 |
|---|---|---|---|---|
| h1 dBFS | −25.2 | −26.09 | −26.43 | −26.65 |
| h3 | 0.38 / −0.04 / −9.46 | 4.46 / −1.42 / −9.84 | 1.87 / −0.50 / −9.76 | 0.46 / −0.10 / −9.71 |
| h5 | 0.70 / −0.21 / −14.39 | 10.42 / −4.47 / −15.86 | 3.94 / −1.48 / −15.10 | 0.81 / −0.22 / −14.66 |
| h7 | 1.19 / −0.48 / −18.52 | 10.36 / −4.01 / −20.70 | 5.79 / −2.69 / −19.57 | 1.40 / −0.48 / −18.59 |
| h9 | 1.65 / −0.71 / −22.72 | 9.79 / −1.45 / −24.41 | 7.27 / −2.92 / −23.58 | 1.94 / −0.73 / −22.26 |
| h11 | 2.17 / −0.75 / −26.98 | 9.99 / −0.94 / −27.74 | 6.86 / −1.75 / −27.27 | 2.57 / −0.84 / −25.93 |
| mean odd range (rule c) | 1.43 | 10.14 | 5.96 | **1.68** |

Even-partial ranges (h2/h4/h6/h8) stay about the same across candidates: 24.1–24.4,
14.4–18.4, 15.9–16.3 and 11.8–12.1 dB. No render clips: 0 % of samples reach the rail, and
the peak is between −17.1 and −16.9 dBFS for the phrase and the moving-phase renders alike.

**What this shows (development data, not confirmation).** Drive controls the excess phase
sensitivity almost entirely. At 0.25, the model's odd-partial ranges, darkening and mean
ratios come close to the Mini V3's on this capture. That supports the drive mechanism named
in #218 and #219. It does not validate it: the curve was inspected before this experiment,
and drive 0.25 was one of three predeclared points, not fitted.

### Complete original M1A phrase: property vectors

| property | 0.75 (baseline) | 0.50 | 0.25 | tolerance |
|---|---:|---:|---:|---:|
| Pitch (cents) | −0.051 ✓ | −0.049 ✓ | −0.050 ✓ | 1 |
| **Harmonic shape (dB, max \|err\|)** | **19.823** ✗ | **20.482** ✗ (+0.66) | **22.627** ✗ (+2.80) | 1 |
| Envelope attack | unqualified | unqualified | unqualified | — |
| Envelope release (ms error) | +8.479 ✓ | +8.521 ✓ | +8.542 ✓ | 20 |
| Gain (dB error) | −1.044 ✓ | −0.946 ✓ | −0.925 ✓ | 3 |
| Filter envelope | unqualified | unqualified | unqualified | — |
| Clipping (%) | 0 ✓ | 0 ✓ | 0 ✓ | 0.01 |
| per-note harmonic cells ≤ 1 dB (of 33) | 8 | 9 | 15 | — |

### Per-note, per-partial harmonic error (model − reference, dB; change from baseline in brackets)

| note | partial | 0.75 | 0.50 | 0.25 |
|---|---|---:|---:|---:|
| 36 @0.1 | h2 | +13.44 | +13.63 (+0.20) | +13.71 (+0.27) |
| | h3 | −1.61 | −0.82 (+0.79) | −0.45 (+1.15) |
| | h4 | −1.55 | −0.07 (+1.48) | +0.52 (+2.07) |
| | h5 | −5.60 | −1.95 (+3.65) | −0.43 (+5.17) |
| | h6 | +1.03 | +3.14 (+2.11) | +4.60 (+3.56) |
| | h7 | −8.26 | −4.00 (+4.26) | −0.55 (+7.71) |
| | h8 | +0.58 | **+1.28 (+0.70) REG** | **+3.43 (+2.85) REG** |
| | h9 | −5.97 | −5.63 (+0.34) | −0.67 (+5.30) |
| | h10 | −0.84 | −0.27 (+0.57) | **+1.92 (+2.75) REG** |
| | h11 | −4.31 | −4.17 (+0.14) | +0.06 (+4.37) |
| | h12 | +9.16 | +10.00 (+0.84) | +11.46 (+2.31) |
| 43 @2.1 | h2 | +0.42 | +0.77 (+0.34) | +0.92 (+0.50) |
| | h3 | −1.72 | −0.71 (+1.01) | −0.10 (+1.63) |
| | h4 | +0.69 | **+2.94 (+2.26) REG** | **+4.11 (+3.42) REG** |
| | h5 | −8.90 | −3.59 (+5.31) | −0.81 (+8.09) |
| | h6 | +12.05 | +12.94 (+0.89) | +15.02 (+2.98) |
| | h7 | −6.25 | −6.60 (−0.35) | −2.46 (+3.78) |
| | h8 | +7.41 | +8.05 (+0.63) | +9.52 (+2.11) |
| | h9 | −4.76 | −5.34 (−0.58) | −2.83 (+1.93) |
| | h10 | +0.65 | **+1.39 (+0.75) REG** | **+2.60 (+1.96) REG** |
| | h11 | −1.51 | −2.55 (−1.04) | −1.75 (−0.23) |
| | h12 | −1.00 | +0.37 (+1.38) | +0.64 (+1.64) |
| 36 @4.1 | h2 | +0.22 | +0.43 (+0.21) | +0.52 (+0.30) |
| | h3 | −1.11 | −0.59 (+0.51) | −0.38 (+0.73) |
| | h4 | +0.32 | **+1.85 (+1.53) REG** | **+2.48 (+2.16) REG** |
| | h5 | −4.06 | −1.46 (+2.60) | −0.43 (+3.63) |
| | h6 | +3.52 | +5.63 (+2.11) | +7.14 (+3.63) |
| | h7 | −5.58 | −2.57 (+3.01) | −0.24 (+5.35) |
| | h8 | +19.82 | +20.48 (+0.66) | +22.63 (+2.80) |
| | h9 | −2.68 | −2.73 (−0.05) | +0.53 (+3.21) |
| | h10 | +7.99 | +8.58 (+0.58) | +10.67 (+2.68) |
| | h11 | −0.44 | −1.00 (−0.56) | **+1.49 (+1.93) REG** |
| | h12 | +2.79 | +3.73 (+0.95) | +4.97 (+2.19) |

**Every regression.** These are all per-cell; no property regresses.

- **0.50, 4 regressions:** 36@0.1 h8; 43@2.1 h4 and h10; 36@4.1 h4.
- **0.25, 6 regressions:** 36@0.1 h8 and h10; 43@2.1 h4 and h10; 36@4.1 h4 and h11.

### Why neither is chosen

- **(a) Regressions: fail** for both candidates (4 and 6 cells).
- **(b) Harmonic shape: fail.** It gets worse: 19.82 → 20.48 → 22.63 dB. The official
  maximum is the h8 notch at 36@4.1, which #218 attributed to oscillator phase state. Lower
  drive deepens it, and it raises the *even* partials by 1.5–3.6 dB across the phrase
  (h4, h6, h8, h10, h12). Once compensated, lowering drive brings the odd partials up to the
  reference (h5/h7/h9/h11 on 36@0.1: −5.6/−8.3/−6.0/−4.3 → −0.4/−0.6/−0.7/+0.1). But it
  overshoots every even partial, and the even partials carry the scored maximum.
- **(c) Odd range: pass** for both (10.14 → 5.96 → 1.68 dB).

**Conclusion.** Drive alone does not improve the harmonic property. The excess odd-partial
phase sensitivity is a drive effect. The remaining harmonic failure sits in the even partials:
their level relative to the fundamental, and the phase-state notches. Per the brief: **stop**.
The next step is another measurable failure, not more M1A. No MIDI 43 acceptance rule is
written, because no candidate was chosen.

### Where drive lives (for any future promotion)

- **Patch-local value.** `drive=.75` is set in `mono_m1a_score.patch_for_reference`, which is
  M1A only. M5A/M5B set their own `filter_drive` 0.75 in `mono_m5a_score`.
- **Shared code it runs through.** The host conversion is `model/fixed.py` `LadderFx.regs`,
  which gives the `gain` register, and the ladder path is `model/voice_fx.py` step 6
  (`lad.process`).
- **What a promotion would change.** Only M1A's register value, 127 795 → 42 598, which is
  within the 20-bit field. It would still need M5A/M5B regression runs and production-path
  (RTL) verification of the ladder at that gain value before promotion. No RTL job was run in
  this round.

### Wrong-then-right

None in this round: the instrument ran once, clean. The rule tests were written against the
pre-declared rules, and each check was shown to fire: a Gain regression, a Pitch regression,
a per-cell pass → fail, a validity change, and the choice rule's three exclusions.
