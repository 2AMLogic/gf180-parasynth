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
| Pitch | 1 cent | Fixed pitch screening bound; conversion is 1200 cents per octave |
| Harmonic shape | 1 dB maximum absolute partial error | A missing measurable partial is compared against that signal's measured floor; two below-floor sides are explicitly unmeasured |
| Foldback alias energy | 3 dB excess | Only predicted above-Nyquist image energy above the reference is penalized; cleaner output scores zero |
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
candidate, ladder drive 0.75, and the chip's square wave for Mini V3's measured
47.9% pulse. The drive setting is one measured intervention against the frozen
reference: it reduced both saw events' harmonic error and excess alias energy
in the oscillator/mixer/ladder probe; M5A's complete seven-property result
remains the acceptance check. The stage and drive measurements are preserved in
[`signal-path-v1.json`](mono-m5a-miniv3/signal-path-v1.json) and
[`signal-path-drive-v1.json`](mono-m5a-miniv3/signal-path-drive-v1.json).
A separate short integration
stimulus exercises both waveforms at MIDI 84/96 through SPI writes and the
production I2S serializer, then compares every emitted I2S word bit-exactly to
`SynthTopModel`. That integration check makes no full-envelope claim; the
scorecard's complete envelope and sound comparison is explicitly model-based.

## Stimulus timing contract (M5A and M5B, #372)

The scorer cuts every window at an absolute position in the decoded WAV, so
the SPI stimulus is part of the case definition, and so is where it lands.

- **Timing map.** Segment k starts at the manifest's `offset_samples[k]`, the
  scorer's own map, and not at the sum of `duration_s` and silence. The
  reference segments were trimmed by their 44-sample host latency, so the two
  differ by 44 samples per segment. An event is heard at sample
  `offset_samples + round(on_s × 48000)` and released at
  `offset_samples + round((on_s + gate_s) × 48000)`, rounded to the nearest frame.
- **Wire delay.** i2s_tx sends core frame f's sample in period f + 1
  (contract 13, D = 1). Each GATE_ON and GATE_OFF is therefore applied in
  core frame (map sample − 1).
- **Permitted error: 0 frames, for every GATE_ON and GATE_OFF.** This comes
  from the link, not from a candidate. A write applies in the frame after the
  one its CS_N acceptance falls in (DR 0007 §5), and the host chooses that
  frame when it starts the transaction.
- **Check.** `tools/check_phrase_schedule.py` reads the bench's pin-side write
  log and compares it with the manifest. It does not consult the scheduler.
  It returns FAIL on any offset, and REFUSES malformed, unordered or
  unpredicted logs, missing or extra gates, and an inconsistent map.
  `verify_synth_top.py --m5a` runs the same check and REFUSES a phrase that
  misses the map. The scheduler's `legacy-relative` mode (`--m5a-schedule
  legacy-relative`) is the control that must be refused. It was refused on the
  iverilog smoke bench: `m5-schedule-372/smoke-legacy-relative/`.
- **Scope: SPI only.** The absolute waits are solved against SPI landing
  (`rtl-sketch/tb_top_bx_timing.py`). The FPGA UART phrase
  (`fpga/uart_host.py --fixture m5a`, `fpga/verify_uart_bridge.py`) reads
  each wait as a UART frame count. It is a smoke fixture with no claim on
  this map, and R1 pins its bytes as `run-m5a`. It therefore passes
  `schedule="legacy-relative"` explicitly, and `tools/test_r1_capture.py`
  asserts that R1's bytes are unchanged.

**What the earlier stimulus did.** It used relative waits with a fixed
10/16-frame reserve and gate waits shortened by 3 frames. Measured against
the map above, with the link timing that reproduces the bench's stored traces,
every gate landed **late**:

| | lead-in | later GATE_ONs (first event aligned) | gate length |
|---|---:|---|---:|
| no override | +55 | 0, −5, +36, +31 | −2 |
| saw drive 0.35 | +58 | 0, −11, +33, +22 | −2 |
| saw cutoff + volume | +53 | 0, −11, +28, +17 | −2 |

The "up to 27 frames early" figure in #343 was measured against the
duration+silence cursor with the first event aligned. On that cursor the same
runs give 0, −11, −16, −27 frames. The 44-sample map difference and the
constant lead-in were both invisible to that check. The absolute schedule
places all 8 gates of M5A and M5B with an offset of 0, with or without
overrides.

**Stale receipts.** Every SPI→I2S M5 receipt recorded before this change used
the earlier stimulus. Its decoded WAV, its hashes and any score cut from it do
not describe the current stimulus. They remain valid as records of that
stimulus, and must be re-run before anyone quotes them as the M5 case:
`mono-m5a-miniv3/filter2x-spi-i2s.txt` and `filter2x-i2s.wav`,
`mono-m5a-miniv3/saw-cutoff-20khz-i2s.*` and `saw-cutoff-20khz-scored.wav`,
`m5-artifacts-333/{repro,probe,pulse2x-rtl,saw-drive/confirm}/`, and
`mono-pulse-2x/rtl/`.
