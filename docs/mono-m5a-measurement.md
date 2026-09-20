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

## True 2× oscillator path

The integrated 2× path initially measured −36.232 dB inharmonic energy, 10.995
dB above the qualified Surge result. Inspection found that the decimator's FIR
ringing drove its saturating Q1.15 output over full scale on 1,080 of 33,600
samples. A 10% headroom sweep still clipped one MIDI 2 sample at 33,420 LSB.
Fifteen percent headroom kept the complete MIDI 0–127 sweep below the rail; the
worst filtered peak was 31,565 LSB at MIDI 2. The corrected path measured
−46.943 dB, 0.283 dB above
Surge, with a fundamental offset of −0.0052 cents. Its RMS is −6.377 dBFS versus
Surge at −19.995 dBFS, a +13.620 dB level difference in the refreshed run.
The component rig's oscillator level is therefore not calibrated to the chip's amplitude scale,
and the close spectral score is not an overall sound-quality pass.

The captured report and all three WAVs are in the
[M5A component evidence bundle](scorecard/mono-m5a-component-2x/report.json).

Two headroom candidates failed before the complete pitch sweep passed (2/3,
66.7% wrong-candidate rate); the first spectrum is retained because it exposed
the missing saturation precondition. The integrated quick default scenario is
bit-exact after the headroom change. `tools/run_case.py M5A --no-audio` returned
`not run` (exit 2): the repo still lacks a qualified Mono envelope reference,
so the harness will not report an ungrounded full-patch result.

The JSON report and all three WAV hashes are produced by
[`tools/measure_mono_case.py`](../tools/measure_mono_case.py). A missing or
unusable plugin, silent/non-finite output, wrong fundamental, or estimator
refusal returns `REFUSED` and writes no measurement numbers.
