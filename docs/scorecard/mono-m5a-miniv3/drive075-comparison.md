# M5A filter-drive trial: 1.00 to 0.75

This is a bounded, single-parameter trial on the frozen Mini V3 M5A phrase.
The reference identity and audio hash are in the associated signal-path and
scorecard records. The stage sweep changes only ladder filter drive; cutoff
remains at 14,073 Hz. The measured stage data is in
[`signal-path-drive-v1.json`](signal-path-drive-v1.json), and the current
complete phrase result is in [`../results/M5A.json`](../results/M5A.json).

| Saw note | Output harmonic-shape error, max common partial (dB), drive 1.00 → 0.75 | Excess alias (dB), drive 1.00 → 0.75 | Gain error (dB), drive 1.00 → 0.75 |
| --- | ---: | ---: | ---: |
| MIDI 84 | 9.3056 → 7.8229 | 15.1752 → 12.0848 | +2.9795 → +1.4127 |
| MIDI 96 | 9.9245 → 8.9212 | 18.1856 → 15.4779 | +1.6011 → +0.2600 |

The isolated saw sweep supports drive 0.75: both notes improve in shape,
excess alias and gain error. It does not cover pulse. The complete drive-0.75
M5A run preserves separate diagnostics for saw and pulse at MIDI 84 and 96;
its worst harmonic-shape error is 93.1901 dB on pulse MIDI 84, and its worst
excess alias is 21.5061 dB on pulse MIDI 96. The case is a valid **FAIL**.
The pulse is now the dominant shape mismatch and needs a separate duty-cycle
investigation; this trial does not establish that reducing drive caused that
pulse mismatch.

The drive-1.00 full-phrase record was made with `m5a-score-v2`, while the
drive-0.75 record uses `m5a-score-v3`. Their aggregate harmonic-shape values
are not presented as an apples-to-apples delta. The per-note drive deltas
above come from the same isolated stage-sweep procedure, not from comparing
those aggregate case scores.

The selected drive-0.75 candidate also has a short production-path SPI-to-I2S
smoke record bound into the M5A provenance. That proves transport/sample
agreement for the smoke stimulus, not bit-exact execution of the complete
27.2-second phrase through I2S. Keep #180 draft until the full instrument
acceptance path is measured.
