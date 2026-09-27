# M5A/M5B unwanted artifacts on the selected engine (#333, plan098 §5)

Sound batch 1: reproduce, localize and check the existing pulse work. This
bundle is model and RTL-simulation evidence. It is not a measurement of a
programmed board. The published R1 image (`OSC2X=1 FILTER2X=1 PULSE2X=0`) is
unchanged and remains the rollback. **No result below that uses `pulse2x` is
sound delivered by the R1 image.**

## A. Engine identity and reproduction

The selected engine is **R1 as published**. Every RTL source and ROM pinned in
`fpga/release/r1-candidate.json` has the same SHA-256 on `origin/main`
`f92918e`. Only testbenches changed after the R1 freeze at `6864435aa6eb`. The
configuration is:

- contract rev 14, `OSC2X=1 FILTER2X=1 PULSE2X=0`;
- presets `default`, `m5a-saw` and `m5a-pulse` (`fpga/selected_preset.py`), SR 48 kHz;
- M5 patch: drive 0.75, q 0, and cutoff 20 kHz on saw segments (the Mini V3 self-oscillation calibration of 14,073 Hz on pulse segments);
- saw volume correction −0.45428 dB;
- the `pulse29` control, which is **47.90 % duty** under `VOICE_FILTER_2X` (the `DUTY_WIDE` encoding).

References are the frozen Mini V3 3.12.0.3422 WAVs, sha `a808cd22…` (M5A) and `ee77ef21…` (M5B).

Three engines are kept distinct:

| name | what | where |
|---|---|---|
| R1 | published image configuration | RTL at the pins; `VoiceFx(oversample_2x, rate_converted_ladder, causal, headroom, pulse479_filter_candidate)` |
| development | the same as R1; nothing has diverged yet | — |
| pulse2x | R1 plus `VOICE_PULSE_2X` (rectangles through the 2x decimator) | `oversample_pulse_2x=True`; RTL `--pulse2x` |

| case | property | published | reproduced, model | reproduced, RTL SPI→I²S |
|---|---|---:|---:|---:|
| M5A | harmonic shape | 7.56× (7.56137 dB) | 7.56136 dB | 7.56137 dB |
| M5A | foldback | 3.42× (10.27423 dB) | 10.27447 dB | 10.27423 dB |
| M5B | harmonic shape | 5.96× (5.96219 dB) | 5.96219 dB | (model only; not published as RTL) |
| M5B | foldback | 2.95× (8.84954 dB) | 8.84954 dB | — |

The R1 RTL phrase decoded **1,305,533 of 1,305,533 I²S periods identical to the
model**. Its decoded WAV hash is `d64c9a86…`, byte-identical to the published
capture. The failures reproduce, so no identity or measurement difference needs
locating first. Records are in `repro/`.

The worst partials say what each failure is:

- **Harmonic shape is a brightness deficit, not aliasing.** Every saw partial h2..h12 is 1.8–7.6 dB *below* Mini V3 relative to h1. The worst are M5A MIDI 96 h3 and M5B MIDI 84 h7.
- **Foldback comes from the pulse segments.** Saw excess is 1.9/2.2 dB, inside the limit. Pulse excess is 8.85/10.27 dB at MIDI 84/96 and 7.26/8.85 dB at MIDI 72/84.

## B. Localization

The instrument is `tools/mono_artifact_probe.py`. Its known-answer suite is
`tools/test_mono_artifact_probe.py` (15 tests, started red with a rectangular
window).

For a held note with drift, noise and modulation off, every stage is either LTI
or memoryless, so the ideal output has energy only at k·f0. The probe computes
f0 exactly from the increment register; it does not estimate it. Each stage's
power is then split four ways:

- intended harmonics;
- images at the predicted folds;
- residual (everything else);
- at 96 kHz, supra-band harmonics that the decimator must remove.

It reports absolute dBFS, the value relative to the intended signal, and the
intended power in 5–20 kHz. A darker candidate is therefore visible.

The probe refuses rather than answers in these cases:

- silence;
- resonance ≥ 0.97;
- noise, drift or modulation;
- multi-pitch patches;
- f0 under 16 analysis bins.

Transitions, glides and modulation are **not covered**. They need a validated
time-varying oracle, which this probe does not supply.

**Controls.** Properties × defects, at R1 saw MIDI 84 and R1 pulse29 MIDI 96
(`probe/controls-*.json`). Every control moves its intended property:

