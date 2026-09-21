# M5A saw cutoff candidate through SPI → I²S

The accepted model-level saw cutoff and fixed gain correction now run through the SPI control path and complete I²S phrase. The pulse remains at the measured `pulse29` baseline. During saw segments, the filter cutoff is 20 kHz and the saw volume correction is −0.45428 dB; controls return to baseline for the pulse segments.

The full Verilator run captured 1,305,533 I²S periods. The decoded stream matched the fixed-point model bit-for-bit, left and right channels agreed, and the complete phrase and release were present. The exact decoded WAV, scored WAV, and simulator transcript are committed beside this report. The decoded WAV SHA-256 is `d64c9a864f592e8387a8228f21c314aefc28c8e07bf8aca8a36d3110d3a4b494`.

The seven metrics were independently recomputed from the decoded I²S audio:

| Property | Measured error | Limit | Result |
| --- | ---: | ---: | --- |
| Pitch | −0.14813 cents | 1 cent | pass |
| Harmonic shape | 7.56137 dB | 1 dB | fail |
| Foldback energy | 10.27423 dB excess | 3 dB | fail |
| Attack | +5.375 ms | 5 ms | fail |
| Release | −7.95833 ms | 125 ms | pass |
| Gain | +1.42139 dB | 3 dB | pass |
| Clipping | 0% | 0.01% | pass |

Against the previous integrated M5A record, the component-vector comparator accepts the change: **22 components improved, zero regressed**. Overall M5A remains a valid failure, with harmonic shape, foldback, and attack outside their limits. The candidate improves saw harmonic shape from 8.84429 dB to 7.56137 dB while preserving the baseline foldback result within the comparison tolerance.

This is integrated RTL sound evidence for the candidate, not a passing M5A case. The official board totals remain unchanged until this draft candidate is promoted.

Reproduce the score from the captured stream with:

```sh
PYTHONPATH=model:tools python tools/score_m5a_i2s.py \
  --wav docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.wav \
  --verification docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.txt \
  --pulse-shape pulse29 --saw-cutoff-hz 20000 \
  --saw-volume-correction-db -0.45428 \
  --out build/scorecard/saw-cutoff-20khz-i2s.json \
  --audio build/scorecard/saw-cutoff-20khz-scored.wav
```

The comparator can be rerun with:

```sh
python tools/compare_m5a_i2s_candidate.py \
  docs/scorecard/results/M5A.json \
  docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.json \
  --out build/scorecard/saw-cutoff-20khz-delta.json
```
