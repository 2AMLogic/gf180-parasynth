# M5A saw cutoff with fixed gain correction

This complete-phrase model comparison uses the selected causal,
headroom-preserving reconstructed 2× filter and the frozen pulse479 candidate.
The saw cutoff moves from 14,073 Hz to 20,000 Hz. A fixed −0.45428 dB volume
correction is applied only to the saw segments. It is derived from the measured
change in gain error: +1.87551 dB at 20 kHz without correction versus the
+1.42123 dB baseline. This is a case-specific calibration candidate, not a
new global gain default. Pulse settings and audio are unchanged.

The existing vector gate accepts this as an incremental improvement: **22
components improved and none regressed**. Harmonic-shape error falls from
8.84582 to 7.56136 dB, release error improves from −9.85417 to −8.93750 ms,
and gain returns to 1.42087 dB from the 1.42123 dB baseline. Pitch, foldback,
attack, and clipping are unchanged within the gate's deadbands.

The full M5A case still fails overall. Harmonic shape remains above its 1 dB
limit, foldback remains at 10.27447 dB above the 3 dB limit, and attack remains
5.31250 ms (0.31250 ms over its limit). This is an accepted model-level
property improvement only; there is no RTL or SPI→I²S evidence for the new
cutoff/volume setting yet.

Reproduce with:

```sh
python tools/measure_m5a_saw_cutoff.py --cutoff 20000 \
  --gain-correction-db -0.45428 \
  --out build/scorecard/m5a-saw-cutoff-gain-corrected-v1.json
```

The report binds to source commit
`3acd8f0b315186b4c7dec6ddcf396aa24d3cc016`, the frozen Mini V3 audio and
manifest hashes, both complete property vectors, and the partial-level
non-regression comparison.
