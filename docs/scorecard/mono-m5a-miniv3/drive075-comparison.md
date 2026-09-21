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
excess alias and gain error. It does not cover pulse. The first complete
drive-0.75 run retained a 50% square and recorded the pulse mismatch; its
per-event values are preserved in the M5A result history. A separate duty
sweep then showed that supported `pulse29` reduces worst pulse harmonic error
to 19.3092 dB at MIDI 84 and 17.9919 dB at MIDI 96. The selected candidate now
combines drive 0.75 and pulse29. Its complete model phrase keeps saw and pulse
diagnostics separate: worst harmonic-shape error is 19.3092 dB and worst
excess alias is 20.0613 dB. The case remains a valid **FAIL**.

The drive-1.00 full-phrase record was made with `m5a-score-v2`, the square
drive-0.75 intermediate used `m5a-score-v3`, and the selected candidate is
also scored with `m5a-score-v3`. We do not present their aggregate
harmonic-shape values as one apples-to-apples delta: the first transition
changes analysis version, and the second changes pulse shape. The per-note
drive deltas above come from the isolated saw stage sweep; the pulse-shape
deltas come from the isolated duty sweep.

The selected 2× saw / pulse29 / drive-0.75 candidate has a short production-path
SPI-to-I2S smoke record bound into the M5A provenance. It decodes 4,837 I2S
periods with exact model agreement, no dropped writes, busy/overflow, or
overrun. This proves the selected shape reaches the production transport for
the smoke stimulus; it is not bit-exact execution of the complete 27.2-second
phrase through I2S. Keep #180 draft until the full instrument acceptance path
is measured.
