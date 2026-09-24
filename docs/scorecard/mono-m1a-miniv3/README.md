# M1A frozen round-bass reference

This is frozen Mini V3 software reference audio. Its release measurement was
qualified at capture; its fast attack is now measured by a waveform-domain
fit qualified against known signals (see `model-comparison.md`). It is not a
Model D cross-check.

The dry phrase plays MIDI 36, 43, 36 over 4.6 seconds, then finishes the
release. It uses a saw plus a quieter octave saw, low resonance, and a short
filter-envelope response. Three independent renders are byte-identical.
Open-filter isolation controls verify both saw waveforms and their octave
relationship; the upper oscillator measures 5.44 dB quieter. The resting
cutoff's self-oscillation calibration measures 1056.435 Hz in all three runs.

Removing the filter envelope changes the output above the zero repeated-run
variation for 42.7–60.4 ms after the note starts. This is the measured duration
of the output difference, not the hidden cutoff-envelope time. The initial
250 ms harmonic-window control could not distinguish that short transient;
its refusal and near-zero h4 differences remain recorded.

The lead's 5 ms RMS window failed a known bass-frequency release by up to
28.45 ms. The qualified 40 ms window measures the same 230.259 ms release
within 3.31 ms, and phase-varied MIDI 36/43 tests remain within 10 ms.
The reference's measured releases are 117.6–123.1 ms on this explicit basis.
No M5A/M5B scorer or tolerance changed. The first 100 ms spectral window also
refused its insufficient cycle count; the bass spectral window is 250 ms.

All reference and control WAVs are hash-bound in `manifest.json`, with
plugin/host identity, parameter names/readbacks, the MIDI timeline, source
hashes, and the clean/injected reference-integrity result. A test reproduces
the pitch, level and release measurements from frozen audio without plugins.

Wrong-then-right: three apparatus checks needed correction before the reference
qualified (spectral window, envelope window, transient control). No refused
attempt was published as a valid reference. The final phrase was rendered
three times, with all three identical.

**Attack (qualified 2026-09-22, lane F).** The 40 ms RMS window is not
capable of fast bass attacks: a known 8 ms 10–90% linear rise measured
19–21 ms on it, depending on carrier phase — the historical reason the case
was no-verdict. The 10–90% attack is now measured on both sides by a
waveform-domain fit (`tools/measure_mono_m1a_reference.py: attack_fit`),
qualified against 56 known signals with worst error 0.50 ms and a 1.0 ms
refusal gate; the RMS windows stay in the suite as controls that must remain
red. The reference's own attacks measure 0.27–1.64 ms.

The first model mapping is fixed in `tools/mono_m1a_score.py` before rendering.
It uses the shared selected engine, the measured quieter-octave level ratio,
and measured release. Attack/decay/sustain, resonance, and filter-envelope
shape remain explicitly provisional — the model's provisional 10 ms amplitude
attack is the envelope finding of the first valid comparison (a fail).
