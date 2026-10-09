# #558: toms and congas against the #379 gate, by mechanism

**No sound changes ship here.** The kit, the model constants, the RTL and the image are all unchanged. This directory records a frozen
experiment and its result. Under the pre-registered rule the result is negative: no candidate was selected, so nothing could be confirmed.
Under that rule, all six sounds still FAIL the gate. A post-hoc diagnostic on untouched conditions points at one mechanism for the congas
(section 3). It selected nothing and confirmed nothing.

Engine: the shipped kit on `origin/main` at `cd06654`. Gate: `tools/perceptual_gate.py`, unchanged. Corpus:
`tidalcycles/sounds-tr808-fischer` at `85fbecf`, with sha256 frozen per take in `prereg.json`. The six TUNING 5.0 hashes equal the hashes
`docs/scorecard/results/D03A..D08A.json` already record. `~/dev/refs` does not exist on this loom worker, so the corpus was
cloned to `/tmp`. That is a missing capability on the host. It does not affect the result, because every take is hash-checked when it is used.

**A note on attribution.** The issue body cites `docs/scorecard/gate-379/README.md` section 9 and `main` at `3bdb7e9b`.
Neither is on `origin/main`: `3bdb7e9b` is a commit on #561's branch, and #561 is still OPEN. Every figure used here comes from
section 4 on `origin/main`, and the baseline below reproduces that section exactly.

## Sound summary

