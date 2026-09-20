# M5A saw alias energy through the ladder

[`signal-path-alias-energy-v1.json`](signal-path-alias-energy-v1.json) adds
absolute predicted-image-band energy and whole-signal energy to the existing
ratio measurements. Stage samples are normalized from signed Q15 to full
scale; the Hann-windowed FFT power is converted with one-sided Parseval
weighting. `alias_db` remains the prior image-band/total ratio, so existing
scorecard semantics do not change.

At MIDI 84, the oscillator and mixer are unchanged by the drive sweep: alias
energy is −53.3205 dBFS and total signal energy is −6.3770 dBFS at both drive
settings. Across the ladder, alias-band energy is −44.2224 dBFS at drive 1.00
and −48.8802 dBFS at drive 0.75, a **4.66 dB reduction**. Total ladder energy
falls by only 1.57 dB, from −9.2704 to −10.8372 dBFS. At the final output, the
absolute alias-band reduction is also 4.66 dB, while total energy falls 1.57
dB.

MIDI 96 shows the same pattern: ladder alias-band energy falls 4.05 dB
(−42.1833 to −46.2344 dBFS), while total ladder energy falls 1.34 dB. At the
output, alias-band energy falls 4.05 dB and total energy falls 1.34 dB. The
unchanged oscillator/mixer input and the stage levels locate the measured
drive-dependent change across the nonlinear ladder operation; a reduced
alias-to-total ratio alone would not show this.

This is a fixed-model diagnosis at cutoff 14,073 Hz, comparing drive 1.00 and
0.75 on the saw only. The dBFS values are absolute relative to a normalized
full-scale sample; they describe this model/reference patch and are not a
physical instrument measurement.
