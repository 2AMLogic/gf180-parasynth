# Published pulse 2× integrated evidence

The [Linux audio workflow](https://github.com/2AMLogic/gf180-parasynth/actions/runs/35661452754)
completed successfully at `22c688cb786d128c30110f5881d20fe3dd2700fc`, using the
pinned Verilator 5.052. `ci-import.json` binds the original artifact bytes,
measurement source hashes, simulator commit, and workflow. Original CI JSONs
are retained as `M5A-ci.json` and `M5B-ci.json`; locally rescored records use
committed transcript paths and reproduce all seven metrics and audio hashes.

| Case | I²S periods, all exact | Properties passing | Evidence | Remaining failures |
|---|---:|---:|---|---|
| M5A | 1,305,533 | 5/7 | integrated RTL, SPI → I²S | harmonic shape 7.56137 dB; attack +5.45833 ms |
| M5B | 729,533 | 5/7 | integrated RTL, SPI → I²S | harmonic shape 5.96211 dB; attack +6.29167 ms |

Foldback excess is 2.20822 / 1.91496 dB (3 dB limit). Pitch, release, gain and
clipping also pass. Both channels agree, all slots are 32 BCLK, all 67 writes
land, and no sample deadline is missed. Worst strobe cycles are 177/256 and
175/256. The separate drums-plus-voice run reaches 182/256 with all 16 sounds.
The disabling control remains caught by 2,145 mismatching periods.

These integrated metrics are separate from the earlier model-only candidate:
SPI register delivery and integer audio can change small score values. The
strict comparator's rejection and accepted incremental harmonic tradeoff
remain recorded; no tolerances or production defaults changed. The official
board remains 20 valid / six passing, with candidate evidence advancing here.
This is simulation evidence; physical DAC playback and pulse-enabled FPGA fit
are outstanding.

Reproduce scoring without rendering the software voice (repeat for M5B):

```sh
python3 tools/score_m5a_i2s.py --case M5A --pulse2x --wav docs/scorecard/mono-pulse-2x/rtl/M5A-i2s.wav --verification docs/scorecard/mono-pulse-2x/rtl/M5A.txt --out docs/scorecard/mono-pulse-2x/rtl/M5A.json --audio docs/scorecard/mono-pulse-2x/rtl/M5A-scored.wav
```

Wrong-then-right: this import reproduced 2/2 complete records without a metric
correction. Earlier bench tap selection, stale-base detection and simulator
failures remain documented in `../rtl-advancement.md` and the retained logs.
The obsolete-simulator investigation is closed; the pinned tool is retained.
