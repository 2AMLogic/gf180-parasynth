# M1A attack measurement: known-answer qualification (m1a-envelope-score-v3)

**Outcome: the M1A attack is still unmeasurable on the reference side. M1A stays
NO VERDICT, and 10 ms is kept.** The estimator is now qualified over a stated
domain with measured error bounds. All three Mini V3 reference fits fall
outside it, for a reason the known-answer suite identifies.

## What changed (measurement version v2 → v3)

1. **One quantity.** The property reports the 10–90 % attack time in ms, and
   the 5 ms tolerance applies to that value. v2 stated its qualification in
   that quantity: the known-signal suite's `known_10_90_ms` was 0.5–20 ms. Its
   gate (`attack_fit_qualified`), however, tested `ramp_ms`, which is the fit's
   total 0–100 % ramp. That value is `10-90 / kfrac(p)`, where
   `kfrac(p) = 0.9^(1/p) − 0.1^(1/p)`: 1.25× the 10–90 value at p = 1 and 2.4× at
   p = 4. v3 gates on the reported 10–90 value. The ramp is only the search
   variable. When it sits on the 32-sample minimum or the maximum, the reading
   is a search limit and is never qualified.
2. **Estimator.** `tools/attack_fit_v3.py` replaces v2's `attack_fit` on both
   sides. The level during and after the ramp is a quadratic in window time,
   `A + Bτ + Cτ²`, fitted by linear least squares. The window is 80 ms (it was
   150 ms). The reason: both M1A patches decay to sustain 0.75, v2 had no way to
   represent that decay, and on steady spectra it measured known attacks up to
   4.8 ms short.
3. **The domain is stated in observables.** These are the fit's reported 10–90
   value, its explained-energy ratio, and the boundary flag. The true shape and
   spectrum of real audio are unknown, so "p ∈ {0.5, 1, 2}" cannot be checked
   against a reading. Each row's bound is the worst known-answer error over
   every suite case whose fit landed in that region, whatever that case's true
   shape or spectrum was.

## Known-answer suite (`tools/attack_known_answer.py`)

The suite has 1,898 signals per estimator. Every signal is synthesized in closed
form, independently of both synthesizers, and has an analytically known 10–90
time.

- **Shapes:** power law p = 0.5, 1, 2, 3, 4 (including the p = 3 and p = 4
  shapes the reference fits select), plus RC charges `1 − e^(−ku)` with k = 3
  and k = 5. The RC charges are outside the fit's model family.
