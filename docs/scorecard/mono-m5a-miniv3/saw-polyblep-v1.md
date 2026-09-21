# M5A saw PolyBLEP control

This controlled 2× model experiment changes one oscillator setting: the normal
PolyBLEP saw correction is enabled or disabled before the same 2× decimator.
Cutoff (14,073 Hz), ladder drive (1.0), frozen Mini V3 reference, event windows,
and all other patch settings are fixed. Full oscillator, mixer, ladder, and
output vectors are recorded in the paired JSON files.

Disabling PolyBLEP is rejected. At the oscillator output, worst common-partial
error grows from 5.083 to 5.572 dB at MIDI 84 and from 8.437 to 9.809 dB at
MIDI 96. Oscillator foldback excess grows from 3.185 to 26.510 dB and from
0.830 to 28.921 dB, respectively. Final output foldback excess also worsens:
15.175 to 22.068 dB at MIDI 84 and 18.186 to 25.328 dB at MIDI 96. Output gain
is effectively unchanged (+2.980/+1.605 dB versus +2.980/+1.601 dB).

This gives a negative but useful control: the PolyBLEP correction is doing
necessary anti-alias work, and removing it slightly worsens harmonic shape too.
The original stage localization remains: oscillator and mixer match each other;
the ladder introduces most of the broad harmonic attenuation. The next saw
experiment should target the ladder response while holding the selected
oscillator fixed. No RTL was changed or verified by this model-only result.

Reproduce both conditions with:

```sh
python tools/measure_m5a_signal_path.py --cutoff 14073 --drive 1.0 \
  --out build/scorecard/saw-polyblep-on-v1.json
python tools/measure_m5a_signal_path.py --cutoff 14073 --drive 1.0 \
  --disable-polyblep --out build/scorecard/saw-polyblep-off-v1.json
python -m pytest tools/test_measure_m5a_signal_path.py -q
```

The reports share source commit `65b8714454ca3ec0de822024c0309ccbcf546b1b`
and the same frozen reference hash. See [PolyBLEP enabled](saw-polyblep-on-v1.json)
and [PolyBLEP disabled](saw-polyblep-off-v1.json).