| defect | property it must move | saw 84 | pulse 96 |
|---|---|---|---|
| `ALIAS_NOBLEP` (model mutation) | image dBFS | −65.5 → −46.0 | −53.7 → −33.5 |
| `DARKEN_LP4K` | upper wanted, relative | −15.5 → −24.4 | −11.1 → −20.8 |
| `SPUR_M70` (−70 dBFS tone) | residual dBFS | −94.4 → −70.0 | −95.9 → −70.0 |
| `CLIP_2XFS` | output rail samples | 0 → 21,731 | 0 → 29,355 |
| `DROPOUT_10MS` | dropout depth | −0.2 → −222 dB | −0.0 → −225 dB |
| `SILENCE` | activity | FAIL-ACTIVITY; every artifact property refused | same |

The matrix also shows a blind spot. At pulse MIDI 96, a −70 dBFS spur is
**BLIND** in the headline unwanted-energy figure, because images at −53.7 dBFS
dominate it. Only the residual sees it, which is why both are reported.

**Sweep.** `probe/sweep2-*.json.gz` and `probe/summary.json` hold 310 points,
all MEASURED and none refused:

- a grid of 10 notes (MIDI 24–127) × 9 waveforms × the R1 and pulse2x engines, at the M5A preset filter;
- a boundary/pairwise set of cutoff {400, 4k, 20k, 21.6k} × q {0, 0.5, 0.9} × drive {0.75, 1.6, 4.0} over notes {36, 84, 96, 120}.

**In-band unwanted energy by stage, dBFS** (M5A preset filter; the output
includes vol 0.45):

| engine / wave / MIDI | osc | mixer | ladder in @96k | ladder out @96k | ladder out (after decimation) | output | output, relative to intended |
|---|---:|---:|---:|---:|---:|---:|---:|
| R1 pulse29 84 | **−33.8** | −33.8 | −35.2 | −49.2 | −48.8 | −55.8 | −43.1 dB |
| R1 pulse29 96 | **−30.8** | −30.8 | −32.2 | −47.2 | −46.8 | −53.7 | −39.1 dB |
| pulse2x pulse29 84 | −55.1 | −55.1 | −57.3 | −64.2 | −57.3 | −64.2 | −51.2 dB |
| pulse2x pulse29 96 | −52.1 | −52.1 | −54.3 | −61.9 | −54.5 | −61.4 | −46.6 dB |
| R1 saw 84 | −53.3 | −53.3 | −57.8 | −69.2 | **−58.6** | −65.5 | −48.2 dB |
| R1 saw 96 | −55.1 | −55.1 | −57.3 | −65.4 | **−58.7** | −65.6 | −47.2 dB |

- **Pulse foldback is made at the oscillator.** The base-rate PolyBLEP rectangle is 20 dB dirtier than the 2x saw at the same notes. The mixer and ladder do not create it; the ladder removes about 14 dB of it.
- **The saw's remaining aliasing is made in the ladder and the rate converter.** The tanh ladder raises supra-band (>24 kHz) harmonic power from −60.4 to −47.8 dBFS at MIDI 84. The rate converter's decimator is −6 dB at 24 kHz and −18 dB at 26 kHz, so its transition band folds part of that back. The effect is +10 dB of in-band images at decimation (−68.5 → −58.6 dBFS image). It stays inside the M5 foldback limit.
- Clipping and dropout: none at the output anywhere in the sweep.
  - R1's base-rate rectangle sits on the oscillator rail by construction (full-scale plateau), so the oscillator rail count does not discriminate there.
  - **Open item:** with pulse2x, the 2x rectangle's decimator reaches its rail at MIDI ≥ 108 (1,007–6,064 of 36,000 samples). The 0.85 headroom was sized on the saw sweep only.

**Harmonic-shape deficit** (`probe/drive.json`): the same held saw at 20 kHz
with only the ladder drive varied, compared with the ideal 1/k saw.

| MIDI | analytic 4-pole @20 kHz, h2..h6 | drive 0.05 | drive 0.75 (preset) |
|---|---|---|---|
| 72 | −0.04 −0.10 −0.18 −0.28 −0.41 | −0.05 −0.12 −0.22 −0.34 −0.48 | −0.76 −2.32 −3.80 −4.21 −4.15 |
| 84 | −0.14 −0.38 −0.70 −1.10 −1.59 | −0.17 −0.43 −0.76 −1.18 −1.67 | −1.92 −5.27 −5.55 −5.09 −6.04 |
| 96 | −0.56 −1.44 −2.62 −4.02 −5.59 | −0.59 −1.50 −2.76 −4.21 −5.77 | −4.16 −7.80 −6.19 −8.55 −9.29 |

