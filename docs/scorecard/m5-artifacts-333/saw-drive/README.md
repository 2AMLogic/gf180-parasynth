# M5 saw-only drive on the `m5a-saw` preset (#333)

The instrument is `tools/measure_m5_saw_drive.py`. The budget (saw drive 0.5 / 0.35 / 0.3), the volume calibration
(once per drive, on the development held saw at MIDI 84) and the selection rule were fixed in its docstring before
any render. Pulse segments keep drive 0.75. The engine is the next image's: pulse2x with rectangles at 0.74. The
saw is bit-identical between R1 and pulse2x, so every saw number below also holds on R1's engine. All records are
at `69523c3`, with sources clean.

## Result

| saw drive | saw vol | M5A harmonic | M5B harmonic | M5A gain | M5B gain | M5A/M5B foldback | M5A/M5B attack | M5B release (tol 125 ms) | clipping |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.75 (baseline) | 0.427 | 7.561 | 5.962 | −1.544 | 2.049 | 2.206 / 1.913 | 5.271 / 6.125 | −30.00 | 0 |
| 0.5 | 0.593 | **5.297** | **4.957** | −1.544 | 1.845 | 2.313 / 2.312 | 5.438 / 6.125 | −33.92 | 0 |
| 0.35 | 0.821 | **5.297** | **4.957** | −1.544 | 1.728 | 0.316 / 0.317 | 5.438 / 6.208 | −35.85 | 0 |
| 0.3 | 0.952 | **5.297** | **4.957** | −1.544 | 1.700 | 0.413 / 0.407 | 5.438 / 6.208 | −35.88 | 0 |

Attack (errors in ms against a 5 ms tolerance) moves by at most +0.17 ms. That is inside the 0.5 ms preservation
allowance, but **attack still fails its absolute tolerance** (1.09× M5A, 1.24× M5B at 0.35), as it did before.

- **The prediction held.** M5A harmonic shape is 5.297 against the ~5.3 predicted from the shared-drive run's per-event data. M5B is 4.957.
- **Both case figures are now set by the pulse, not the saw:** M5A by pulse MIDI 96, h9 −5.30 dB; M5B by pulse MIDI 84, h9 −4.96 dB. That is why every candidate reads the same.
- **Invariance control.** Every pulse event is identical to the baseline in all three candidates, which the rule checks. Gain is therefore unchanged at −1.544 (pulse 96).
- **All three candidates pass the preservation set:**
  - |Gain| within 0.5 dB;
  - foldback ≤ 3 dB (it falls, to 0.3–0.4 dB below drive 0.5);
  - attack worse by at most 0.17 ms (M5A) and 0.08 ms (M5B);
  - no clipping;
  - saw is brighter at the development notes (upper wanted +0.8 to +3.1 dB).
- **F1.** F1A/F1B/F1C pass at 0.76/0.42/0.36 (`f1/`), the same as published. By construction the change cannot reach them; this is the check.

The saw's worst partial error against Mini V3, per event, in dB:

| saw event | 0.75 | 0.5 | 0.35 | 0.3 |
|---|---:|---:|---:|---:|
| M5A 84 (dev) | 5.96 | 4.53 | 3.65 | **3.05** |
| M5A 96 (dev) | 7.56 | 4.64 | 3.42 | **2.82** |
| M5B 72 (untouched) | 5.51 | 4.43 | 3.18 | **2.81** |

The untouched held-note probe (MIDI 36, 48, 60, 72, 108, 120):

- At 0.35 and 0.3, relative unwanted energy improves at every note: −1.3 to −8.7 dB (0.35) and −1.6 to −11.4 dB (0.3).
- At 0.5 it is +0.1 to +1.0 dB at 36–72. MIDI 48 (+1.01 dB) is the one point just outside the 1 dB confirmation bound.
- Upper wanted power rises at every note, and there are no output rail samples.

## The selection rule had a gap, disclosed rather than patched over

