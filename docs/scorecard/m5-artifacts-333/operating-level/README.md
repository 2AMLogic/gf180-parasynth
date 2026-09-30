# M5 harmonic shape: lower-drive operating-level candidate (#333). Negative result

This is model evidence on the next image's engine: pulse2x, with rectangles at
0.74. The instrument is `tools/measure_m5_operating_level.py`. The budget (three
drives), the volume calibration and the selection rule were all fixed in its
docstring before anything was rendered.

- **Mechanism tried.** One shared ladder drive for the M5 patch, lowered from 0.75. The output `vol` register restores level.
- **Volume calibration.** Done once, on the development held saw at MIDI 84. This is a preset change only; no RTL is involved.
- **Selection.** On M5A (84/96). A candidate must lower Harmonic shape while meeting all of these:
  - M5A Gain within ±0.5 dB of the baseline;
  - foldback ≤ 3 dB;
  - attack no worse by more than 0.5 ms;
  - no clipping;
  - no reduction of upper wanted power at saw or pulse, MIDI 84/96.

| drive | vol factor | M5A harmonic | M5A gain | M5A foldback | M5A attack | M5B harmonic | M5B gain | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 0.75 (baseline) | 1.000 | 7.561 | −1.544 | 2.206 | 5.271 | 5.962 | 2.049 | — |
| 0.50 | 1.389 | **6.611** | 1.420 | 2.313 | 5.438 | 5.752 | 1.845 | **rejected: pulse darker** at MIDI 84 (−0.10 dB) and 96 (−0.31 dB) |
| 0.35 | 1.923 | 7.960 | 1.421 | 0.316 | 5.438 | 5.650 | 1.728 | **rejected:** worse than baseline; pulse 96 darker |
| 0.25 | 2.653 | — | — | — | — | — | — | **REFUSED:** vol 1.13 (saw) to 1.19 is outside the host's 0..1 volume range |

**No candidate was chosen, and nothing is promoted.** The M5 case records and
presets are unchanged.

## What the three runs localize

A single drive cannot serve both waveforms. The per-event worst partial error
(model minus Mini V3, dB) shows it:

| event | drive 0.75 | 0.50 | 0.35 |
|---|---:|---:|---:|
| saw MIDI 72 (M5B, untouched by selection) | 5.5 | 4.4 | **3.2** |
| saw 84 | 6.0 | 4.5 | **3.6** |
| saw 96 | 7.6 | 4.6 | **3.4** |
| pulse 84 | 5.0 | 5.8 | 5.7 |
| pulse 96 | 5.3 | 6.6 | **8.0** |

- The saw brightens monotonically as drive falls. This confirms the localization: the saw's deficit is the tanh operating level. The improvement also holds at the held-out MIDI 72.
- The pulse goes the other way. Its segments run at the 14,073 Hz calibrated cutoff, not the saw's 20 kHz. There, drive-induced saturation was supplying upper partials that the linear 4-pole at 14 kHz removes. Less drive exposes the linear loss.
- Unwanted energy stays within its limits. Saw relative unwanted is −48.2 → −49.8 dB at MIDI 84, and foldback stays under 3 dB in every run that rendered.

## Next mechanism, not yet run

**A per-waveform operating level.** R1 already ships `m5a-saw` and `m5a-pulse`
as separate presets, and the M5 phrase already overrides cutoff and volume per
saw segment. The next candidate is therefore a saw-only drive override with the
pulse left at 0.75, measured as its own experiment.

Reading this batch's per-event data suggests M5A harmonic would fall to about
5.3 dB, limited by pulse 96. That is a prediction for choosing the experiment,
not a result. The pulse's remaining ~5 dB deficit is then the linear ladder at
14 kHz, which is a cutoff or voicing question for the pulse preset.

Wrong-then-right in this step: 1. The drive-0.25 run crashed in the scorer's
volume guard instead of being recorded. The tool now records a refused
candidate as REFUSED, and the rule treats it as inadmissible.
