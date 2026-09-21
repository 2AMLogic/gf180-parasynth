# M5A attack estimator bias

The M5A attack score uses a 5 ms RMS envelope. This control sends a known linear
attack through that exact meter and crossing estimator, using saw and 47.9%
pulse carriers at MIDI 84 and 96, with eight carrier phases per condition. The
frozen reference manifest and audio hash are checked before any result is
reported.

The known signal's true 10–90% attack is 7.333 ms. The estimator reports it
1.229–1.521 ms late across the four pitch/waveform conditions. The frozen Mini
V3 recording has the same 0.050 amp-attack readback for its four events, yet its
measured attack values span 4.125 ms (3.333–7.458 ms).

This establishes that the meter has measurable bias and that the observed
reference spread is not explained by the shared attack readback alone. The
plugin's internal envelope is not available as an independent ground truth, so
this does not estimate the true Mini V3 rise time or justify changing the
acceptance limit. Keep the attack score visible; do not tune the VCA from this
comparison until its estimator is qualified against the plugin signal path.

Reproduce with:

```sh
python tools/measure_m5a_attack_bias.py
python -m pytest tools/test_measure_m5a_attack_bias.py -q
```

The machine-readable measurements and provenance are in
[`attack-bias-v1.json`](attack-bias-v1.json).
