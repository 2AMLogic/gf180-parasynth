# Selected-path saw cutoff sweep

This five-point model sweep uses the exact filter configuration selected for
M5A: 2× saw oscillator, causal reconstructed 2× ladder, preserved headroom,
cutoff-dependent coefficient ROMs, and ladder drive 0.75. It changes only the
saw segment's cutoff. The frozen Mini V3 reference and event windows are shared
across points. The report includes oscillator, mixer, ladder, and output
harmonics, foldback excess, absolute alias-band power, and gain for MIDI 84 and
96.

Raising cutoff improves the worst-partial error monotonically, from
7.771/8.846 dB at 14,073 Hz to 5.962/7.561 dB at 20,000 Hz and
5.611/7.383 dB at 21,600 Hz (MIDI 84/96). It also raises alias-band energy.
At 20,000 Hz, foldback excess is 1.918/2.203 dB and gain error is
+1.876/+1.053 dB, still within the individual limits of 3 dB for foldback and
gain. At 21,600 Hz, foldback reaches 3.131/3.316 dB and fails the limit.

The 20 kHz point is a promising model-only saw challenger: it improves shape
without crossing the saw foldback or gain limits. This stage probe does not
score attack, release, pitch, or pulse. A separate complete-phrase property
vector is required before selecting it for implementation, and it does not
establish integrated RTL behavior.

Reproduce with:

```sh
python tools/measure_m5a_signal_path.py \
  --cutoff 14073 16000 18000 20000 21600 --drive 0.75 \
  --out build/scorecard/saw-selected-filter-cutoff-sweep-v1.json
```

The machine-readable report binds to source commit
`c75ca74db61e364caed4d0a0e62858fe715ffa16` and records the selected filter
configuration explicitly.
