# M1A fixed volume candidate and oscillator diagnosis

The fixed **−4 dB patch output-volume challenger** improves M1A from **3 passing / 2 failing / 2 unqualified** components to **4 / 1 / 2**. It changes only `vol`: 0.45 to 0.283930804. No DSP implementation or scoring tolerance changes. All evidence here is **model-only** and the complete case remains **NO VERDICT**.

| Property | Baseline | Volume −4 dB | Outcome |
| --- | ---: | ---: | --- |
| Pitch error | −0.05026 cents | −0.05059 cents | pass preserved |
| Worst harmonic error | 19.82259 dB | 19.82331 dB | failure preserved |
| Release error | +8.47917 ms | +8.47917 ms | pass preserved |
| Worst signed gain error | +4.97474 dB | −1.04361 dB | new pass |
| Output clipping | 0% | 0% | pass preserved |
| Attack / filter envelope | unqualified | unqualified | no verdict preserved |

The three candidate gain errors are **+0.97465, −1.04361 and −0.83170 dB**. No per-note harmonic pass is lost. The tiny aggregate harmonic change remains visible; it does not cross a pass boundary. The default/baseline patch stays unchanged, and its newly rendered WAV exactly reproduces the earlier committed SHA-256 `50add9b962caa5f37716f030c7254141664ddd238ab13300708ae6f40ddd8c05`. Candidate WAV SHA-256: `628a6a312288ed8dc817be0152329cc9de2fe3ff6e239d987c7f80423a2ba61c`.

## What the isolated recordings establish

Across all three events, isolated open-filter controls put oscillator 2 **−3.4892 to −3.4903 cents** below the exact octave of oscillator 1. This is a measured mapping difference. The estimator is checked against independently synthesized twelve-partial signals with known 0 and ±3.49 cent offsets at the same bass notes.

At the repeated MIDI 36 notes, the relevant isolated partial levels barely change: oscillator 1 h8 changes 0.00080 dB and oscillator 2 h4 changes 0.00010 dB. But adding those two recorded outputs produces an **18.14 dB** h8 drop. The reference patch drops **19.69 dB**, and disabling its filter envelope leaves that late-window change identical.

A shared-frequency complex projection measures the interference directly:

| MIDI 36 onset | Relative partial phase | Coherent power relative to incoherent sum |
| --- | ---: | ---: |
| 0.1 s | −95.10° | −0.40 dB |
| 4.1 s | +170.44° | −18.54 dB |

The power decomposition is independently qualified on analytically known constructive and destructive two-sine sums. This demonstrates strong cancellation in the summed isolated controls. It does **not** claim that their sum is the exact internal input of the nonlinear reference filter, or that interference alone explains every model/reference error.

The bounded model counterfactual uses the measured median octave offset and the same −4 dB output volume. It **does not solve the sound failure**: worst harmonic error becomes 19.88760 dB and six existing per-note harmonic passes are lost. Its h8 change between repeated notes is +0.23 dB, versus −19.69 dB in the reference. Keep this run diagnostic only. Relative oscillator phase and note-history behavior need qualification before treating a static detune correction as the complete mapping.

## Envelope and corroboration status

The independently defined 8 ms rise still measures 19.04–20.79 ms with the 40 ms bass window. That window remains qualified only for release. Attack and internal filter-envelope mapping explicitly retain no verdict; a response to disabling a control is not a calibrated time trajectory. No global envelope adjustment was made.

Analysis version `m1a-partial-score-v2` uses the case's declared measurements: fundamental/harmonics, envelope and bass level. Model D is a **separate, currently unmeasured corroboration milestone**, not an extra mandatory metric. Removing that accidental extra gate cannot turn this case valid while envelope evidence is missing. The official board remains 20 valid / six fully passing.

Reproduce the fixed candidates and diagnostic (no plugin needed):

```sh
python3 tools/measure_m1a_volume_mapping.py
python3 tools/diagnose_m1a_oscillator_mapping.py
python3 -m pytest -q tools/test_mono_m1a_score.py tools/test_measure_mono_m1a_reference.py tools/test_measure_m1a_volume_mapping.py
```

`report.json` binds all three complete phrases, configurations, source/reference hashes, component vectors, signed per-note partial errors and preservation checks. `oscillator-diagnosis.json` binds the isolated controls and reports the cross terms. Twenty-three focused tests pass. **Wrong-then-right: one scoring-contract correction (the undeclared Model D gate); zero discarded sound measurements.** The earlier four reference-apparatus corrections remain recorded in the baseline evidence.
