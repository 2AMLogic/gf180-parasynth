# Fixed-duty pulse 2× experiment

This is a model-only experiment at source commit `916243e`. The selected
RTL still oversamples saw only; pulse oversampling remains disabled by default.
Both complete frozen reference phrases are measured, including saw and pulse,
their envelope calibration, and release tails. The pulse duty stays 47.90%.
The saw cutoff, filter configuration, gain controls, and note timelines stay
fixed. All eight full-phrase measurements completed without a corrected sound
measurement (wrong-then-right: 0/8). Before implementation, independent
rectangular-wave DC/Fourier checks rejected the saw-only renderer.

| Complete phrase | Baseline foldback excess | Gain-only control | Pulse 2× | Limit |
| --- | ---: | ---: | ---: | ---: |
| M5A (MIDI 84/96) | 10.27447 dB | 9.41280 dB | 2.20593 dB | 3 dB |
| M5B (MIDI 72/84) | 8.84954 dB | 8.01867 dB | 1.91296 dB | 3 dB |

Pulse foldback excess is zero at every measured note (cleaner than the
reference under the one-sided rule). Saw now sets the phrase maxima shown
above. The decimator's existing 27853/32768 headroom gain reduces pulse level;
the gain-only control applies that exact gain before mixing without changing
oscillator rate. Its much smaller improvement shows that reduced drive alone
does not explain the candidate's foldback result. The JSON retains absolute
alias-band power and total power at oscillator, mixer, ladder, and output.

Both cases reach **5/7 model properties passing**: pitch, foldback, release,
gain, and clipping. Saw still sets the worst harmonic errors (7.56136 dB in
M5A and 5.96219 dB in M5B), and attack still fails. This modeled baseline is
rerendered alongside the candidate; it is not a fresh integrated RTL result.

There is a tradeoff: individual pulse-partial absolute errors increase by up
to **0.347 dB**. No previously passing per-note property loses its pass, and
saw measurements are unchanged. The stricter incremental comparator nevertheless
rejects promotion because it forbids regressions in already-failing partials
as well. Both its rejection and the full signed partial vector remain visible.
No gate was relaxed to accept this experiment.

The disabling control produces baseline audio byte-for-byte and cannot be
promoted. The independent DC/Fourier test covers duty and both pulse edges;
odd increments and chunked rendering preserve exact history. Ten oscillator
checks and three comparator/control checks passed.

Reports: [M5A](M5A.json), [M5B](M5B.json).
Candidate audio: [M5A](M5A-pulse2x.wav), [M5B](M5B-pulse2x.wav).
The official scorecard is unchanged: 20 valid cases, six passing cases.

Reproduce all four modes and their audio per case:

```sh
python3 tools/run_all.py --timeout 900 --json build/mono-pulse-2x/jobs.json \
  "python3 tools/measure_mono_pulse_2x.py --case M5A" \
  "python3 tools/measure_mono_pulse_2x.py --case M5B"
```
