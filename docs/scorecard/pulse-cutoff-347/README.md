# #347: M5 pulse brightness, bounded pulse-cutoff sweep. Negative result

The instrument is `tools/measure_m5_pulse_cutoff.py`. The candidates, the pulse-specific objective, the admissibility
rule, the tie-break and the confirmation were all fixed in its docstring before any render. One mechanism changes:
the cutoff on pulse segments.

The engine is the next image's: pulse2x, with rectangles at 0.74 inside the 2x chain. The saw stays at its preset
drive of 0.75. The saw-drive candidate is a separate experiment and is not mixed in, and an invariance control
checks that every saw event is identical. The record is `sweep.json`, commit `f5545ba`, sources clean.

| pulse cutoff | M5A pulse objective (worst partial, dB) | M5B pulse objective | M5A / M5B foldback (limit 3) | M5A gain | verdict |
|---|---:|---:|---|---:|---|
| 14,073 (baseline) | 5.297 | 4.957 | 2.206 / 1.913 | −1.544 | — |
| **17,000** | **3.947** | **4.369** | 2.206 / 1.913 | +1.421 (sign flip: the worst event moves from pulse 96 to saw 84) | admissible on M5A; **fails confirmation** |
| 20,000 | 3.771 | 3.977 | **3.733** / 2.108 | +1.421 | **rejected**: M5A pulse-96 foldback 3.73 > 3 |

**Selection.** 17 kHz is the only admissible candidate on M5A.
- It brightens the pulse. The worst partial error falls from 5.30 to 3.95 dB, and at the untouched M5B MIDI 72 from 4.96 to 4.37 dB.
- The |Gain| change is 0.12 dB.
- The saw events are unchanged.

**Confirmation: FAILS.** Held pulse notes 60 and 108 through the artifact probe show relative unwanted energy
**+3.65 dB** and **+3.64 dB** worse. That exceeds the 1.00 dB bound. The higher cutoff lets through the rectangle's
upper images that the 14 kHz cutoff was attenuating. By the plan's rule a candidate that trades brightness for alias
energy is rejected. **Nothing is promoted.**

**What it localizes.**
- At 14,073 Hz, the pulse's remaining brightness deficit and its alias margin are the same filter slope seen from two sides.
- A raised cutoff can only buy brightness with alias energy.
- The next pulse-brightness lever would have to lower the images themselves, for example at the 2x decimator's transition band, before a cutoff change can pay. That is a separate mechanism, and it is not queued ahead of the drum work.
