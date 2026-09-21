# Pulse 2x: authorization to advance to RTL

The product decision on 2026-09-21 accepts the measured harmonic tradeoff for
advancement to RTL. It does not change the strict comparator or any tolerance.
The strict model comparator still rejects both candidates. No already-passing
per-note property is lost against the selected baseline; an already-failing
partial worsens from approximately 3.39 to 3.74 dB. Legacy-to-selected losses
remain recorded separately.

Both complete model phrases have five of seven passing properties. This is
model evidence until the full phrases pass SPI-to-I2S comparison and are scored
from their decoded audio. The official board remains unchanged.

Start red: c6b509a selected the pulse2x model against the existing saw-only RTL.
The 4,837-period SPI-to-I2S smoke delivered all 61 writes and met the deadline
(worst strobe 175/256), but 2,145 decoded periods differed. The first mismatch
was period 2,692, in the pulse segment. This was an audio comparison failure,
not a compile failure. The scorer's new case/configuration test also failed
before its implementation and passes afterward.

The RTL candidate uses VOICE_OSC_2X plus VOICE_PULSE_2X. VOICE_FILTER_2X selects
the existing reconstructed filter and 47.9% duty. The disabling mutation is
INJECT_BUG_VOICE_PULSE2X_OFF. Default builds retain their previous behavior.

The first directed voice run produced zero output-sample mismatches across
2,880 frames, but failed its internal oscillator taps: the bench still chose
the base-rate tap for every non-saw waveform. The bench now selects the same
explicit 2x waveform predicate as the datapath. This is a diagnostic repair;
the audio-producing RTL is unchanged. Wrong-then-right count at this point:
one trace-selection defect found by the directed comparison.

FPGA synthesis selects this candidate explicitly with `OSC2X=1 FILTER2X=1
PULSE2X=1`; requesting pulse 2x without the oscillator chain refuses. The
separate selected-baseline FPGA build in #191 does not include pulse 2x.
