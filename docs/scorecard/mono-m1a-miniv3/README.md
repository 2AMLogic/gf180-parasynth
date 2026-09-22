# M1A frozen round-bass reference

This is frozen Mini V3 software reference audio. Its release measurement is
qualified; its fast attack measurement is not. It is not a Model D cross-check. The official scorecard coverage is unchanged.

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

The 40 ms RMS window is **not qualified for attack**: an independent 8 ms
10–90% linear rise at MIDI 36/43 measures about 19–21 ms, depending on carrier
phase. `qualify_attack_basis()` preserves all eight observations. Historical
attack observations in the capture manifest are unqualified; the current
analysis marks `attack_valid=false` and `release_valid=true`. Neither changing
the synth attack nor reporting a passing attack follows from these numbers.
This is the fourth corrected apparatus claim (release qualification had been
extended to attack without testing it).

The first model mapping is fixed in `tools/mono_m1a_score.py` before rendering.
It uses the shared selected engine, the measured quieter-octave level ratio,
and measured release. Attack/decay/sustain, resonance, and filter-envelope
shape remain explicitly provisional. The runner reports component measurements
but keeps the required envelope and Model D cross-check at **NO VERDICT**.
