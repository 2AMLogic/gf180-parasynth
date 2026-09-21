# Mono attack-context diagnostic

The frozen Mini V3 patch was rendered three times for each of six contexts
and both waveforms. All 36 renders qualified. Every repeat range was zero
at the measurement resolution. The attack knob and MIDI 84 target were fixed.

| Context | Saw attack (ms) | Pulse attack (ms) |
| --- | ---: | ---: |
| delayed84_at4p1 | 8.292 | 7.958 |
| delayed84_at5p7 | 8.542 | 8.312 |
| isolated84 | 7.333 | 7.458 |
| repeat84_gap3p4 | 3.104 | 2.875 |
| from72_gap3p4 | 3.208 | 2.854 |
| repeat84_gap5 | 3.562 | 3.375 |

The delayed isolated controls distinguish event time from preceding-note
history: at 4.1 seconds, an isolated note measures 8.292 / 7.958 ms, while
a note following MIDI 84 measures 3.104 / 2.875 ms. A preceding MIDI 72
gives nearly the same result. At 5.7 seconds, the isolated-versus-repeated
difference remains approximately 4.94–4.98 ms. Elapsed time alone does not
explain the attack discrepancy between the frozen lead phrases.

These are audio-envelope measurements. They establish a prior-note effect,
but do not identify the plugin’s internal VCA mechanism. Keep that distinction
when changing the model; a single global attack adjustment cannot match both
contexts. No sound code or tolerance was changed.

`phase1.json` preserves the first 24-render experiment. `report.json` adds
12 delayed-isolated controls, reusing the original WAVs only after checking
their hashes, event timelines, plugin/host identity and analysis sources.
Every measured WAV is committed. The clean reference-integrity run found no
unprompted clicks and its injected-click control fired.

Reproduce with `python3 tools/measure_mono_attack_context.py` in a clean
checkout with the qualified Mini V3 host installed, using a fresh output
directory. Wrong-then-right: 0 corrected audio measurements / 36 renders.
