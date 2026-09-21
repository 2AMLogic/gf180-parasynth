# Complete-phrase saw cutoff candidate

This compares the frozen M5A phrase under the selected causal,
headroom-preserving reconstructed 2× filter path. Both runs use pulse479; the
candidate changes only the cutoff during saw segments from the calibrated
14,073 Hz to 20,000 Hz. Pulse cutoff, duty, envelope, gain, and all other
controls remain at the frozen manifest values. The report contains the complete
seven-property vectors, event diagnostics, and the existing incremental
non-regression comparison over every measured partial.

Harmonic-shape error improves from 8.846 to 7.561 dB, while foldback stays
10.274 dB. Pitch is unchanged. Attack remains 5.313 ms (0.313 ms over its
limit), release error improves from −9.854 to −8.938 ms, and clipping remains
zero. Gain error increases from +1.421 to +1.876 dB, still within the 3 dB
case limit. Both full cases remain valid failures.

The incremental gate records 22 improved components and one regression:
`metric:Gain`. It therefore rejects this as an accepted improvement despite the
case-level gain still passing. Keep this as a measured challenger; do not
promote it to RTL. The next bounded experiment should test a documented
saw-only gain correction at 20 kHz and rerun the complete vector, with the pulse
segment fixed.

Reproduce with:

```sh
python tools/measure_m5a_saw_cutoff.py --cutoff 20000 \
  --out build/scorecard/m5a-saw-cutoff-candidate-v1.json
```

The report binds to source commit
`3839ad804520a139549b27e6a5bdbd3d3c44a410`, the frozen Mini V3 audio and
manifest hashes, and the exact selected filter configuration.