At low drive the ladder matches the analytic linear response within 0.2 dB,
which is the known answer for the cutoff mapping. At the preset drive it loses
a further 2–7 dB of every upper partial. **The M5 harmonic-shape failure is
localized to the ladder's tanh operating level.** It is not the oscillator
(within 0.5 dB of ideal up to h12 at MIDI 84), not the rate converter and not
aliasing. Lower drive also lowers aliasing: output unwanted is −59.0 dB relative
at drive 0.05 against −48.2 at 0.75 (MIDI 84). But drive is also gain: intended
power falls by 22 dB. So this is an operating-level question, not a knob to turn
on its own.

## C. Existing pulse work, re-evaluated on the current engine

**The pulse2x benefit reproduces** (`repro/pulse2x-model-*.json`, `pulse2x-rtl/`):

| | baseline foldback | gain-only control | pulse2x, model | pulse2x, RTL SPI→I²S |
|---|---:|---:|---:|---:|
| M5A | 10.274 dB | 9.413 dB | 2.206 dB | 2.208 dB (1,305,533 / 1,305,533 periods exact) |
| M5B | 8.850 dB | 8.019 dB | 1.913 dB | 1.915 dB (729,533 / 729,533 periods exact) |

Both phrases now pass foldback (limit 3 dB). Harmonic shape is unchanged
(7.56/5.96: saw is untouched, and the probe test shows R1 and pulse2x saw are
bit-identical). The disabled control is byte-identical to the baseline and is
not promoted. The gain-only control applies the candidate's level change to
the baseline without changing the oscillator rate:

| rectangle gain | gain-only foldback (M5A / M5B) | due to level alone | pulse2x foldback | due to the rate change |
|---|---|---:|---|---:|
| 0.85 (pulse2x as first built) | 9.413 / 8.019 dB | 0.86 / 0.83 dB | 2.206 / 1.913 dB | 7.21 / 6.11 dB |
| **0.74 (R2, with the rectangle headroom)** | **8.618 / 7.195 dB** | **1.66 / 1.65 dB** | 2.206 / 1.913 dB | **6.41 / 5.28 dB** |

At 0.85, the control scaled rectangles by the saw's gain; R2 uses 0.74 for
rectangles. `tools/measure_mono_pulse_2x.py` now takes one rectangle gain for
both candidate and control (`--rect-gain-q15 24248`, `pulse2x-rect074/`).
The earlier "under 0.9 dB" held only for 0.85. **At R2's 0.74 the level
change accounts for 1.65 dB of foldback.** The rate change still accounts for
5.3–6.4 dB, so the candidate does not pass by being quieter.

The strict incremental comparator still rejects the candidate: an
already-failing pulse partial moves by 0.347 dB. That is the tradeoff already
accepted for RTL advancement on 2026-09-21 (`docs/scorecard/mono-pulse-2x/rtl-advancement.md`).
No rubric is changed here.

**The unused anti-alias computation is still valid on current sources.** The
records are in `docs/deadline/recheck-333/`, in a separate PR.

## D. Promotion status of pulse2x

The candidate was selected on development conditions: pulse29 at the M5 notes
72/84/96, preset filter. It is confirmed on **107 untouched** points: other
notes, other duty shapes, and the cutoff/q/drive pairwise set. **This table
was measured at rectangle gain 0.85** (`probe/summary.json`, provenance commit
`d1fe013`, before the rectangle-headroom work `a4e2f0a`/`97a7078`/`1063bfc`
picked 0.74 for R2). It is evidence for pulse2x at 0.85 against R1, not for
R2's actual 0.74 configuration.

| property (pulse2x − R1, at rectangle gain 0.85) | development (3) | untouched (107) |
|---|---|---|
| output unwanted, dBFS | 3 improved > 1 dB (−7.7 to −8.6) | 95 improved, 1 regressed (+1.2 dB: MIDI 120 at cutoff 400, where the intended note is −106 dBFS, i.e. filtered to inaudibility) |
| output unwanted, relative | 3 improved (−7.5 to −8.1) | 93 improved, **0 regressed** |
| upper wanted power, relative | within 0.22 dB | 9 brighter and 6 darker by > 1 dB (worst −1.69 dB, all at q ≥ 0.5 or cutoff 400) |
| intended level | −0.24 to −0.74 dB | −1.47 to +7.5 dB (the 0.85 headroom gain) |
| new rail samples / new dropouts at output | 0 / 0 | 0 / 0 |

At rectangle gain 0.85, the candidate improves unwanted energy both
absolutely and relative to the signal. It does not win by getting quieter
there: the relative gain is as large as the absolute one, and the gain-only
control explains 0.86/0.83 dB of foldback at 0.85 (M5A/M5B), against
7.21/6.11 dB for the rate change.

