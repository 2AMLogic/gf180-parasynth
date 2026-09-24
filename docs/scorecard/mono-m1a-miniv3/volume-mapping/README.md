# M1A fixed volume candidate and oscillator diagnosis

The fixed **−4 dB patch output-volume challenger** improves M1A from **3 passing / 3 failing / 1 unqualified** components to **4 / 2 / 1**. It changes only `vol`: 0.45 to 0.283930804. No DSP implementation or scoring tolerance changes. All evidence here is **model-only**. The complete case remains a **valid fail**: the qualified envelope metric (attack 10–90%, waveform-fit) and harmonic shape still fail; this candidate moves the GAIN component inside tolerance and preserves every other verdict.

| Property | Baseline | Volume −4 dB | Outcome |
| --- | ---: | ---: | --- |
| Pitch error | −0.05026 cents | −0.05059 cents | pass preserved |
| Worst harmonic error | 19.82259 dB | 19.82331 dB | failure preserved |
| Envelope attack (10–90%) | +8.10889 ms | +8.10889 ms | failure preserved (provisional 10 ms amp attack is the finding) |
| Envelope release (T20) | +8.47917 ms | +8.47917 ms | pass preserved |
| Worst signed gain error | +4.97474 dB | −1.04361 dB | new pass |
| Output clipping | 0% | 0% | pass preserved |
| Filter envelope | unqualified mapping | unqualified mapping | unqualified property, not a metric |

The three candidate gain errors are **+0.97465, −1.04361 and −0.83170 dB**. No per-note harmonic pass is lost. The tiny aggregate harmonic change remains visible; it does not cross a pass boundary. The default/baseline patch stays unchanged, and its newly rendered WAV exactly reproduces the committed model-audio SHA-256. Candidate WAV SHA-256: `628a6a312288ed8dc817be0152329cc9de2fe3ff6e239d987c7f80423a2ba61c`. The rescore was rerun under the qualified estimator (`m1a-envelope-score-v1`, 2026-09-23 build host): attack is unchanged by construction — output volume cannot touch a timing measurement — and the harmonic vector moves only in the seventh digit.

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

The 10–90% attack is now measured on both sides by the waveform-domain fit qualified against 56 known signals (worst error 0.50 ms, `tools/measure_mono_m1a_reference.py: attack_fit`); the RMS windows stay in the suite as controls that must remain red. The model's provisional 10 ms amplitude attack measures **+8.11 ms** against the reference's 0.27–1.64 ms — inside the envelope finding, unchanged by the volume candidate (a gain change cannot move a timing measurement, and the rescore confirms it does not). The isolated-recording interference diagnosis below stands as recorded; it is a hypothesis for the harmonic-shape failure, not a fix. No global envelope adjustment was made, by the candidate or otherwise.

Analysis version `m1a-envelope-score-v1` scores the case: required metrics are fundamental/harmonics, envelope (worst-normalized attack/release) and bass level. M1A is a **valid fail** — 21 valid cases on the board, six whole-case passes. The volume candidate is a component improvement; it does not turn the case green while harmonic shape and attack fail.

Reproduce the fixed candidates and diagnostic (no plugin needed):

```sh
python3 tools/measure_m1a_volume_mapping.py
python3 tools/diagnose_m1a_oscillator_mapping.py
python3 -m pytest -q tools/test_mono_m1a_score.py tools/test_measure_mono_m1a_reference.py tools/test_measure_m1a_volume_mapping.py
```

`report.json` binds all three complete phrases, configurations, source/reference hashes, component vectors, signed per-note partial errors and preservation checks. `oscillator-diagnosis.json` binds the isolated controls and reports the cross terms. Twenty-five focused tests pass. **Wrong-then-right: one scoring-contract correction (the undeclared Model D gate); zero discarded sound measurements.** The earlier four reference-apparatus corrections remain recorded in the baseline evidence.
