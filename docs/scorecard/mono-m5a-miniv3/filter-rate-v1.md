# M5A ladder-rate experiment, v1

**Status: valid model measurement; 4× is not selected.** The experiment ran the
complete frozen M5A phrase for each of three ladder paths and both pulse
widths. Every render has the seven scorecard properties and four
wave/note-event diagnostics. The 4× path improves the analytic filter-response
match slightly, but it does not improve both harmonic shape and foldback
relative to the equivalent reconstructed 2× path. No RTL candidate is
justified by these results.

Reproduce from the experiment commit with:

```sh
python3 tools/measure_m5a_filter_oversample.py \
  --out docs/scorecard/mono-m5a-miniv3/filter-rate-v1.json
```

The instrument is [measure_m5a_filter_oversample.py](../../../tools/measure_m5a_filter_oversample.py)
and the full machine-readable measurements are
[filter-rate-v1.json](filter-rate-v1.json). The WAVs are regenerated under
`build/scorecard/m5a-filter-rate-audio/`. Source commit: `f7d0b6f` (clean tree
at measurement time). Reference WAV SHA-256:
`a808cd22448ecea1c639f9311578eb72399f69bda373e9036174c2ca208a1f0a`.
The reference is Mini V3 3.12.0.3422, not a physical Model D.

The three comparisons separate the change in rate conversion from the change
in ladder rate:

| Path | Ladder updates | Input/output rate conversion |
| --- | ---: | --- |
| Production baseline | 2 per 48 kHz frame | sample-and-hold input, last substep retained |
| Reconstructed 2× | 2 per 48 kHz frame | 41-tap Kaiser reconstruction and anti-alias decimation |
| Reconstructed 4× | 4 per 48 kHz frame | 81-tap Kaiser reconstruction and anti-alias decimation |

The 2× and 4× chains build `g` and resonance-compensation `k` ROMs at their
actual internal rates. Both use the same zero-phase SciPy Kaiser resampler
(beta 8.6); the ladder arithmetic itself remains fixed point. This is an
offline model probe. Its resampler is non-causal and is not an RTL design.
The 47.9% pulse is explicitly model-only and has no RTL waveform code.

For the sound comparison, values below are the worst seven-property errors
over the complete phrase. Lower is better for these error magnitudes.

| Pulse model | Property | Production 2× | Reconstructed 2× | Reconstructed 4× |
| --- | --- | ---: | ---: | ---: |
| pulse29 | Harmonic shape (dB) | 19.309 | 19.298 | 21.536 |
| pulse29 | Foldback excess (dB) | 20.061 | 10.673 | 11.230 |
| pulse29 | Gain error (dB) | 1.413 | 1.420 | 1.651 |
| pulse29 | Pitch error (cents) | 0.148 | 0.148 | 0.148 |
| pulse29 | Attack error (ms) | 5.396 | 5.396 | 5.396 |
| pulse29 | Release error (ms) | 204.25 | 204.25 | 204.25 |
| pulse29 | Clipping (%) | 0 | 0 | 0 |
| pulse479 | Harmonic shape (dB) | 8.921 | 8.846 | 8.808 |
| pulse479 | Foldback excess (dB) | 20.567 | 10.969 | 11.572 |
| pulse479 | Gain error (dB) | 1.413 | 1.420 | 1.563 |
| pulse479 | Pitch error (cents) | 0.148 | 0.148 | 0.148 |
| pulse479 | Attack error (ms) | 5.292 | 5.292 | 5.250 |
| pulse479 | Release error (ms) | 204.25 | 204.25 | 204.25 |
| pulse479 | Clipping (%) | 0 | 0 | 0 |

The sizeable alias reduction comes from adding reconstruction and a real output
decimator: reconstructed 2× improves the phrase's foldback-excess error by
about 9.4–9.6 dB over production. Increasing that correctly rate-converted
chain from 2× to 4× then **worsens foldback by 0.56–0.60 dB**. At pulse29 it
also worsens harmonic shape by 2.24 dB. The 0.038 dB pulse479 harmonic
improvement is too small to offset the alias regression. Absolute stage data
show the same distinction: for the saw at MIDI 84 the output alias-band power
falls from −55.82 dBFS in production to −73.37 dBFS at reconstructed 2×, while
4× is −72.67 dBFS. Total power is reported alongside those values in the JSON.

The small-signal response check fixes cutoff at 8 kHz, resonance at zero and
drive at 0.5. This is a four-pole ladder: its intended response at the
commanded one-pole cutoff is around −12 dB, not a conventional −3 dB corner.
At 8 kHz, measured responses are −13.57 dB (production), −13.30 dB
(reconstructed 2×), and −13.50 dB (reconstructed 4×). Against the analytic
four-pole response, the reconstructed paths differ by −0.56 dB and −0.46 dB
respectively. The direct sine projection uses an integer-cycle half-second
window. Across the measured 150 Hz–16 kHz sweep, maximum error from that
analytic response is 0.87 dB at 2× and 0.97 dB at 4×.

Gain remains within its 3 dB limit, pitch within 1 cent, and clipping is zero.
The existing envelope errors remain outside their limits at both rates
(5.25–5.40 ms attack against 5 ms; 204.25 ms release against 125 ms); they are
unchanged from production and are not caused by 4×. Both complete M5A vectors
remain valid failures, with harmonic shape and foldback also outside limits.

The judge's ambiguous pure-440-Hz stage control refused because predicted
foldback bins collide with real harmonics; it did not report a number. The
pre-implementation rate-conversion harness also failed during collection as
expected. After implementation the current fast gate passed 5/5 jobs, including
138 focused tests and three estimator ground truths. The complete phrase
comparison itself did not run through RTL or SPI→I2S; existing integration
smoke coverage is not evidence for these new offline paths.

**Next technical direction:** do not build a 4× ladder yet. If pursuing this
family, the promising intervention to prototype is the reconstructed 2× path;
measure a causal, RTL-feasible input/output filter against this offline
reference before considering 4× again. Keep the pulse29 and 47.9% results
separate, and retain all seven properties when judging that prototype.
