# F1 on the selected filter under calibration `surge-type2-clean-v1` (plan074 B–E)

**What changed.** A versioned, explicitly selected filter operating point in
the production host conversion, and the official F1 runner measuring the
selected filter path under it. **What did not change:** the default
conversion, every existing patch and register image, `LADDER_CFG`, the g/k
ROMs, `CUT_TRIM`, the tanh table, state widths, the rate chain, the RTL and
the FPGA build. F1's frozen reference clips and tolerances are unchanged.

## B. The calibration

`model/voice_fx.py`: `FILTER_CALIBRATIONS["surge-type2-clean-v1"]` =
input scale s = 1/4 (frozen, #231's selection), applied by
`ladder_regs(res, drive, calibration)`. This is the single gain/ogain
conversion, used by `VoiceFx.patch_regs(..., filter_calibration=...)` and by
the SPI host's resonance knob (`fpga/spi_host.py`). The words are recomputed
from the physical mapping (vpu = 0.13·s) via `LadderFx.regs_unclamped`. They
are not scaled from the rounded baseline words.

| res, drive | legacy gain / ogain | calibrated gain / ogain |
|---|---|---|
| 0, 1.0 | 170394 / 25206 | **42598 / 100825** (= #231's recorded words) |
| 0.5, 1.0 | 170394 / 50412 | 42598 / 201649 |
| 1.0, 1.0 | 170394 / 75618 | 42598 / 302474 |

The calibration refuses where the legacy conversion clamps. Out-of-range words
are refused, never clamped (e.g. gain above 20 bits at drive ≳ 24.6, k at res
2.0). An unknown name refuses. A misspelt keyword (`calibration=`,
`filter_calib=`) refuses instead of being swallowed by `**_ignored`. An image
that names the calibration but carries other words is refused by
`VoiceFx._apply_patch`. The image records `filter_calibration`. The default
image has no such key, so it stays byte-identical.

**Default register images unchanged** (`tools/check_default_register_images.py`,
separate process per tree, baseline = `4c26c41`, the fork point):
M1A, M5A-saw, M5A-pulse, M5B-saw, M5B-pulse and the default patch are
identical in `patch_regs`, the SPI `load()` voice writes and the SPI
resonance-knob writes (18 of 18). See `default-register-images.{json,log}`,
exit 0. Control: `--inject CALIBRATE_ALL` gives 18 differences, exit 1
(`control-images-calibrate-all.log`). This is therefore a **local, additional
operating point**. Tests: `model/test_filter_calibration.py` (11).

## C. Official F1 records (`docs/scorecard/results/F1{A,B,C}.json`)

Model side: `tools/f1_filter_path.py` (`f1-filter-path-v1`). It uses the
selected engine `selected-m5a-reconstructed-filter2x`, built by
`mono_m5a_score`'s own constructor, and checks its identity (causal,
headroom-preserving 2x `RateConvertedLadder`, 2x ROMs). Registers come from
`VoiceFx.patch_regs(filter_calibration=...)`. The words entering the inner
ladder are asserted to be 42598 / 100825 and different from legacy. A
selected-voice note under the calibration is reproduced bit-exactly first:
12,000 frames, 0 g / k / ladder mismatches. Reference: the frozen profile
clips, unchanged (sha256 `bd2279368babf5b6` / `042d97c684b418f5` /
`e18a16e6f2203fdf`, open `ad30cebd73b77be3`). Estimators:
`run_case@631951be792c + audio_measure@cd27a0a67dfd`. ROMs: g
`48c6c974faf01776`, k `f27ec0e198d0b1f8`, CUT_TRIM 1.03. 48 kHz, −12.04 dBFS.

| case | property | ours | Surge | error | tolerance | within |
|---|---|---:|---:|---:|---:|---|
| F1A | corner Hz | 108.6651 | 117.6344 | −8.9693 (−7.62 %) | 11.7634 | yes |
| F1A | low-band dB | −0.8967 | −0.9073 | +0.0106 | 3.0 | yes |
| F1A | rolloff dB/oct | −17.7933 | −18.6879 | +0.8946 | 1.5 | yes |
| F1B | corner Hz | 431.7350 | 450.5278 | −18.7927 (−4.17 %) | 45.0528 | yes |
| F1B | low-band dB | −0.1489 | −0.2556 | +0.1067 | 3.0 | yes |
| F1B | rolloff dB/oct | −17.8910 | −17.9191 | +0.0281 | 1.5 | yes |
| F1C | corner Hz | 1656.1734 | 1718.5771 | −62.4037 (−3.63 %) | 171.8577 | yes |
| F1C | low-band dB | −0.0424 | −0.1364 | +0.0940 | 3.0 | yes |
| F1C | rolloff dB/oct | −16.2688 | −16.4113 | +0.1425 | 1.5 | yes |

Worst/tolerance: F1A 0.76, F1B 0.42, F1C 0.36. `run_case.py F1A F1B F1C` exit
0 (`run_all.json`). **Agrees with #231's separate experiment** to the
reported precision (corner −7.62/−4.17/−3.63 %, F1A rolloff +0.8946). No
discrepancy was found.

History: the superseded records (legacy base-rate `OurLadder`, global words)
are in `legacy-records/`, and each new record carries the legacy reading as
`diagnostics.legacy_standalone` (F1A corner 97.85 Hz, rolloff −16.87, which
fails as before).

Provenance: the records say `dirty: true, untracked_files: 2`. The two files
are this batch's own `run_all.{json,log}`, which are generated outputs. Every
consumed input hash on the records equals the committed file at `c024716`.
They were not re-rendered to clear the flag.

Controls (by exit status, `run_all.json` and `control-corner2x-run_all.*`):

| control | result |
|---|---|
| `REF_PROFILE_MISSING` F1A–C `--expect 'no verdict'` | fired, 3/3 |
| `REF_PROFILE_TAMPERED` F1A–C `--expect 'no verdict'` | fired, 3/3 |
| `F1_LEGACY_SUBSTITUTE` F1A–C `--expect 'no verdict'` (legacy ladder under the selected label) | fired, 3/3, refused by identity |
| `REF_CORNER_2X` F1A–C `--expect fail`, **after the plan075 repair** (reference axis only; DUT on the frozen grid) | **fired, 3/3, all 9 metrics valid**: worst 8.76 / 9.39 / 9.33, corner. DUT grid sha `449ca4205ccd35f3` identical clean vs injected; reference axis `449ca4205ccd35f3` → `1c688263b94ab167`. `settled-run_all.*` |
| (history) `REF_CORNER_2X` F1A–C before the repair | **NO-VERDICT**. F1A/F1B measured fail (8.72, 9.29). F1C measured the corner failure (error 806 Hz vs 86 Hz tolerance), but *our* rolloff went invalid: the injection halves the tone grid our side is rendered on too (top tone 6 kHz), leaving 3 points in F1C's band. It did not crash, but it is not a measured case failure. Not fitted around. |
| (history) `REF_CORNER_2X` F1A F1B before the repair | fired, 2/2 (worst 8.72, 9.29), exit 0 |

## D. Register transport and component RTL

All jobs ran in one `tools/run_all.py` batch (`run_all.json`, iverilog). The
build is `c024716` plus generated logs. RTL sources and ROMs are unchanged
from main (`synth_top.v e4f8d97b9785`, `tanh16.hex a5c4862a7e90`), so **no
FPGA rebuild is implied**.

- **Component** (`verify_voice.py --set quick --only f1cal --filter2x`, PASS,
  exit 0): voice_dp with the 2x filter, 7,200 frames, 166 writes at the
  register port. The calibrated words are applied at 250 / 1000 / 4000 Hz res 0
  and 1000 Hz res 1.0 / 1.1 (ogain 302474 / 322639). Every sample and every tap
  is identical to `VoiceFx`, final state identical.
  **Negative control** `--f1cal-fault LEGACY_WORDS --expect-fail` (image names
  the calibration, port receives 170394 / 25206): built, simulated all 7,200
  frames, 7,118 samples differ. Caught, exit 0.
- **SPI→I²S** (`verify_synth_top.py --f1cal-smoke surge-type2-clean-v1`, PASS,
  exit 0): 50 writes over the SPI pins and 5,841 I²S periods decoded from the
  wire, all identical to the model driven by the *requested* image (wire and
  model sha256 `9d22ca7bc21dad80`). The words at the register port were
  42598 / 100825 (voice) and 42598 / 100825 (drum filter words), and after the
  resonance knob (res 0.5), 42598 / 201649. The saw passed through 250 / 1000 /
  4000 Hz at res 0.
  **Negative control** `--f1cal-fault LEGACY_WORDS --expect-fail`: 6 of 6
  gain/ogain writes arrived as legacy words, and 5,718 of 5,841 periods differ
  (wire `f868cc79f061eea6`). Caught, exit 0.
- **Apparatus incident (first attempt, logs kept):**
  `rtl-run_all-first-attempt.log`. verify_synth_top crashed on a coverage print
  (fixed). verify_voice was NO-VERDICT ("datapath still busy at the end of
  frame 0") with three 2x *saw* oscillators and the 2x filter. It was
  reproduced with the legacy words (`voice-bench-budget-isolation.log`), so the
  calibration is not the cause. tb_voice pulses `go` at cycle 48, synth_top at
  cycle 8. The scenario uses revsaw. This is a bench-budget limit worth its own
  issue.

**What D is and is not.** It is component RTL equivalence at the F1 cutoffs
and a short whole-chip transport smoke. It is **not** an integrated-RTL F1
measurement. The chip has no audio input, so the stepped tone never traversed
RTL. The F1 records are fixed-point *model* measurements (engine
`fixed-model`), and the board's engine label says so.

## Headroom / resonance (`headroom.{json,log}`, probe exit 0)

Selected voice, saw note 45, 1 kHz, drive 1.0, vol 0.45. Level change is
calibrated minus legacy steady ladder RMS:

| res | legacy ladder peak | calibrated peak | Δ level | rails (ladder/out) |
|---|---:|---:|---:|---|
| 0 | 16057 | 23784 | +2.13 dB | 0/0 both |
| 0.5 | 19742 | 30862 | +0.93 dB | 0/0 both |
| 1.0 | 23164 | 45867 | +4.54 dB | 0/0 both |
| 1.1 | 23332 | 52609 | +6.28 dB | 0/0 both |
| 1.1, input 0.01 | 6451 | 24635 | **+12.05 dB** | 0/0 both |

The pre-declared screen (no new rail samples) passed **on these conditions
only**. Counterexample from D's component run: the calibrated res 1.0
revsaw scenario drove the pre-rail output (`out_v`) past the 16-bit rail on
**1 sample**, where legacy peaks near half. Resonant and self-oscillating
output is 4.5–12 dB hotter under this calibration, because the reciprocal
ogain multiplies a tanh-limited signal. **The operating point is qualified at
res 0 only.** Offering it with resonance needs an output-level decision
(plan074 G), not this rung.

Wrong-then-right, 1: the probe's first run set the small input through
`mix=`, which `mix_weights` normalises back to full scale, so that row
duplicated the full-level row (`headroom-wrong-first-run.log`). It was caught
by reading the table and is now asserted in code.

## E. Board

`tools/scorecard.py --markdown --readme`: **20 valid / 9 pass / 11 fail /
5 no verdict / 75 not run** (was 20 / 6 / 14 / 5 / 75). Only F1A–F1C changed
state. Filters: 3 valid, 3 pass. What was measured: the fixed-point model of
the selected filter path under an explicitly selected calibration, against
frozen Surge Type 2 clips, at res 0, drive 1.0, −12 dBFS. It is a development
result: the scale was selected on the same cases (#231). M1A attack remains
unqualified and nothing about M1A/M5A/M5B changed.

## Settled re-run after merging main (#234) — plan075 §4

Merged `origin/main` (`c3a8797`, #234: `tools/refprofile.py` per-clip
post-render checks; `profile.json` and the frozen audio unchanged). The
runner comment now names the selected path. `REF_CORNER_2X` shifts only the
reference axis (`run_case.dut_probe_grid`), with an invariant test and
recorded grid hashes. One batch at `e356498` (`settled-run_all.*`), **6/6 by
exit status**: F1A–C exit 0 (pass 0.76 / 0.42 / 0.36); missing, tampered and
legacy-substitute controls 3/3 each; shifted-corner 3/3 measured failures;
focused tests 172 passed, 4 skipped. All nine record values are
**identical** to the pre-merge records. The inputs bind to the new tree,
including `tools/refprofile.py` `bde30e5f0a93e3e4` (was `a5c5196a1ecc952b`).
F1A's record is clean. F1B and F1C say `dirty: true` because F1A.json,
written moments earlier by the same batch, was uncommitted. That is a
generated output, not a consumed input.

## Operating domain (plan075 §4, resonance boundary)

`surge-type2-clean-v1` is **qualified at resonance 0 only**. It stays out of
any resonance-enabled factory preset until an output-level policy is
qualified. The low-level host API (`filter_calibration=`) may exercise it
for tests. A musical frontend must not treat the zero-resonance
qualification as permission for the whole resonance range. The ~12 dB
self-oscillation increase is what the fourfold reciprocal output gain does
to an internally sustained oscillation. It is not evidence of a broken
ladder.

## Not done / follow-ups
- tb_voice's cycle budget with 3×2x saw plus the 2x filter (above).
- Global default (plan074 G): not attempted.
