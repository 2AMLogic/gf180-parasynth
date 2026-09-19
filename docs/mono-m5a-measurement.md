# Mono M5A component measurement

This is the first valid mono comparison. It covers the measurable high-note
oscillator segment of scorecard M5A (“bright high lead”), at MIDI note 84, with
a single saw oscillator held for 700 ms. The full M5A patch remains unqualified
because the repository has no independently measured Mini V3 envelope-time
mapping; this record does not silently substitute a knob position for that
missing evidence.

The reference audio is rendered from Surge XT’s Classic oscillator by the
qualified `model/reference_rigs.SurgeRig` (Type 2 filter rig, with the filter
disabled for this oscillator measurement). The rig asserts the plugin bundle,
parameter names, oscillator type, shape, width, unison, effects and readbacks
before accepting audio. Surge is an external software reference and is not
claimed to be a physical Model D. The DUT is the integer PolyBLEP oscillator
used by `model/voice_fx.py`. Both signals are measured with the ground-truthed
`audio_measure` fundamental, harmonic and inharmonic-energy estimators.

Run it with:

```text
.venv/bin/python tools/measure_mono_case.py --out build/mono-m5a
```

The baseline run at commit `0bb807f` produced a valid comparison. The measured
fundamentals were 1046.502 Hz on both sides (DUT − reference: −0.0005 cents).
The inharmonic-energy measurement was −47.2265 dB for Surge and −31.0685 dB
for the unfiltered DUT, a **+16.158 dB DUT excess**. The prototype causal
oscillator filter reduces the DUT to −48.8775 dB, a 17.809 dB improvement and
1.651 dB below the reference. The fundamental remains −0.0005 cents from the
reference. Its upper harmonics are also more attenuated than Surge (the twelfth
is −30.21 dB versus −22.29 dB), so this is an aliasing experiment for review,
not an accepted sound-quality fix. It is a component result, not a pass on the
complete M5A case.

The JSON report and both WAV hashes are produced by
[`tools/measure_mono_case.py`](../tools/measure_mono_case.py). A missing or
unusable plugin, silent/non-finite output, wrong fundamental, or estimator
refusal returns `REFUSED` and writes no measurement numbers.