| sound | gate figure on `main` (rank.json section 4) | what the failing feature is reading | can it be repaired now? |
|---|---|---|---|
| LC, MC, HC | pitch_shape 16.4 / 19.4 / 8.7x | **A fixed BP numerator reduces the pitch_shape ratio by a median 62 % in the float twin** (10 of 10 untouched conga conditions improve; post-hoc, on untouched conditions, not a pre-registered confirmation). Our all-pole mode has a sin-phase onset and the bridged-T has a cos-phase one, and the gate reads that phase as roughly 37 cents of onset "pitch" (a known answer, independent of our model). The remaining 3-21x is not explained. | **Not now: #591, waiting on #350.** On the fixed-point bank, BP+X4 breaks `decay` on 6 of 10 untouched congas. That is consistent with #351's deadband finding, but it is not isolated here: BP+X4 differs from twin-BP both in fixed-point state and in the x4 exciter scaling. |
| LT, MT, HT | pitch_shape 3.0 / 1.0 / 1.2x | The numerator is not the lever. In the float twin it gives a median -15 % on untouched toms, and on the fixed-point bank LT gets worse (3.3 -> 6.4). The 808's LT reads 74 cents at onset and ours reads 35 (`baseline.json`). The size and time constant of this unit's diode drop are **not tested** here. | No candidate. Diode drop: #592. |
| all six (and BD) | impulse 3.8-5.0x | **Measured:** passing the *same* shipped render through the take's own 44.1 kHz rate brings `impulse` within its bar on 21 of 30 conditions (0 of 30 as it stands), and only HF-poor sounds move (section 4). **Mechanism, stated and not measured:** the 808 takes are 44.1 kHz, the gate resamples them to 48 kHz, and the resampler leaves images in an otherwise empty band above 2 kHz that our native 48 kHz render lacks. Nothing here measured the >2 kHz spectrum before and after that path. | Probably not a sound defect, but #588 decides that, not this PR. #588 still has to run its harmful counterexamples. |
| LT (also MT) | modulation 14.8x (4.8x) | **Pointer only (#593):** the 808's toms carry H2..H4 at -33 to -41 dB re H1, and ours sit below -80 dB. A quadratic term that puts H2 at the 808's level halves LT's figure (14.85 -> 7.54) on the seen target, with no other feature moving (`harmonics.json`). | Not tested as a candidate. |

## 1. Baseline, reproduced (`baseline.json`)

The figures match section 4 exactly: LT 14.8 (modulation), MT 10.5 (centroid), HT 8.0 (centroid), LC 16.4, MC 19.4 and HC 8.7
(pitch_shape), with impulse at 4.8 / 4.3 / 4.9 / 5.0 / 4.5 / 3.8. Every verdict is FAIL. None is REFUSED or NO-VERDICT.

## 2. Pre-registration (`prereg.json`, committed in `f66cbd4` before any candidate or non-target take was run)

- **Development set:** LT, HT and MC at TUNING 2.5 and 7.5.
- **Untouched set:** 18 conditions. These are MT, LC and HC at every tuning except 5.0, plus LT, HT and MC at 0.0 and 10.0.
- **Not development and not confirmation:** TUNING 5.0, which was looked at during diagnosis.
- **Accent:** cannot be held out. The corpus has one take per setting, at an accent that is not documented.
- **Candidates:** BP and BP+X4, #351's frozen set. Nothing new was added and nothing was swept.
- **Preservation rule:** every non-target feature must stay at or below 1.10 x the shipped ratio + 0.10, and the peak must stay within 1 dB.
- **Confirmation rule:** the selected candidate must reach >= 75 % improved, a median reduction of >= 25 %, and zero violations.
- **Prior use of these recordings is listed in the file.** LC50, MC50 and HC50 fitted the conga taus. #351 selected BP on the 50 takes for
  body spectrum. The diode-drop law was fitted on 808 From Mars, not Fischer.

## 3. Result (`run.json`, `judge.json`)

**The frozen judge selected nothing.** Both candidates broke a preservation limit on the development set:

| candidate | development violations |
|---|---|
| BP | flatness on LT25 and LT75; decay on MC25 (2.87 -> 7.86) |
| BP+X4 | decay on LT25, LT75 and MC75 |

With nothing selected, nothing was eligible for confirmation.

Reported anyway, and not confirmable: on all 18 untouched conditions, BP+X4 improves pitch_shape on 14 of 18 with a median
reduction of 46 %. It has 12 preservation violations, mostly in `decay`.

### Mechanism, by position (`judge.json` -> `post_hoc`)

"Float twin" here means the same host writes, exciter and poles as the engine, but run as a float recursion. It is a post-hoc diagnostic: it
selects nothing and confirms nothing. Its RAW form reproduces the engine's pitch_shape to within 0.23 (ratio) on every condition, and `twin.json`'s own shipped rows are identical to `run.json`'s.
`test_twin_raw_tracks_the_engine` guards that within 0.25 on LT00, HC25 and MC00, and needs the corpus. Its defeating input is the
`twin-no-host-writes` injection, a twin that drops the host's frame-by-frame writes, which turns it red (LT00 3.34 -> 6.10).

The twin was started at 06:13, mid-run, after the LT rows had already shown BP failing. Every twin figure below is post-hoc.

| untouched | n | float twin BP: pitch_shape improved, median reduction | twin decay x (median), cells over the limit | BP+X4 fixed point: improved, median | BP+X4 decay over the limit |
|---|---|---|---|---|---|
| congas | 10 | **10 / 10, -62 %** | x1.00, 2 of 10 | 10 / 10, -62 % | 6 of 10 |
| toms | 8 | 6 / 8, -15 % | x0.96, 3 of 8 | 4 / 8, -1 % | 3 of 8 |

On the congas, the fixed-point candidate and the float twin move pitch_shape identically. Only `decay` separates them.

That is consistent with #351's deadband finding (#350), but it is not isolated here. BP+X4 differs from twin-BP in two ways, fixed-point
state and the x4 exciter scaling. The control that would isolate it was not run: the float twin with floor-rounded state should reproduce
the decay break, and the twin without it should not.

On the toms, the numerator is not what the gate is reading.

### Known answers (`knownanswer`, independent of our model; `test_tom_conga_gate.py`)

- **Onset phase.** For a float resonator at each of the six f0/tau pairs, the gate's first pitch frame reads:
  - all-pole: -2 to +5 cents;
  - the same poles with (1 - z^-2) or (1 - z^-1): +37 to +39 cents, relaxing within 5-15 ms.

  The 808 congas read +46 to +53 cents. So an LTI resonator with no pitch change at all reads as a "pitch drop" to this feature.
- **Floor.** The body half of `impulse` rises with a stationary floor at about 1 dB per dB: -23, -9 and +5 dB at floors of -100, -85 and
  -70 dB. The strike half is blind to the floor.

### Every condition

| take | split | pitch_shape shipped | BP+X4 (fixed point) | BP (float twin) | decay shipped | decay BP+X4 | decay twin RAW / BP | impulse shipped | impulse, same render via the take's rate |
|---|---|---|---|---|---|---|---|---|---|
| LT00 | untouched | 3.32 | 6.38 | 0.93 | 10.57 | 12.15 | 10.66 / 16.45 | 4.19 | 1.24 |
| LT25 | development | 3.46 | 7.36 | 0.82 | 10.14 | 11.94 | 10.25 / 16.05 | 3.56 | 0.84 |
| LT50 | seen | 2.96 | 6.42 | 0.94 | 6.84 | 8.63 | 6.91 / 11.15 | 4.80 | 1.45 |
| LT75 | development | 2.18 | 1.28 | 1.24 | 8.44 | 11.13 | 8.52 / 14.23 | 2.79 | 1.57 |
| LT10 | untouched | 2.79 | 1.66 | 1.78 | 3.35 | 4.70 | 3.40 / 6.17 | 9.36 | 4.65 |
| MT00 | untouched | 1.03 | 1.07 | 1.00 | 7.05 | 4.60 | 7.05 / 5.08 | 5.54 | 0.86 |
| MT25 | untouched | 0.90 | 0.91 | 0.87 | 6.70 | 4.33 | 6.71 / 4.37 | 4.65 | 0.86 |
| MT50 | seen | 0.97 | 1.08 | 1.09 | 2.08 | 2.26 | 2.11 / 2.52 | 4.32 | 0.79 |
| MT75 | untouched | 1.06 | 1.01 | 0.99 | 0.44 | 0.37 | 0.44 / 0.36 | 4.91 | 0.90 |
| MT10 | untouched | 1.15 | 0.72 | 0.73 | 0.91 | 0.56 | 0.96 / 0.46 | 1.96 | 1.17 |
| HT00 | untouched | 0.76 | 0.79 | 0.74 | 8.96 | 9.68 | 8.97 / 9.79 | 5.36 | 0.56 |
| HT25 | development | 0.53 | 0.54 | 0.52 | 8.76 | 8.87 | 8.77 / 8.95 | 5.25 | 0.74 |
| HT50 | seen | 1.16 | 0.87 | 0.86 | 7.24 | 5.85 | 7.25 / 5.91 | 4.94 | 0.83 |
| HT75 | development | 0.67 | 0.84 | 0.88 | 5.27 | 3.59 | 5.80 / 3.64 | 9.56 | 5.49 |
| HT10 | untouched | 0.90 | 0.69 | 0.67 | 2.97 | 3.75 | 2.97 / 3.80 | 8.65 | 4.72 |
| LC00 | untouched | 33.13 | 9.62 | 9.66 | 2.69 | 2.34 | 2.81 / 1.19 | 4.69 | 0.54 |
| LC25 | untouched | 33.78 | 10.46 | 10.53 | 2.75 | 2.97 | 3.02 / 0.92 | 4.37 | 0.56 |
| LC50 | seen | 16.45 | 6.06 | 6.04 | 1.22 | 1.97 | 1.30 / 0.69 | 4.97 | 0.52 |
| LC75 | untouched | 8.98 | 4.01 | 4.05 | 2.17 | 2.76 | 2.29 / 0.96 | 1.80 | 0.98 |
| LC10 | untouched | 7.38 | 3.96 | 4.02 | 2.07 | 1.83 | 2.24 / 2.17 | 9.34 | 5.13 |
| MC00 | untouched | 60.18 | 19.67 | 20.30 | 3.69 | 4.17 | 3.55 / 3.65 | 4.26 | 0.60 |
| MC25 | development | 61.95 | 21.71 | 21.83 | 2.87 | 2.25 | 2.73 / 2.76 | 2.79 | 0.34 |
| MC50 | seen | 19.42 | 7.50 | 7.66 | 0.25 | 0.50 | 0.38 / 0.40 | 4.49 | 0.72 |
| MC75 | development | 10.45 | 4.80 | 4.92 | 0.87 | 1.15 | 0.82 / 0.85 | 2.79 | 0.41 |
| MC10 | untouched | 6.72 | 3.70 | 3.77 | 2.53 | 2.99 | 2.51 / 2.61 | 2.95 | 0.53 |
| HC00 | untouched | 39.14 | 11.38 | 11.51 | 1.01 | 1.29 | 1.01 / 1.30 | 4.25 | 0.48 |
| HC25 | untouched | 39.35 | 11.59 | 12.19 | 0.79 | 1.28 | 0.79 / 1.30 | 3.79 | 0.48 |
| HC50 | seen | 8.73 | 2.96 | 3.03 | 0.34 | 0.70 | 0.33 / 0.69 | 3.75 | 0.42 |
| HC75 | untouched | 7.24 | 3.09 | 3.09 | 0.60 | 0.77 | 0.65 / 0.79 | 1.29 | 0.15 |
| HC10 | untouched | 8.29 | 4.22 | 4.19 | 0.84 | 0.68 | 0.80 / 0.57 | 2.24 | 1.21 |

## 4. Class search (`ratepath16.json`)

The rate-path test was run on all sixteen gate sounds at their gate targets: the shipped render at native 48 kHz, against the same render
through the take's 44.1 kHz.

- **Seven sounds move: BD and the six toms/congas.** These are the sounds whose body has little HF content of its own (stated, not measured
  here). BD goes 4.85 -> 0.98, LT 4.80 -> 1.45,
  LC 4.97 -> 0.52, MT 4.32 -> 0.79, MC 4.49 -> 0.72, HT 4.94 -> 0.83 and HC 3.75 -> 0.42.
- **The other nine move by 0.01 or less.** RS's 1.09 is its own.
- **This is a negative control on the class, not a measurement of the mechanism.** Two tests were not run: the 808 take's >2 kHz body
  spectrum before and after the gate's 44.1 -> 48 kHz path, and our render round-tripped 48 -> 44.1 -> 48 kHz. Whether `impulse` should
  change is #588's call.

The pitch_shape onset-phase finding is a second class:

- **Within this family:** it applies to every all-pole RAW body against a band-pass circuit. Here that means the congas, and LT in part.
- **Beyond this family:** BD (#557) is also a RAW mode. Its figure includes a genuine, measured 58 -> 50 Hz glide, so the phase term is
  at most a part of it. That is not tested here.

## 5. What was not run, and why

- **Heavy checks:** `make verify`, `make controls` and `make verify-full` were not run. Neither were RTL, I2S equivalence or deadline
  checks. Nothing in the instrument changed, so there is no model-to-RTL path to carry. Those runs belong on the build box, not on this
  shared worker.
- **Gate tests:** `tools/test_perceptual_gate.py` was not re-run, because the gate is unchanged.
- **What did run here:** `tools/probes/test_tom_conga_gate.py`, 32 tests with the corpus. That includes 5 injections, each of which turns
  a known answer red, and the provenance tests (section 7).

## 6. Wrong-then-right: 2

1. **H3 was pre-registered as "the body impulse reads the recording's floor".** Its own dose control refuted it:
   - Transplanting the take's floor at its own level put `impulse` within bar on 20 of 30 conditions, and at -20 dB on 21 of 30. A floor
     cannot do that.
   - The transplant resampled our render to 44.1 kHz.
   - The rate path alone does the same (21 of 30). Section 4 shows the rate path does it on every HF-poor sound.
2. **`test_judge_confirmation_reads_untouched_only` was blind to a judge that pools the splits.** The 3 + 4 row fixture pooled to 3 of 7.
   The `confirm-reads-all` injection caught it, and the fixture is now 6 + 2.

## 7. Provenance and reproduce

- **`run.json`** was produced by the probe at `f66cbd4`. Its `provenance.commit` reads `87d54bf`, which was HEAD when the run *finished*.
- **Reproduction check:** four conditions (LT25, LT00, MC25, MC00) re-run at clean `febe3bf` are identical to `run.json`, field for field.
- **`twin.json`** ran from an uncommitted tree, before `87d54bf` existed: `twin.log` was created at 06:13:14, and `87d54bf` is from
  06:15:41. Its `model_tools_dirty: false` is therefore wrong.
- **The probe is fixed, and the records are left as they are.** Provenance is now read at the *start* of every measuring command. A dirty
  `model/`, `tools/` or `prereg.json` is REFUSED (exit 2) before anything is measured or written. The end-of-run HEAD and dirty flag are
  recorded beside the start values (`commit_at_end`, `head_moved_during_run`, `dirty_at_end`) instead of replacing them. Two tests guard
  this: `test_dirty_tree_is_refused_before_any_measurement` and `test_provenance_names_the_commit_the_run_started_from`. Both are red
  against the previous probe.

```sh
P=.venv/bin/python; R=<sounds-tr808-fischer @ 85fbecf>
$P tools/probes/tom_conga_gate.py diagnose    --refs $R --out docs/scorecard/tom-conga-558/baseline.json
$P tools/probes/tom_conga_gate.py knownanswer --out build/558/knownanswer.json
$P tools/probes/tom_conga_gate.py run         --refs $R --out docs/scorecard/tom-conga-558/run.json     # ~13 min, one core (run.log)
$P tools/probes/tom_conga_gate.py twin        --refs $R --out docs/scorecard/tom-conga-558/twin.json
$P tools/probes/tom_conga_gate.py judge docs/scorecard/tom-conga-558/run.json --twin docs/scorecard/tom-conga-558/twin.json --out docs/scorecard/tom-conga-558/judge.json
$P tools/probes/tom_conga_gate.py harmonics   --refs $R --out docs/scorecard/tom-conga-558/harmonics.json
$P tools/probes/tom_conga_gate.py ratepath16  --refs $R --out docs/scorecard/tom-conga-558/ratepath16.json
GF180_TR808_FISCHER=$R $P -m pytest -q tools/probes/test_tom_conga_gate.py
```

## 8. Next steps (not done here)

- **#591 (waiting on #350):** fix the modal deadband, then re-run `run` unchanged. The conga numerator is the candidate waiting on it.
- **Gate rubric (#588):** put candidates through the target's rate path, or band-limit the impulse body term. Either needs its own
  experiment with harmful counterexamples, such as a real body click that must still fail. It should also measure the mechanism
  (section 4). Also say in the gate's doc that pitch_shape
  reads onset phase.
- **Toms:**
  - the diode-drop magnitude and time constant for this unit (LT onset 74 against 35 cents): **#592**;
  - H2..H4 output distortion, for modulation: **#593**.
