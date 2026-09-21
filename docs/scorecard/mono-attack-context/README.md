# Mono attack-context diagnostic

The frozen Mini V3 patch was rendered three times per waveform and context.
All 24 renders qualified and all repeat ranges were zero at the measurement
resolution. MIDI 84 on its own measures 7.33 ms (saw) / 7.46 ms (pulse).
After another MIDI 84 with a 3.4-second gap it measures 3.10 / 2.88 ms.
After MIDI 72 at that gap it measures 3.21 / 2.85 ms. A 5-second gap after
MIDI 84 gives 3.56 / 3.38 ms.

This reproduces the four-to-five millisecond context difference seen between
M5A and M5B without changing the attack knob. Changing the preceding pitch
has little effect at the tested gap. These are audio-envelope measurements;
they do not identify the plugin's internal VCA state. Delayed isolated-note
controls will distinguish preceding-note history from absolute event time.

`phase1.json` preserves the first bounded experiment; every WAV is frozen
with its SHA-256. The qualified reference-integrity check detected the
injected clicks and found none in the clean render. No sound or tolerance
was changed. Wrong-then-right: 0 corrected audio measurements / 24 renders.