The frozen rule selects on the M5A *case* harmonic-shape error. That value is the worst partial over all events, and
it is now pulse-limited, so **all three candidates tie at 5.297**. The rule did not specify a tie-break. The tool
reported 0.3, but only because the tie fell to string ordering in `sorted`, which is an artifact and not a decision.
Wrong-then-right: 1.

Any tie-break chosen now is post hoc, so this goes to the reviewer as a recommendation, not a selection.
**Recommend saw drive 0.35 (vol 0.821).**

- The saw-event error measured on the development events tie-breaks in favour of 0.3, and 0.3 confirms at the held-out MIDI 72 (2.81).
- But 0.3 needs saw vol 0.952: 0.43 dB below the host's 0..1 limit, leaving no room for any later level trim. 0.35 keeps 1.7 dB of range.
- 0.35 gives up about 0.6 dB of saw partial error, and passes every confirmation point.

## Delivery: preset data, but it changes R1-bound bytes

This is a host/preset change: two register values on `m5a-saw`, the ladder `gain` (from drive) and `vol`. There is
**no RTL change.** It does, however, change command bytes that R1 pins:

- `fpga/release/r1-candidate.json` binds `presets["m5a-saw"].image_sha256 = 316934c7…`, and the current tree still matches it.
- With the candidate, the image recomputes to `c843d2c7…`.

It therefore belongs to **the next image or profile only**, with the pulse2x image (#205/#344) or a new host profile.
R1's `m5a-saw` must stay as published for the rollback. No production path equivalence was run for it. The SPI→I²S
path carries the same register writes the host sends, so the next image's M5A phrase run covers it once the preset
is committed there.

## Remaining limit

Both M5 cases are now held by the **pulse** segments' brightness deficit, 5.0–5.3 dB. The pulse runs at the
Mini V3 calibrated 14,073 Hz cutoff, where the linear 4-pole removes upper pulse partials. That is a cutoff/voicing
question for the `m5a-pulse` preset, filed separately and not pursued here.

## Fresh-condition confirmation: NOT confirmed (`confirm/`)

`tools/confirm_m5_saw_drive.py` checks drive 0.35 against 0.75 at 28 conditions not inspected in the experiment:

- notes: MIDI 42, 54, 66, 78, 90, 102, 114;
- cutoff: 20 kHz and 8 kHz;
- resonance: 0 and 0.5.

The rule was fixed in its docstring before any render, and the rule has its own tests. **21 of 28 pass, so it is not
confirmed.**

| region | result |
|---|---|
| resonance 0, both cutoffs (14 points) | all pass: relative unwanted −1.1 to −7.0 dB, brighter, ladder deficit smaller, level within ±1.4 dB |
| resonance 0.5, MIDI 78–114 | pass |
| **resonance 0.5, MIDI 42/54/66, both cutoffs (6 points)** | **fail: relative unwanted +1.0 to +4.2 dB** |
| resonance 0.5, MIDI 114, 8 kHz | fail: level drift +3.67 dB |

At resonance 0 the candidate is better everywhere, notes 42–114. With resonance, which the player reaches on CC71,
lower drive raises the low notes' unwanted energy relative to the signal. **The preset is not promoted.**

The next question is what the resonant low-note unwanted energy is: images, or residual from the resonance peak.
Only after that can a drive be tied to resonance, and that is its own experiment.

**Production path, run before this verdict.** The M5A/M5B SPI→I²S phrases with saw drive 0.35 on saw segments are
bit-exact with the model: M5A 1,305,543 periods, M5B 729,543, 73 writes each (`confirm/M5A.txt`, `M5B.txt`). These
ran with the 16-frame write margin. The three extra writes pushed each segment's first event about 10 frames late in
total, which is the +10 periods. The margin is now 22 when a saw drive is overridden. This is delivery evidence for
the mechanism, not a promoted preset. R1's `m5a-saw` is unchanged.
