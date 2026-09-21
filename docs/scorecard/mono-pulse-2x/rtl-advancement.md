# Pulse 2x: authorization to advance to RTL

The product decision on 2026-09-21 accepts the measured harmonic tradeoff for
advancement to RTL. It does not change the strict comparator or any tolerance.
The strict model comparator still rejects both candidates. No already-passing
per-note property is lost against the selected baseline; an already-failing
partial worsens from approximately 3.39 to 3.74 dB. Legacy-to-selected losses
remain recorded separately.

Both complete phrases now have five of seven passing properties measured from
decoded SPI-to-I2S audio. The official board remains 20 valid cases / six passing
cases; these candidate results remain separate from the selected baseline.

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

## Independent CI qualification remains open

Ubuntu 24.04 Verilator 5.020-1 produces 2,897 smoke-period mismatches,
starting at period 489 in the saw segment. The same sources pass locally
under Verilator 5.052, and directed Icarus verification passes. The cause is
not yet established. The CI disabling-control job also reports a mismatch
from this unrelated baseline failure, so that CI control is **not evidence**
of mutation detection. The local clean/control pair remains recorded above.
`rtl/ci-verilator-5.020-failure.json` preserves all four CI job outcomes.

A separate provenance guard rejected deliberate candidate changes when main
only gained documentation. The repaired guard compares each dependency with
the common ancestor and still refuses missing upstream dependency changes.
The new red test failed before the repair; five focused checks pass afterward.

## Landed baseline verification

The baseline stack landed at main `356c6799bee9e07677f3f2d4df8ac904eaba83ed`;
its tree is exactly `349a382a391a21fa112b889eed87b6b49866220d`, the tested
#190 tree. The local aggregate injected-defect gate passes all 42 controls.
The local fast aggregate timed out on two jobs under host contention; its
scorer retry passed, while the focused suite retry remained NO-VERDICT after
1,800 seconds. Those outcomes are retained in `rtl/baseline-*.json`, not
reported as a local aggregate pass. Standard CI passed on the identical
baseline tree. Full RTL was not rerun for the baseline landing.

The repaired branch provenance gate now permits both reference controls to
execute: clean comparisons are valid, and missing/tampered mutations each
produce the intended refusal (2/2 PASS locally and in CI). The local run took
614 seconds per control under host contention; these are executed controls,
not missing-baseline successes. See `rtl/reference-controls-repaired.json`.

The directed cross-simulator comparison localizes the 5.020 discrepancy to
`fe` at frame 193 and `ae` at frame 194; oscillator and mixer taps agree.
Making `env_update` automatic was a rejected probe: 5.020 then left both
envelopes at zero. This is preserved in `rtl/ci-envelope-probe-5.020.json`;
the production function is unchanged. The independent rate converter passes
all 512 vectors under 5.020. These findings narrow the tool discrepancy but
do not establish its internal cause.

CI now builds pinned Verilator 5.052, upstream commit
`ea338be98e1e838d3518809ce8899f85a009963c`, and runs both simulators' directed
checks, the clean/control pair, drums deadlines and both complete phrases.
This is a toolchain qualification attempt, not yet a claimed Linux pass.

The simulator discrepancy and exact reproducer are filed upstream as
[Verilator #8442](https://github.com/verilator/verilator/issues/8442).
