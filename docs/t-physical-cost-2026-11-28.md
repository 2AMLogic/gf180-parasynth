# Where T-PHYSICAL's 372 seconds went, and what is left of them (#1944)

**Result.** Of the test's 396 s on a hosted `ubuntu-24.04` runner, 387 s was
its `synthetic-defects` control, and 357 s of that was one function evaluating
one kernel: `interp`, the Kaiser-windowed sinc, computed for all 64 taps of
every position it interpolated. The kernel depends on the fractional part of a
position alone, so it is now tabulated once and read off. The same test on the
same runner type then took 86 s under pytest, with every defect still caught
and every defect's record identical to the one before. The receipt check costs
5 ms. The cost was not irreducible, and none of it was the clean pass being
recomputed per defect: each defect is its own synthesis and its own analysis,
and they all cost the same.

This is measurement tooling and CI maintenance. No sound, RTL or image change
is claimed.

## The facts, none of them inferred

| | |
|---|---|
| the test | `tools/test_r0_capture.py::test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught` |
| before, run | [46156529803](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46156529803), `push`, head `83c917d2`: `origin/main` `b2762026` plus `tools/r0_controls_profile.py` and one workflow file |
| after, run | [46158104276](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46158104276), `push`, head `d905ad24`: `83c917d2` plus the kernel table (`ee86dbda`) and the same workflow with a third job |
| what did not change between them | `tools/test_r0_capture.py`'s T-PHYSICAL test, `tools/trial.py`, `docs/trials.json`; `tools/r0_capture.py` differs by `interp` and its two table helpers only |
| `tools/r0_capture.py` before | the same bytes as `ed00ea2c`, the commit #1816's 372.39 s was measured at |
| runner | `GitHub Actions` label `ubuntu-24.04`, AMD EPYC 7763, 4 vCPU, on both runs |
| stack | Python 3.12.16, pytest 9.2.0, numpy 2.5.5, scipy 1.18.1 (`pip install --upgrade`, unpinned, the `tools` job's own line) |
| the test alone, before | 400.56 s under pytest, on its own runner in the same run as the instrument's 396.21 s <!-- claim: grep="400[.]56s call" in=docs/t-physical-cost-2026-11-28/before/pytest-the-test-alone.txt --> |
| the test alone, after | 86.32 s under pytest, the same steps on the same runner type, in the same run as the instrument's 91.99 s <!-- claim: grep="86[.]32s call" in=docs/t-physical-cost-2026-11-28/after/pytest-the-test-alone.txt --> |
| both files, after | `tools/test_r0_capture.py` and `tools/test_r1_capture.py`, as `trial-physical` runs them: 124 passed in 128.85 s <!-- claim: grep="124 passed in 128[.]85s" in=docs/t-physical-cost-2026-11-28/after/pytest-both-files.txt --> |

Every number from here on was produced by `tools/r0_controls_profile.py`
(its controls are in `tools/test_r0_controls_profile.py`) from the record in
`docs/t-physical-cost-2026-11-28/`: `before/` and `after/` hold each run's
`report.json` and printed `summary.txt`, the pytest steps' own lines, the
workflow as run, and the two runs' job ids. The instrument reproduces the test
step for step and starts the trial's children through a launcher that times
every function; it REFUSES unless the run it timed is the test's -- NO VERDICT,
both controls caught, the receipt intact, the sessions accounting for the
child's own seconds. Both runs answered `OK`. The instrument's total against
pytest's own figure for the un-instrumented test is the check on the
instrument: 396.21 against 400.56 before, 91.99 against 86.32 after.

## The breakdown

The test, phase by phase. The children's seconds are the trial's own, from
its receipt; "start and imports" is the runner's figure for the child less
what it spent in its `main`.

| phase | before, s | share | after, s |
|---|---:|---:|---:|
| setup: the test's own `synth_session` | 7.79 | 2.0 % | 1.38 |
| trial: `run_trial`, wall | 388.41 | 98.0 % | 90.60 |
| &nbsp;&nbsp;child `r0-capture` (refused as synthetic; all of it start and imports) | 1.7 | 0.4 % | 1.3 |
| &nbsp;&nbsp;child `synthetic-defects` (13 sessions) | 387.4 | 97.8 % | 89.5 |
| &nbsp;&nbsp;child `cross-image-refusal` | 3.1 | 0.8 % | 1.9 |
| receipt: `check_receipt` | 0.005 | 0.0 % | 0.004 |
| total | 396.21 | 100 % | 91.99 |

The three children start together and the first and third finish within
seconds, so the trial's wall is the `synthetic-defects` child's.
<!-- claim: grep="total +396[.]21 s" in=docs/t-physical-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="total +91[.]99 s" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

The control, session by session. A session is one synthetic bundle -- the
clean one, or the clean one with one defect -- synthesised, then analysed in
full.

| | before, s | after, s |
|---|---:|---:|
| synthesis, 13 sessions | 95.58 | 11.68 |
| analysis, 13 sessions | 290.15 | 76.24 |
| one session, least to most | 26.51 (`wrong-input`) to 31.41 (`delay-slip`) | 5.89 to 7.94 (`clean`) |
<!-- claim: grep="13 sessions +95[.]58 +290[.]15 +385[.]73" in=docs/t-physical-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="13 sessions +11[.]68 +76[.]24 +87[.]91" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

No defect stands out. `delay-slip` is the dearest because its synthesis warps
its take twice; `wrong-input` is the cheapest because the reference is not on
the declared inputs, so the take's later checks are skipped. There is no
repeated work between sessions to share: each is a different synthesis and a
different analysis.

Where the seconds went inside the control, by function. A function's "self"
is everything inside it that calls no other timed function, numpy included.

| `synthetic-defects`, self s | before | after |
|---|---:|---:|
| `interp`, 304,427 calls | 356.86 | 59.09 |
| &nbsp;&nbsp;of which inside `warp_reference`, 322 calls | 294.96 | 33.91 |
| &nbsp;&nbsp;of which inside `unwarp_capture`, 52 calls | 38.48 | 4.75 |
| &nbsp;&nbsp;of which one position at a time (`ncc_lag`, `_refine_peak`), 304,053 calls | 23.42 | 20.44 |
| `tone_f0`, 52 calls | 8.23 | 8.13 |
| `correlate`, 7,201 calls | 7.61 | 7.56 |
| `band` and `envelope`, 1,274 calls | 5.43 | 5.49 |
| everything else | 7.63 | 7.69 |
| the child's `main` | 385.77 | 87.95 |
<!-- claim: grep="interp +356[.]86 +356[.]86 +304427" in=docs/t-physical-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="interp +59[.]09 +59[.]09 +304427" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

Before, `interp` was 92.5 % of the control: the analysis and the synthesis
alike are warps and unwarps of signals 100,000 to 330,000 long, and every
position cost 64 sines and 64 Bessel functions. The instrument's `--bench`
puts the kernel at 4.80 s per million positions when evaluated per sample, on
the same runner.
<!-- claim: grep="direct +4[.]802" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

## What changed, and what was left alone

`interp` now tabulates the kernel once per tap count (`KERNEL_SAMPLES`, 8,192
rows per unit of the fractional part) and reads a position's weights off it
with the cubic through the four neighbouring rows. A position whose taps all
fall outside the signal is left at zero. The shipped kernel reads 0.548 s per
million positions, 8.8 times faster than the formula, and differs from it on
the warped reference by 1.2e-14.
<!-- claim: grep="shipped +0[.]548 +1[.]2e-14" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

The kernel's error is a property of the table, not of any input, so one scan
of the fractional part bounds it for every input there is: 3.5e-14 per tap at
64 taps and 1.4e-13 at the 32 the peak searches use, against a kernel whose
peak is 1 -- and the same again in sum, which is the most one output can be
off by, relative to the largest sample it reads. The takes the control
analyses are 24-bit (least significant bit 1.2e-7) and the reference they are
compared with is 16-bit (3.0e-5). Away from
the window's edge the table is fourth order in its row spacing; at the two
taps that sit on the Kaiser window's clipped edge it is second order (a
fourth per doubling against a sixteenth elsewhere), and those set the bound.
<!-- claim: grep="cubic-8192 +64_taps: 3[.]5e-14 3[.]6e-14 +32_taps: 1[.]4e-13 1[.]4e-13" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

