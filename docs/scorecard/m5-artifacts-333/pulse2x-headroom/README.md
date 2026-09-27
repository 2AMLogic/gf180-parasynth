# Pulse2x decimator headroom: rectangle gain 0.74 (#333)

**Decision:** in the PULSE2X=1 image, scale rectangular 2x substeps by
**24248 / 32768 (0.74)**. The saw keeps **27853 (0.85)**, so saw audio and R1 stay
bit-identical. This is model evidence produced with `tools/measure_pulse2x_headroom.py`,
which wraps the unchanged `voice_fx._render_2x`. No RTL was changed.

## The defect

The 0.85 headroom in front of the 31-tap Q1.15 decimator was sized on a saw
sweep. That sweep's worst peak was 31,565 at MIDI 2, and 0.85 still leaves the
saw 0.33 dB clear. A band-limited rectangle rings further than a saw: its
Gibbs peak alone is about 1.18× the plateau, and 0.85 × 1.18 > 1. At the top of
the range it is worse again.

The coarse integer sweep at 0.85 (`decimator.json`, MIDI 0–127, 23,760 samples
per note) shows every rectangle saturating:

| waveform (FILTER2X=1 duty) | notes that clip | clipped samples | worst clipped energy | worst peak |
|---|---|---:|---:|---:|
| pulse29 control → 47.9 % | MIDI 119–127 | 30,293 | −32.6 dB | 35,146 (−0.61 dB) |
| square | 119–127 | 33,125 | −33.5 dB | 34,462 |
| pulse25 | 108–127 | 30,566 | −24.0 dB | 37,072 (−1.07 dB) |
| pulse15 | 99–127 | 34,775 | −34.5 dB | 35,280 |
| saw | none | 0 | — | 31,565 |

A true 29 % pulse (35,604 at MIDI 127, −1.20 dB) is not reachable under
`VOICE_FILTER_2X`, because the pulse29 control encodes 47.9 % there. It is kept
as a diagnostic.

## Selection

The rule was frozen before the fine sweep (`MARGIN_DB = 0.1`):

- choose the largest gain with zero clipped samples, and a worst peak at least 0.1 dB under the rail;
- apply it to every reachable rectangle (square, 47.9 %, 25 %, 15 %);
- check on a **1/8-semitone** grid from MIDI 90 to 127, which covers glide increments between notes, at 48,000 samples per point.

| gain | result | limiting waveform |
|---|---|---|
| 0.80 | fail: 47.9 %, 25 %, 15 % clip | pulse25 34,891 at MIDI 127 |
| 0.78 | fail: pulse25 clips (22,520 samples) | |
| 0.76 | fail: pulse25 clips (5,046) | |
| 0.75 | fail on margin: no clipping, pulse25 peak 32,711 (−0.015 dB) | |
| **0.74** | **pass** | pulse25 32,274 at MIDI 127 (−0.13 dB); 47.9 % 30,621; square 30,003; 15 % 30,735 |
| 0.73 … 0.70 | pass | smaller, so not chosen |

The only reachable duty that needs 0.74 is the narrow 25 %. Without it, 0.77
would do. The frozen rule covers every exposed rectangle, so it is not excluded
after the fact.

## Cost, measured

- **Oscillator level:** −1.19 dB for rectangles, and 0 for saw. The probe test shows the saw is bit-identical and that an all-waveform scope would move it.
- **Output after the drive stage:** intended power moves by −1.17 to +0.15 dB at 31 of 32 probe points (`probe-074.json`). The tanh compresses most of the change at the M5 drive. The exception is pulse15 at MIDI 127, which rises +2.36 dB once the clipping that distorted it is removed.
- **M5 phrases** (`phrases-074.json`, pulse2x engine):

| | 0.85 (pulse2x as built) | 0.74 |
|---|---:|---:|
| M5A Gain (worst \|error\|, limit 3 dB) | 1.421 dB (saw 84) | **1.544 dB** (pulse 96, now the worst event) |
| M5B Gain | 2.091 dB | 2.049 dB |
| M5A / M5B foldback | 2.206 / 1.913 dB | 2.206 / 1.913 dB |
| harmonic shape, attack, release, pitch, clipping | unchanged | unchanged |

  M5A Gain gets 0.12 dB worse and stays well inside its limit. If that 0.12 dB matters, the host can compensate on the `m5a-pulse` preset's volume register; no RTL is needed.

- **Unwanted energy relative to intended** (32 probe points: 4 rectangles × 8 notes):
  - 25 points improve, down to −6.5 dB (pulse25 MIDI 114, where clipping was removed).
  - 7 points worsen. Only one is worse by more than 1 dB: pulse15 MIDI 84, +1.02 dB, at about −60 dB relative.
- **Upper wanted power:** within ±0.26 dB.
- **Rails:** oscillator rail samples go from 1,007–7,107 per clipping point to **0** at every probe point.
  - Mixes (`mix-074.json`): the default preset and a three-rectangle mix at 10 notes go from 30,965 oscillator rail samples to **0**.
  - Mixer and output rail samples are 0 before and after.

## Spec for the integration owner (added to #344)

1. **Model.** In `model/voice_fx.py`, add `_OS2_RECT_GAIN_Q15 = 24248`. In `_render_2x`, use it when `o.shape in TWO_EDGE`, and keep `_OS2_SUBSTEP_GAIN_Q15 = 27853` for saw.
2. **RTL.** In `rtl-sketch/polyblep_saw_pair.v`, under `ifdef VOICE_PULSE_2X`, select `rectangular ? 16'sd24248 : SUBSTEP_GAIN_Q15` for both `scaled0` and `scaled1`.
   - Without `VOICE_PULSE_2X` the logic is unchanged. The file hash is in R1's pin set, so this belongs to the new image only.
3. **Control.** Add `INJECT_BUG_VOICE_PULSE2X_RECT_HEADROOM`, which restores 27853 for rectangles. It must fail the RTL/model comparison. With the model at the old gain, `measure_pulse2x_headroom.py fine --gains 0.85` must report clipping.
4. **Evidence at the new head.**
   - Re-run `verify_voice --only waves3 --filter2x --pulse2x` and the M5A/M5B PULSE2X=1 phrases. Expect foldback 2.21/1.91 dB and gain 1.54/2.05 dB.
   - Re-run `stress-pulse --pulse2x` for the deadline. A multiplier constant does not change the schedule.

Wrong-then-right in this step: 0. The frozen margin rule rejected 0.75, which
clipped nothing but sat 0.015 dB under the rail. That is the rule working as
designed, not a correction.
