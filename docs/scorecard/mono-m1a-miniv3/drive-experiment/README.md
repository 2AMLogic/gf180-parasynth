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
