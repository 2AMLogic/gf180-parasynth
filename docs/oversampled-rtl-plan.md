# 2× oscillator RTL

The voice can render saw oscillators through a 96 kHz PolyBLEP pair and a
31-tap Q15 decimator. Each oscillator has its own phase and FIR history, so
interleaving voices does not mix filter state. The model retains that state
across frame and note boundaries.

The internal phase advances by `2 * floor(inc / 2)` per 48 kHz frame. The
PolyBLEP pair shares the base reciprocal; the reciprocal is an explicit
function input so it updates correctly during glides. Fifteen percent input
headroom keeps FIR ringing below the decimator's saturating Q1.15 output across
MIDI notes 0–127. Full-scale input clipped 1,080 of 33,600 MIDI 84 samples; a
10% reduction still clipped one MIDI 2 sample. With 15% headroom, the worst
filtered peak is 31,565 LSB at MIDI 2, below the 32,767 positive rail.

## Evidence

`verify_voice.py --set quick --osc2x` passes 64,416 frames with
every sample, tap and final state identical to `VoiceFx(oversample_2x=True)`.
The worst `go`-to-`sample_valid` latency is 198 cycles in the 256-cycle frame.
The focused default, glide and paraphonic sequence also passes 4,800 frames
exactly. `model/test_oversampled_osc.py` checks high-note spectral behavior,
FIR history across play boundaries and the independent Surge XT target.

The valid MIDI 84 component comparison measures −46.943 dB inharmonic energy,
0.283 dB from the qualified Surge Type 2 reference. Its refreshed RMS is
13.620 dB above that reference, so the component comparison validates spectral shape only;
absolute level is not calibrated and this is not full-patch acceptance. The
complete M5A run returns `not run` because the repository has no qualified
Mono envelope reference.