- **Durations (10–90):** 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 10 and 15 ms.
- **Spectra:** band-limited saw plus octave saw (as in the M1A patch) through a
  quasi-static two-pole low-pass. Each harmonic takes the magnitude and phase
  of the filter at the instantaneous cutoff, and the power is renormalized so
  the level envelope stays the answer. The conditions are steady dull
  (400 Hz), steady bright (3 kHz), and three filter-envelope brightenings:
  2× decaying over 50 ms (the model's own law), 4× over 150 ms, and 8× over
  20 ms.
- **Realism:** ADS decay to 0.75 (80 ms), −60 dB noise, 16-bit quantization, an
  off-grid onset, MIDI 36 and 43, and two carrier phases. A clean slice without
  decay or noise is also included.

### Qualified domain (v3), in `qualification.json`

The domain was derived by the fixed rule in `tools/qualify_m1a_attack.py`, and
committed (3d81cc4) before any M1A audio was scored with v3. Bound = 1.0 ms.
Every row excludes search-boundary fits.

| explained ≥ | reported 10–90 | known answers | worst \|error\| | mean error |
|---:|:--:|---:|---:|---:|
| 0.99 | 0.25–6 ms | 342 | 0.783 ms | +0.007 ms |
| 0.98 | 0.25–6 ms | 431 | 0.783 ms | +0.009 ms |
| 0.97 | 0.25–6 ms | 439 | 0.783 ms | +0.008 ms |
| 0.95 | 0.25–6 ms | 449 | 0.979 ms | +0.013 ms |
| 0.90 | 5–6 ms | 41 | 0.818 ms | +0.194 ms |
| 0.80 | 0.25–0.75 ms | 144 | 0.817 ms | −0.010 ms |
| 0.70 | 0.25–0.75 ms | 144 | 0.817 ms | −0.010 ms |

Readings below explained 0.95 are qualified only near the bottom of the range
(0.25–0.75 ms) and in the 5–6 ms band. Readings above 6 ms are never qualified.

The same rule applied to v2 qualifies only 0.25–1.5 ms. Outside the domain the
estimator is badly wrong: off-boundary errors reach **7.9 ms** (fenv 2×/50 ms),
**7.8 ms** (4×/150 ms), **8.7 ms** (8×/20 ms), and 3.7–4.1 ms on steady spectra
with RC shapes at 10–15 ms.

**A floor hit bounds nothing.** 541 v3 fits landed on the 32-sample minimum,
with true 10–90 times anywhere from 0.1 to **10 ms**.

## Controls (in `tools/test_qualify_m1a_attack.py`)

- **Start red:** v2 fails the realistic suite. It is off by more than 4 ms inside
  its own claimed 0.5–20 ms range, and a live RC 10 ms case with decay is off by
  more than 2 ms.
- **Biased estimators violate the committed domain:** 1.10× and 0.90× gain,
  and +0.3 ms and −0.3 ms offset. A live 1.3× estimator exceeds the bound on the
  live slice.
- **The ramp is not the 10–90 value.** A p = 4 floor fit has a 0.667 ms ramp,
  which v2's 0.5–20 check passes, while its 10–90 value is 0.274 ms. v3 rejects
  it.
- **Search boundaries are never qualified.** This is checked for both the
  minimum and the maximum.
- **Stale suite:** if `attack_fit_v3.py` changes after the suite ran, the scorer
  REFUSES.
- The committed domain must re-derive exactly from the committed suite.

## Rescore of the existing renders (`rescore.json`, `tools/rescore_m1a_attack_v3.py`)

No render was made. Each WAV is bit-identical to its sweep point's recorded
SHA-256 (10 ms `628a6a31…`, 4 ms `d7177503…`, 5 ms `1689d723…`). 10–90 values are
in ms; Q means qualified and U means unqualified.

| setting | note | model (p, explained) | Q | reference (p, explained) | Q | grade |
|---:|:--|---:|:-:|---:|:-:|:--|
| 10 ms | 36 @0.1 s | 8.517 (1, 0.960) | U (>6 ms) | 1.687 (2, 0.724) | U | unqualified |
| 10 ms | 43 @2.1 s | 8.600 (1, 0.965) | U (>6 ms) | 0.274 (4, 0.656) | U (search min) | unqualified |
| 10 ms | 36 @4.1 s | 9.950 (1, 0.960) | U (>6 ms) | 1.150 (1, 0.777) | U | unqualified |
| 4 ms | 36 @0.1 s | 0.274 (4, 0.956) | U (search min) | 1.687 | U | unqualified |
| 4 ms | 43 @2.1 s | 3.083 (2, 0.962) | Q | 0.274 | U (search min) | unqualified |
| 4 ms | 36 @4.1 s | 3.317 (0.5, 0.946) | U (<0.95) | 1.150 | U | unqualified |
| 5 ms | 36 @0.1 s | 4.950 (0.5, 0.958) | Q | 1.687 | U | unqualified |
| 5 ms | 43 @2.1 s | 5.217 (0.5, 0.963) | Q | 0.274 | U (search min) | unqualified |
| 5 ms | 36 @4.1 s | 3.917 (1, 0.950) | U (<0.95) | 1.150 | U | unqualified |

Every other property is identical at all three settings: Pitch −0.05059 cents
(pass), Harmonic shape 19.82331 dB (fail), Release +8.47917 ms (pass), Gain
−1.04361 dB (pass), Clipping 0 % (pass), Filter envelope unqualified.

**Promotion: none.** No setting has a qualified reference on any note, so no
setting can be graded, and 10 ms is kept.

## The specific limitation

The quantity is the 10–90 % attack time of the Mini V3 reference at all three
notes.

- **MIDI 43 @2.1 s:** the fit sits on the ramp-search minimum (0.274 ms at
  p = 4). In the suite, floor hits come from true attacks between 0.1 and 10 ms.
- **MIDI 36 @0.1 s and @4.1 s:** the readings are 1.687 ms and 1.150 ms, with
  explained ratios of only 0.724 and 0.777. The periodic-carrier model leaves a
  quarter of the window unexplained. In the suite, readings like these (explained
  0.65–0.80, reported 1–2.5 ms, 42 cases) arise only under the 4× and 8× filter
  sweeps, from true attacks between 0.1 and 5 ms, with errors up to 3.48 ms.
  At that explained ratio, only readings of 0.25–0.75 ms are qualified.

**Why:** the reference's waveform changes during the first tens of
milliseconds. The manifest's h4 moves by up to 6.5 dB between 20 and 300 ms.
The estimator's steady-state template does not describe the onset, so under
those conditions the attack cannot be separated from the spectral change.

**What would close it:** an estimator that does not depend on a steady-state
template during the attack. One option is a time-varying template, or a fit
restricted to partials the filter sweep does not move. It would need to be
qualified on the same suite. A second option is a reference render with the
filter contour at 0, taken as a separate reference measurement (the frozen
reference is not changed).

The model side is also a problem. At 10 ms, its readings of 8.5–9.95 ms are
above the domain, and in the suite, readings like them (explained 0.94–0.97,
7–10.5 ms) carry errors of up to 7.9 ms.

## Wrong-then-right in this round: 4

1. **v2's "<1 ms known-signal error" was stated for signals with no
   post-attack decay and a near-steady spectrum.** On realistic signals it
   measured up to 4.8 ms short. The earlier sweep's model readings inherit this.
2. **The v2 gate tested the ramp and not the 10–90 value** (described above).
3. **Re-deriving a domain from a biased estimator's readings is not a
   control.** A 1.15× estimator still re-derives 0.25–6 ms, because the range is
   stated in its own readings. The control that works applies the committed
   domain.
4. **The first v3 scorer run crashed** with `UnboundLocalError` (a name clash
   with a local variable) before producing any number. The known-answer
   domain's own bound check failed on 4-decimal rounding of the published
   bound; the fix allows a tolerance of 5e-5.

## Scope and caveats

- The bounds hold inside the suite's conditions. The domain is conditioned on
  the observables, but its coverage is only as wide as the suite's mix of
  shapes, spectra and decays.
- The ADS decay in the suite (80 ms exponential) is not the model's linear
  decay. It was chosen as a harder case, not as a calibration.
- The attack-sweep points (`../attack-sweep/`) were fitted by the v1/v2
  estimator and are still classified under the frozen v2 rule. This rescore
  supersedes them for the 10, 4 and 5 ms settings.
