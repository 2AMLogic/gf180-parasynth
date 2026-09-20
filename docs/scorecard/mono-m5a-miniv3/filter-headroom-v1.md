# Reconstructed 2× filter headroom and causal conversion

This experiment compares the reconstructed 2× ladder with its historical
int16 input clamp, the same offline chain with reconstruction headroom, and a
stateful causal FIR implementation. All rows render the frozen Mini V3 M5A
phrase, including both notes in each saw and pulse segment. The raw values and
per-event/per-partial diagnostics are in
[`filter-headroom-v1.json`](filter-headroom-v1.json).

The reference is the Mini V3 software-synth recording identified by the
manifest hash in the JSON report; it is not a physical-instrument recording.
The clamped and offline-headroom rows were freshly rendered from source
`49e3966`. The causal rows were rendered from clean source `dcad2b1` and reused
with that provenance recorded separately. One earlier report attempt completed
all six renders but failed during report assembly because its cutoff-step
diagnostic lacked a NumPy import; the import and a regression test were added,
and no result from that failed report attempt is treated as verdict evidence.

| Pulse model | Chain | Worst harmonic error | Foldback excess | Incremental result |
| --- | --- | ---: | ---: | --- |
| pulse29 | Clamped offline | 19.29801 dB | 10.67286 dB | Baseline |
| pulse29 | Headroom offline | 19.31971 dB | 9.73252 dB | Rejected: harmonic error regresses 0.02170 dB, although foldback improves 0.94034 dB |
| pulse29 | Headroom causal | 19.31980 dB | 9.73253 dB | Rejected for the same pulse29 regression |
| pulse479 | Clamped offline | 8.84583 dB | 10.96949 dB | Baseline |
| pulse479 | Headroom offline | 8.84583 dB | 10.27445 dB | Accepted as an incremental model improvement |
| pulse479 | Headroom causal | 8.84582 dB | 10.27447 dB | Accepted versus clamped; equivalent to offline headroom within the declared comparison deadbands |

The headroom diagnostic counts samples that the legacy int16 stage would
clamp. It found **20.312%** for pulse29 and **22.403%** for pulse479 at both
notes, with a maximum reconstructed value of 39,113 in Q1.15-scaled units.
The headroom candidate retains those values as signed int32 input to the
nonlinear ladder. The final decimator continues to saturate to the ladder's
output width.

Incremental acceptance compares all seven score properties and each measured
pulse partial. Its materiality deadbands are 0.01 cents for pitch, 0.01 dB for
spectral/gain properties, 5 ms for envelope measurements, and 0.01 percentage
points for clipping. These are regression deadbands, not relaxed M5A pass
limits. The strict scorecard still requires every property to meet its
existing tolerance. The causal pulse479 row remains a failing case: harmonic
shape, foldback, attack, and release are outside their current limits; pitch,
gain, and clipping pass.

The causal converter uses the same Kaiser cutoff and tap length as the offline
resampler, with Q2.30 coefficients and int64 accumulators. Chunked processing
matches one-shot processing exactly; reset repeats the impulse render. An
impulse peaks after **20 base frames (0.417 ms)**. A known cutoff coefficient
step first changes the quantized output after **4 frames**; 50% and 90% of the
paired output-difference energy arrive 27 and 50 frames after the step. The
causal chain retains the offline headroom result within the declared deadbands.

This report is **model evidence only**. The causal filter is not implemented
in RTL, and this candidate has not been verified through SPI → I2S. The
47.9%-duty waveform is itself model-only and has no RTL encoding, so pulse479
acceptance does not authorize hardware promotion. The next hardware decision
requires an RTL representation of that waveform and a decoded SPI → I2S phrase;
the current evidence does not justify claiming that milestone complete.