**At R2's rectangle gain of 0.74**, the 107-point untouched comparison above
has not been re-run; only a narrower gain-only check exists
(`pulse2x-headroom/probe-074.json`, 32 points, comparing 0.74 against 0.85):
**7 of the 32 worsen, one by more than 1 dB (pulse15 MIDI 84, +1.02 dB)**. The
gain-only control at 0.74 explains 1.66/1.65 dB of foldback (M5A/M5B), against
6.41/5.28 dB for the rate change (§C). The "93 improved, 0 regressed" row
above is established for pulse2x at 0.85 against R1; it is **not** established
for pulse2x at R2's 0.74.

**Production path, PULSE2X=1, unmutated RTL, current sources:**

- M5A and M5B full phrases: see above.
- Full-kit phrase: `verify_synth_top --filter2x --pulse2x`, 2,851 / 2,851 I²S periods exact.
- `stress-pulse`: 2,158 / 2,158 periods, writes 461 / 461, 0 missed, **slack 3** (the deadline README says 4).
- `extreme-pulse`: register-legal but above Nyquist and refused by the host's INC_RANGE. It **misses 1,294 frames**.

**Next step (integration owner): a new image, not an R1 change.** The spec is
in `docs/deadline/recheck-333/README.md`:

- `OSC2X=1 FILTER2X=1 PULSE2X=1` plus the one-line `skip2xwin` S_WIN condition in `voice_dp.v`;
- admit `PULSE2X` in `fpga/release/qualified_domain.py`;
- rebuild under #205.

## Remaining critical defects and the next decisive action

1. **M5A/M5B harmonic shape (7.56×/5.96×)**, localized to the ladder's tanh operating level at drive 0.75, is **improved, not resolved**. A shared lower drive was a **negative result** (`operating-level/`); a saw-only drive of **0.35**, recommended after a disclosed tie (`saw-drive/`), gives M5A 5.30× and M5B 4.96×. Both still fail, now limited by the pulse. Saw partials themselves remain 3.2–3.7 dB off at their worst.
2. **Pulse2x rectangular headroom at MIDI ≥ 108** is resolved: clipped energy was measured against a 0.80 headroom challenger (`pulse2x-headroom/`); 0.80 fails, and **0.74 was selected by the frozen rule** (§C).
3. **Pulse brightness deficit**, the new limiting factor after the saw-only drive fix, is filed as #347.
4. **Dynamic coverage.** Transitions, glides and modulation have no artifact coverage (§B) and need a validated time-varying oracle before this probe can be extended to them.
5. **Fresh-condition confirmation of saw drive 0.35** has not yet been run under a control that can demonstrably fail.
6. **Attack** (1.07×/1.21×) is unchanged and outside this batch.

## Wrong-then-right, this batch: 3

1. The first clipping control (+12 dB) never reached the rail at the preset's −17 dBFS level. It was **BLIND** to its own defect; the controls matrix caught it, and it now overdrives relative to peak.
2. At 96 kHz, image and residual energy above 24 kHz (the decimator's job) was lumped with in-band aliases. That made the ladder input look dirtier than the oscillator for pulse2x. The fix splits unwanted energy at the audio band; a known-answer test pins the split.
3. The `skipallwin` negative control was first run with `verify_deadline --expect-fail`, which requires a *deadline* reason. It reported "not caught" although the intended I²S assertion fired (358 of 2,158 periods differ). The control is valid. The invocation was wrong, and the record keeps both.

## Reproduce (build box)

```
PY=~/work/venv/bin/python
$PY -m pytest -q tools/test_mono_artifact_probe.py
$PY tools/mono_artifact_probe.py controls --out build/probe/controls-r1-84-saw.json
$PY tools/mono_artifact_probe.py controls --note 96 --wave pulse29 --out build/probe/controls-r1-96-pulse.json
$PY tools/mono_artifact_probe.py sweep --part 0 --parts 2 --out build/probe/sweep2-0.json   # and --part 1
$PY tools/mono_artifact_probe.py summarize build/probe/sweep2-0.json build/probe/sweep2-1.json --out build/probe/summary.json
$PY tools/mono_artifact_probe.py drive --out build/probe/drive.json
$PY tools/run_case.py M5A M5B --results build/repro/results
$PY tools/measure_mono_pulse_2x.py --case M5A --out build/repro/pulse2x    # and M5B
$PY tools/verify_m5a_filter2x_i2s.py --case M5A --pulse2x --timeout 2900 --outdir build/p2x/full-M5A \
    --verification build/p2x/M5A.txt --wav build/p2x/M5A-i2s.wav --record build/p2x/M5A.json --audio build/p2x/M5A-scored.wav
```

Probe records: controls at `792df7a`, sweep at `5765cdf`, drive at `3d8bcb6`, summary at
`d1fe013`; probe and model sources clean in each. The Verilator on the box is 5.053 devel.