Three things in the control's neighbourhood were measured and not taken:

| | s per million positions | error against the formula | why not |
|---|---:|---|---|
| the formula with the trimmed skeleton, bit for bit (`lean`) | 3.85 | 0 | a fifth off the kernel; the table takes 8.8 |
| a linear table, 16,384 rows, in the old skeleton | 0.472 | 3.2e-10 on the reference; 7.8e-9 in sum at worst | about 13 s a run more, by estimate and not by measurement (the two skeletons' measured ratio applied to it); its bound is 15 times under the takes' least significant bit, where the cubic's is three million |
| sharing the unchanged takes of each single-take defect | -- | -- | in three of the ten (`one-channel`, `pitch`, `clock-drift`) the takes after the defect draw a different noise from the clean session's, so "unchanged" is not the same bytes without care; and synthesis is 11.7 s of the 88 s now |
<!-- claim: grep="table-linear-16384 +0[.]472 +3[.]2e-10" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

## The control still catches everything it is registered to catch

Both runs print the properties x defects matrix
(`docs/verification-rules.md` rule 4), and the two are the same line for line:
the 11 observable defects each FAIL for their intended property, `swap` is
`BLIND as declared` and PASSes, the clean session PASSes. The instrument
compares nothing itself; it reads `controls.json`, and the two runs' copies of
it were compared after the fact: all twelve defect records -- verdict, caught,
the properties that failed, the four reasons, the outcome -- are identical.

What moved is the clean run's two known answers, in their eighth and ninth
digits: the clock estimate from 34.94256273 to 34.942562655 ppm (7.5e-14 in
the clock ratio itself) and the gain from -10.465504174 to -10.465504173 dB.
<!-- claim: grep="clean: PASS known answers \{'ppm': \[35[.]0, 34[.]94256273\], 'gain_db': \[-10[.]457574905606752, -10[.]465504174\]\}" in=docs/t-physical-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="clean: PASS known answers \{'ppm': \[35[.]0, 34[.]942562655\], 'gain_db': \[-10[.]457574905606752, -10[.]465504173\]\}" in=docs/t-physical-cost-2026-11-28/after/summary.txt -->

The same comparison is a committed test at a smaller scale:
`test_a_session_analysed_with_the_tabulated_kernel_is_the_same_measurement`
analyses one session both ways and requires the same verdict, the same
failures and the same scope, every metric to 1e-6. The known answers move
because the peak searches are a 40-step golden section, whose last digits
depend on differences far below the table's error; every take's own metric,
which is rounded to three to five decimals, is the same.

## What remains, and why it is real

The control is 88 s of the test's 92 now:

- `interp`, 59 s: 39 s are about 67 million positions warped, at the table's
  0.55 s per million; 20 s are the 304,053 calls that interpolate one position
  each. Each of those costs about 67 us, nearly all of it interpreter and
  numpy call overhead for 32 taps, and `_refine_peak` makes 42 of them per
  correlation peak. A path that evaluates one position without the chunk
  machinery would be bit for bit the same and might take half of the 20 s.
  Not done: it is a separate change to a different function.
- `tone_f0`, 8 s: 52 calls, 160 ms each, of a golden-section refinement that
  evaluates the DTFT at the whole span's length, 60 times.
- `correlate`, 7.6 s: the FFT correlations themselves.
- `band` and `envelope`, 5.5 s: the zero-phase filters.
- about 8 s: writing and reading 13 sessions of 24-bit audio, their noise,
  hashing, loading the reference, the peak searches' own arithmetic, the rest.

None of that is work repeated, and none of it is cut by this change. Two
further levers were not measured and are not claimed: running the thirteen
independent sessions on more than one of the runner's four vCPUs, and a
cheaper one-position interpolation.

## What one run cannot say

The hosted runners are not one machine. The same two files' step in
`trial-physical` took 400 to 885 s across the six `main` runs before this
change (`main-trial-physical.txt`), and in the "after" run the test took 55.73 s
in the job that ran both files and 86.32 s in the job that ran it alone, in
the same workflow eighteen minutes apart.
<!-- claim: grep="55[.]73s call" in=docs/t-physical-cost-2026-11-28/after/pytest-both-files.txt -->
<!-- claim: grep="46147611182 8f3d8ed2 success 2026-11-28T13:02:30Z 419 400" in=docs/t-physical-cost-2026-11-28/main-trial-physical.txt -->
<!-- claim: grep="46146778263 560a1580 success 2026-11-28T12:52:10Z 912 885" in=docs/t-physical-cost-2026-11-28/main-trial-physical.txt -->

So the pairs that matter are the ones measured against each other in one
workflow on one runner type: the un-instrumented test 400.56 to 86.32 s, the
instrument's total 396.21 to 91.99 s, the thirteen sessions 385.73 to 87.91 s.
What the `tools` job itself will show is its next run on `main`.

## Wrong, then right

Five things in this work were wrong before they were right. Each was caught
by a measurement or by a control, none by reading.

1. The first cubic table used Catmull-Rom tangents, on the belief that it was
   fourth order. A scan said 7e-9 at 256 rows where fourth order gives 1e-10:
   Catmull-Rom reproduces quadratics, not cubics. It is the four-point
   Lagrange cubic now, and the scan is the committed test.
2. I expected the table's error not to depend on the tap count. It does: the
   32-tap table read 5.5e-13 where the 64-tap one read 1.4e-13, at the same
   resolution, and the committed test went red on it. The table doubled to
   8,192 rows for both.
3. The instrument's `--smoke` run first exited 0 on a trial that had refused
   in preflight: the launcher it wrote for the children did not run. Its own
   test caught that no child had been timed; `--smoke` now refuses on any
   apparatus problem, as the measurement does.
4. The committed equivalence test first compared metrics to 1e-9 absolute and
   failed on the clock estimate by 2e-8 ppm. The tolerance is relative, 1e-6,
   and the docstring says why.
5. I was about to quote 55.73 s as "the" time after. The same test, alone, in
   the same run, took 86.32 s. Both are quoted, and the runner spread with
   them.

## Reproducing

The workflow is `workflow.yml` in the record: the `tools` job's own steps
minus `iverilog`, on a `push` to a `throwaway/**` branch, so that nothing
else in CI runs. The "before" run had two jobs; the third, the two test files
as `trial-physical` runs them, was added for "after".

```
python -m pytest tools/test_r0_capture.py -q -k test_t_physical_on_a_synthetic_bundle --durations=5
python tools/r0_controls_profile.py --out build/r0-profile --bench
python -m pytest tools/test_r0_capture.py tools/test_r1_capture.py -q --durations=12
```

`python tools/r0_controls_profile.py --out DIR --smoke` exercises the
instrument in about two seconds and measures nothing; its report says so.
