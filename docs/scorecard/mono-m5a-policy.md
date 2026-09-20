# Mono M5A screening policy

M5A compares one fixed integer-model render with the frozen, raw Mini V3 3.12.0
software-synth recording in `mono-m5a-miniv3/`. Mini V3 is not a physical
Minimoog, and Model D was not available through the qualified host. This is a
screening comparison for the declared patch, not a hardware calibration.

The limits below are fixed for this case and are not tuned from candidate
errors. The board reports each unit separately and uses the worst normalized
error; a pass cannot compensate for a miss in another dimension.

| Measurement | Limit | Basis |
|---|---:|---|
| Pitch | 1 cent | Fixed pitch screening bound for the frozen patch |
| Harmonic shape | 1 dB maximum absolute partial error | Fixed per-partial screening bound |
| Foldback alias energy | 3 dB | Half-power convention on predicted above-Nyquist images |
| Attack, 10–90% | 5 ms | One 5 ms RMS-envelope measurement hop |
| Release, T20 | 125 ms | 10% of measured reference release (about 1.25 s) |
| Raw steady gain | 3 dB | Half/double amplitude screening range; no normalization |
| Clipping | 0.01 percentage points of samples at rail | Fixed dry-output ceiling |

The maximum Mini V3 cutoff knob is also qualified in Hz by a repeatable
self-oscillation ring measurement (three independent rig instances with the
same knob and emphasis settings, with the 16-sample host block pinned); the
frozen manifest records all three readings. A first reading with the host's
512-sample default block was 14,494.8 Hz, while the qualified 16-sample setup
repeated at 14,072.9 Hz. The default-block reading is discarded because the
host rate was an unasserted precondition. The model uses the repeated measured
frequency rather than interpreting the plugin's normalized `1.0` as Hz.

The capture's wrong-then-right rate is **2/6 measurements**: the saw-edge
transient detector and the unpinned-block cutoff reading were discarded; the
smooth-wave control (including five injected clicks) and the pinned-block
cutoff measurement were accepted. The manifest keeps both failures and their
controls.

dawdreamer 0.8.3 logs `error: attempt to map invalid URI
'/Library/Audio/Plug-Ins/VST3/Mini V3.vst3'` at plugin creation. Captures are
retained only when output is finite and non-silent, all pinned parameter
readbacks hold before and after rendering, and repeated pitch/waveform/level
measurements agree; the warning is preserved in the manifest rather than
hidden.

The full phrase is rendered by `model/voice_fx.py` with the selected 2x saw
candidate and the chip's square wave for Mini V3's measured 47.9% pulse. A separate short integration
stimulus exercises both waveforms at MIDI 84/96 through SPI writes and the
production I2S serializer, then compares every emitted I2S word bit-exactly to
`SynthTopModel`. That integration check makes no full-envelope claim; the
scorecard's complete envelope and sound comparison is explicitly model-based.
