# M5A saw cutoff candidate through SPI → I²S

The accepted model-level saw cutoff and fixed gain correction run through the SPI control path and the complete I²S phrase. The pulse control label remains `pulse29`; under `VOICE_FILTER_2X`, its RTL `DUTY_WIDE` encoding is 8,036,286 / 2²⁴ = **47.90% duty**, equivalent to the model's `pulse479` waveform. The candidate keeps that mapping unchanged. The earlier baseline JSON used `pulse479` as an effective-waveform label; the new evidence records both the register label and the effective waveform.

During saw segments the filter cutoff is 20 kHz and the saw volume correction is −0.45428 dB. Pulse segments restore baseline saw settings. The full Verilator run captured 1,305,533 I²S periods. The decoded stream matched the fixed-point model bit-for-bit, left and right channels agreed, and the complete phrase and release were present. The exact decoded and scored WAVs and simulator transcript are committed beside this report. The decoded WAV SHA-256 is `d64c9a864f592e8387a8228f21c314aefc28c8e07bf8aca8a36d3110d3a4b494`.

The seven metrics were recomputed from the decoded I²S audio:

| Property | Measured error | Limit | Result |
| --- | ---: | ---: | --- |
| Pitch | −0.14813 cents | 1 cent | pass |
| Harmonic shape | 7.56137 dB | 1 dB | fail |
| Foldback energy | 10.27423 dB excess | 3 dB | fail |
| Attack | +5.375 ms | 5 ms | fail |
| Release | −7.95833 ms | 125 ms | pass |
| Gain | +1.42139 dB | 3 dB | pass |
| Clipping | 0% | 0.01% | pass |

Against the previous integrated M5A record, the incremental vector comparison finds 22 improvements (the aggregate harmonic score and 21 saw partials). Per-note checks preserve **17 existing passes**, lose none, and separately report **five degradations that remain inside their per-note limits**. Saw alias excess increases from 0 to 1.9171 dB at MIDI 84 and from 0 to 2.2082 dB at MIDI 96; both stay below the 3 dB limit. This margin use is visible rather than hidden by the pulse's larger failure.

M5A remains a valid failure because harmonic shape, foldback, and attack exceed their limits. The official board stays unchanged at 19 valid cases and six cases passing all properties. This is integrated RTL evidence for an incremental sound improvement, not a passing M5A case.

The scorer requires the recorded `VOICE_FILTER_2X` compile define and validates any printed control/effective-waveform mapping against the RTL duty encoding. Reproduce the score from the captured stream with:

```sh
PYTHONPATH=model:tools python tools/score_m5a_i2s.py \
  --wav docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.wav \
  --verification docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.txt \
  --pulse-shape pulse29 --saw-cutoff-hz 20000 \
  --saw-volume-correction-db -0.45428 \
  --out build/scorecard/saw-cutoff-20khz-i2s.json \
  --audio build/scorecard/saw-cutoff-20khz-scored.wav
```

Compare against the frozen integrated baseline with:

```sh
python tools/compare_m5a_i2s_candidate.py \
  docs/scorecard/results/M5A.json \
  docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.json \
  --out build/scorecard/saw-cutoff-20khz-delta.json
```
