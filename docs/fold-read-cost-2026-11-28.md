# Where the fold's reads went, and what a table gave back (#2112)

**Result.** `_read_at` was not negligible. In the run that reads the fold
most, the `FOLD_SINC_TAPS` sweep probe, it was 32 % of the wall: 73.9 s of
228.4 s. In the other sweep probe it was 41 %, and in the module's own tests
34 %. In the suite the issue named first, `model/test_discrimination.py`, it
was under 1 %. The kernel depends on the fractional part of a position alone,
so it is now read off a table built once per tap width. The same probe on the
same kind of runner then spent 31.1 s of 190.3 s in `_read_at`, and the
module's tests 2.7 s of 19.3 s. A fold at a whole period is still plain
indexing, and the table is within 1e-12 of the formula everywhere else.

This is measurement tooling. No sound, RTL or image change is claimed.

## The facts, none of them inferred

| | |
|---|---|
| the instrument | `tools/fold_read_profile.py`; its controls are `tools/test_fold_read_profile.py` |
| before, run | [46172801612](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46172801612), `push`, head `df2be420`: `origin/main` `8d4adf6e` plus the instrument (`1b157670`, `781e7664`) and one workflow file |
| after, run | [46174507777](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46174507777), `push`, head `7f5a3e95`: the same instrument plus the kernel table (`1501a7d4`) and the same workflow |
| what did not change between them | the instrument's behaviour: `git diff 781e7664 1501a7d4 -- tools/fold_read_profile.py` is its module docstring and nothing else |
| runner | `GitHub Actions` label `ubuntu-24.04`, AMD EPYC 7763, 4 vCPU, on both runs |
| stack | Python 3.12.16, pytest 9.2.0, numpy 2.5.5, scipy 1.18.1 (`pip install --upgrade`, unpinned) |
| both verdicts | `CONTROL`: every check of the apparatus passed, and the headline's slowed read came back dominant |
| where it was not run | the build box. The host this was written on has no `.env` and no `repo-remote` alias, so the box was not provisioned there; a hosted runner was used, as #1944's measurement was |

Every number from here on was produced by `tools/fold_read_profile.py` from
the record in `docs/fold-read-cost-2026-11-28/`: `before/` and `after/` hold
each run's `report.json` and printed `summary.txt`, `first-run-refused/` the
run the instrument refused, `workflow.yml` the workflow as run, `runs.txt` the
runs' ids and times, and `kernel-rows.txt` the output of
`tools/probes/fold_kernel_rows.py`. The instrument runs each candidate in a
fresh interpreter with `_read_at` wrapped, and again with no wrapper, three
times each, and REFUSES unless the run passed every test it collected, every
module holding the family ended wrapped, the repeats read the same counts,
the wrapper's own bookkeeping is under a tenth of what it booked, the
headline's share moved by less than five points, and each instrumented wall
is within 1.5x of its bare twin.

<!-- claim: grep="AMD EPYC 7763 64-Core Processor x 4, load 0[.]81/0[.]27/0[.]10 at start, source node/D-drums-bitexact-4050-gdf2be420" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="AMD EPYC 7763 64-Core Processor x 4, load 2[.]10/0[.]79/0[.]29 at start, source node/D-drums-bitexact-4051-g7f5a3e95" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="^fold_read_profile: CONTROL" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="^fold_read_profile: CONTROL" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->

## What `_read_at` cost, run by run

Before the table, on the kernel as it was on `main`. The five collected files
are the ones CI and `make verify` run; the three scripts are probes a person
runs by hand. Each line is the median of three instrumented runs, and each
bare twin is the median of three runs with no wrapper.

- `fold_rate_sweep`, the probe: 228.40 s of wall, 73.919 s of it in `_read_at` (32.43 %), 16,072 calls, 81,037,500 kernel positions and 13,506,250 read by the index-only path, which is the probe's `taps = 0` arm. <!-- claim: grep="fold_rate_sweep +script +no +228[.]40 +73[.]919 +32[.]43% +32[.]36% [.][.] +32[.]47% +16072 +81037500 +13506250" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `rate_tol_sweep`, the other probe: 91.91 s, 37.678 s (40.98 %), 4,397 calls, 25,796,513 kernel positions. <!-- claim: grep="rate_tol_sweep +script +no +91[.]91 +37[.]678 +40[.]98%" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `self-tests`, the 32 tests in `model/discrimination_features.py`: 23.43 s, 7.866 s (33.54 %), 940 calls, 5,439,551 kernel positions. <!-- claim: grep="self-tests +pytest +yes +23[.]43 +7[.]866 +33[.]54% +33[.]48% [.][.] +33[.]69% +940 +5439551" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `test_dphase_floor_census`: 5.27 s, 2.260 s (42.90 %). <!-- claim: grep="test_dphase_floor_census +pytest +yes +5[.]27 +2[.]260 +42[.]90%" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `test_fold_rate_sweep`: 8.50 s, 2.504 s (29.46 %). <!-- claim: grep="test_fold_rate_sweep +pytest +yes +8[.]50 +2[.]504 +29[.]46%" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `dphase_floor_sweep`: 13.52 s, 1.601 s (11.83 %). <!-- claim: grep="dphase_floor_sweep +script +no +13[.]52 +1[.]601 +11[.]83%" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `test_discrimination`, the suite the issue named first: 46.81 s, 0.412 s (0.88 %), 62 calls. <!-- claim: grep="test_discrimination +pytest +yes +46[.]81 +0[.]412 +0[.]88%" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
- `test_promoted_bands`: 2.59 s, 0.012 s (0.48 %), one call. <!-- claim: grep="test_promoted_bands +pytest +yes +2[.]59 +0[.]012 +0[.]48%" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->

The headline is `fold_rate_sweep`, the run that read the most kernel positions.
<!-- claim: grep="headline fold_rate_sweep: read the most kernel positions [(]81037500[)]; `_read_at` is +32[.]43% of its wall, +73[.]92 s of 228[.]40 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->

The instrumented walls sat on their bare twins: 228.40 s against 227.42 s for
the headline, 23.43 s against 23.41 s for the module's tests, 46.81 s against
47.01 s for `test_discrimination`.
<!-- claim: grep="bare twin, no wrapper, median of 3: 227[.]42 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="bare twin, no wrapper, median of 3: 23[.]41 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="bare twin, no wrapper, median of 3: 47[.]01 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->

## The rule, fixed before the measurement

The instrument declares its rule in the same commit as the instrument, before
any run: `_read_at` is MATERIAL in a run when it is at least 10 % of that
run's wall time AND at least 10 s of one run of it. Both, because each alone
misleads: a third of a two-second file does not pay for a kernel that stops
being the formula between whole positions, and thirty seconds is worth
nothing to a run nobody waits on. A share is an upper bound on what a table
can recover, since the gather and the sum over the taps stay.

By that rule two runs were material before the table: `fold_rate_sweep`
(32.43 %, 73.9 s) and `rate_tol_sweep` (40.98 %, 37.7 s). The three collected
files with a share near a third were each under ten seconds: 7.9 s, 2.5 s and
2.3 s. The rule decides when a table is worth building. It does not promise
that the share falls under the bound once it is built, and it did not: see
below.

<!-- claim: test=tools/test_fold_read_profile.py::test_material_needs_both_a_tenth_of_the_run_and_ten_seconds -->

## What the table gave back

The same instrument, the same kind of runner, the kernel read off the table.

| run | wall, s | `_read_at`, s | share |
|---|---:|---:|---:|
| `fold_rate_sweep` | 228.40 to 190.33 | 73.919 to 31.141 | 32.43 % to 16.36 % |
| `rate_tol_sweep` | 91.91 to 72.52 | 37.678 to 13.131 | 40.98 % to 18.13 % | |
| `self-tests` | 23.43 to 19.30 | 7.866 to 2.698 | 33.54 % to 13.75 % | |
| `test_dphase_floor_census` | 5.27 to 4.12 | 2.260 to 0.813 | 42.90 % to 19.65 % | |
| `test_fold_rate_sweep` | 8.50 to 7.19 | 2.504 to 0.819 | 29.46 % to 11.42 % | |
| `dphase_floor_sweep` | 13.52 to 12.62 | 1.601 to 0.562 | 11.83 % to 4.45 % | |
| `test_discrimination` | 46.81 to 46.50 | 0.412 to 0.193 | 0.88 % to 0.42 % | |
| `test_promoted_bands` | 2.59 to 2.51 | 0.012 to 0.025 | 0.48 % to 1.01 % | |

<!-- claim: grep="fold_rate_sweep +script +no +190[.]33 +31[.]141 +16[.]36%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="rate_tol_sweep +script +no +72[.]52 +13[.]131 +18[.]13%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="self-tests +pytest +yes +19[.]30 +2[.]698 +13[.]75%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="test_dphase_floor_census +pytest +yes +4[.]12 +0[.]813 +19[.]65%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="test_fold_rate_sweep +pytest +yes +7[.]19 +0[.]819 +11[.]42%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="dphase_floor_sweep +script +no +12[.]62 +0[.]562 +4[.]45%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="test_discrimination +pytest +yes +46[.]50 +0[.]193 +0[.]42%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="test_promoted_bands +pytest +yes +2[.]51 +0[.]025 +1[.]01%" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->

Per position and tap, by arithmetic on the headline's `mpd` rows, a read cost
92 ns before the table and 31 ns after at 16 taps, and 78 ns and 26.5 ns at 32.
<!-- claim: grep="_fold_at<-mpd_features@16 +2112 calls +11479952 positions +16[.]872 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="_fold_at<-mpd_features@32 +2112 calls +11479952 positions +28[.]681 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="_fold_at<-mpd_features@16 +2112 calls +11479952 positions +5[.]654 s" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->
<!-- claim: grep="_fold_at<-mpd_features@32 +2112 calls +11479952 positions +9[.]755 s" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->

Five things a reader should know about that table.

The sweep probe is 38.07 s shorter and the other 19.39 s shorter, by
arithmetic on the two columns: the wall moved by less than `_read_at` did,
which is all the run gained.

`self-tests` after the table holds 35 tests, not 32: the three the repair
added. Its 952 calls read 18,756 more kernel positions than before, and the
repair's own tests are part of what the 19.30 s buys.
<!-- claim: grep="self-tests +pytest +yes +19[.]30 +2[.]698 +13[.]75% +13[.]69% [.][.] +13[.]98% +952 +5458307" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->

`test_promoted_bands` got slower inside `_read_at`, from 12 ms to 25 ms: it
makes one fold, and one fold now pays for the whole table. That is the
shape of the cost -- built once per tap width per process, then read -- and
at 13 ms it is noise in a 2.5 s file.

By the rule the two sweep probes are still material after the table, at
16.36 % and 18.13 %. What the table removed is the difference between the
two columns: the evaluation of the kernel. What is left is everything else
`_read_at` does -- the padded gather, the product and the sum over the taps,
the normalisation -- which is arithmetic on an array of positions by taps and
not a kernel. Another table has nothing there to look up.

The control ran on both heads: with the headline's `_read_at` made to run
five times over it came back 68.71 % of its run, and ten times over on the
after head, 65.79 %. A slowed `_read_at` is reported as dominant on the real
headline, not only in the unit tests.
<!-- claim: grep="made to run 5 times over is +68[.]71% of its run, 314[.]74 s of 458[.]11 s" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="made to run 10 times over is +65[.]79% of its run, 263[.]35 s of 400[.]31 s" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->

## What changed, and what was left alone

`_read_at` now evaluates the Hann-windowed sinc once per tap width, at
FOLD_KERNEL_ROWS (8,192) nodes of the fractional part and one node beyond each
end, and reads between them by the cubic through four neighbouring rows.

- What stays exact: a whole position reads at fractional part 0, a node, where the cubic returns the table's own row -- the formula's weights, to the bit -- at every width on the grid. <!-- claim: test=model/discrimination_features.py::test_at_an_integer_position_the_table_is_the_formula_to_the_bit -->
- That is the property `test_the_fold_reduces_to_indexing_on_a_whole_period` rests on, and that test is unchanged and green. <!-- claim: test=model/discrimination_features.py::test_the_fold_reduces_to_indexing_on_a_whole_period -->
- A read that has its table calls neither `sinc` nor `cos`: the evaluation is gone, not merely cheaper. <!-- claim: test=model/discrimination_features.py::test_the_kernel_is_tabulated_not_evaluated_per_position -->

What is bounded instead: between nodes the table is not the formula. Over a
scan of 200,001 points of [0, 1] its largest error at the shipped rows is
2.9e-13 for one tap at the narrowest width on the grid and 3.9e-15 in sum at
the widest; at the shipped width of 16 taps it is 4.4e-16 for one tap and
2.2e-15 in sum, which is where floating-point rounding already is. The bound
the test holds is KERNEL_ERROR_BOUND, 1e-12 per tap and in sum: a thousand
times under the 1e-9 the whole-period test demands, and 120,000 times under
the 1.2e-7 least significant bit of a 24-bit sample.
<!-- claim: grep="8192 +1 +2[.]880e-13 +2[.]887e-13 +FOLD_KERNEL_ROWS" in=docs/fold-read-cost-2026-11-28/kernel-rows.txt -->
<!-- claim: grep="8192 +32 +5[.]551e-16 +3[.]910e-15 +FOLD_KERNEL_ROWS" in=docs/fold-read-cost-2026-11-28/kernel-rows.txt -->
<!-- claim: grep="8192 +16 +4[.]441e-16 +2[.]215e-15 +FOLD_KERNEL_ROWS" in=docs/fold-read-cost-2026-11-28/kernel-rows.txt -->
<!-- claim: test=model/discrimination_features.py::test_the_kernel_table_is_the_formula_over_the_whole_fractional_part -->

The rows were swept before 8,192 was chosen, over 64 to 16,384 at every width
on the grid (`tools/probes/fold_kernel_rows.py`; it is arithmetic, the same on
every host, so it has no bare twin). A 64-row table misses the bound at every
width -- 6.7e-07 for one tap at the narrowest -- and is the control that keeps
the bound honest. Doubling the rows past 8,192 buys the table nothing at the
shipped width: 16,384 rows read the same 4.4e-16.
<!-- claim: grep="64 +1 +6[.]703e-07 +8[.]977e-07 +-" in=docs/fold-read-cost-2026-11-28/kernel-rows.txt -->
<!-- claim: grep="16384 +16 +4[.]441e-16 +2[.]085e-15 +-" in=docs/fold-read-cost-2026-11-28/kernel-rows.txt -->
<!-- claim: test=tools/probes/test_fold_kernel_rows.py::test_a_bound_the_coarsest_table_meets_holds_nothing -->

Left alone, and why:

- FOLD_SINC_TAPS, the window, and the legacy `taps < 0` and index-only
  `taps <= 0` paths. The kernel's dial is still the dial; every registered
  parameter still ships a value its own sweep covers.
- The sum over the taps is now taken in one piece, so the last bits of a read
  at a fractional position differ from the per-tap accumulation the loop
  used. No test in the tree reads those bits as far as the suites that fold
  were run before this record: the module's own, `model/test_discrimination.py`,
  the fold probes' own tests, `tools/test_sensitivity.py` and the
  instrument's. That is a run reported in the pull request, not a marker
  here.
- The committed sweep records under `docs/sensitivity/` were measured with
  the kernel as it was, and were not regenerated. The one about this kernel
  reads a 10 % relative band as flat; the table is within 2.2e-15 in sum of
  that kernel at the shipped width. That is arithmetic on the bound against
  the record's stated rule, not a re-measurement of the record.
<!-- claim: grep="flat_within_relative" in=docs/sensitivity/fold-interpolator.json -->

## The census, which this instrument cannot run

`tools/dphase_floor_census.py` calls the fold once per corpus row and is
excluded from the ranking, with its reason in the instrument: it needs the
Fischer corpus and computes its rows in a process pool, so a wrapper in the
parent would time none of its folds and would read exactly like a free kernel.
The census conditions each row as the study does, cut to its 0.24 s window,
and calls the fold once. What the instrument can run is one `extra_features`
call on a clip of that length: 72,836 kernel positions in 13 calls, and
`_read_at` 55.12 % of its 191.0 ms before the table and 31.38 % of 143.0 ms
after.
<!-- claim: grep="`_read_at` +55[.]12% of 191[.]0 ms, 72836 kernel positions, 13 calls" in=docs/fold-read-cost-2026-11-28/before/summary.txt -->
<!-- claim: grep="`_read_at` +31[.]38% of 143[.]0 ms, 72836 kernel positions, 13 calls" in=docs/fold-read-cost-2026-11-28/after/summary.txt -->

The census has 116 reference rows and 116 render rows, so by arithmetic on
that one call it held about 24 s of `_read_at` before the table and about
10 s after, spread over its pool. That is an estimate and not a measurement:
the census was not run, its total wall is not known here, and the clip the
estimate was made on is a square wave, which reads the same positions as a
drum but is not one.

## The instrument, and the run it refused

The instrument's first hosted run refused itself. Its pytest plugin re-checks
the `_read_at` binding around every test by walking `sys.modules`, and
resolved every module's path on every walk; on that runner it cost about
40 ms a test, and every collected candidate came back slower than its bare
twin -- `test_promoted_bands` 7.61 s against 2.05 s. The bare gate refused
the run, after thirty-nine minutes, and nothing in it was reported as
measured.
<!-- claim: grep="test_promoted_bands: timed at 7[.]61 s against 2[.]05 s with no wrapper: off by more than 1[.]5x" in=docs/fold-read-cost-2026-11-28/first-run-refused/summary.txt -->
<!-- claim: grep="^fold_read_profile: REFUSED" in=docs/fold-read-cost-2026-11-28/first-run-refused/summary.txt -->

`_is_family` now answers from a per-path cache, a sweep is under a
millisecond, and the test that holds it there was red first on the same
host at 7.13 ms over 430 modules.
<!-- claim: test=tools/test_fold_read_profile.py::test_the_plugin_s_sweep_costs_far_less_than_a_test -->

The bare twins in both later runs sit on their instrumented walls, which is
what shows the fix and not the claim.

## Wrong, then right

- The first hosted run of the instrument was refused by the instrument, for
  the instrument's own overhead. Its numbers are on disk in
  `first-run-refused/` and none of them is quoted above.
- Before the after run, a scratch timing on the dispatch host said the table
  was no faster than the formula: 213 ms against 249 ms at 16 taps. It was
  taken at 384,000 positions a call, where the table is memory-bound, and
  nothing in the tree makes a call that size; the sweep reads about 5,400
  positions a call. At that size the same scratch said 2.0x at 16 taps and
  2.8x at 32, and the hosted runs, not the scratch, decided.
- The issue's own suspects were both wrong about where the cost was. It
  named `model/test_discrimination.py` first; that suite reads 0.88 % of its
  wall in `_read_at`, and the probe it named second is the headline.

Two measurements of this instrument were wrong before they were right, both
caught by a gate and not by reading: the bare gate, and the size of a scratch
call.

## The record of #2069's class search, corrected

#2069's class search recorded that `np.sinc`, `special.i0` and `kaiser`
across `tools/ model/ fpga/ rtl-sketch/ pnr/` had "no other per-sample kernel
evaluation". It was one instance short: `_read_at`, the instance this issue
is about. Re-run on this tree, that search returns seven files. Four build an
FIR prototype once (`model/alias_probe.py`, `model/filter_rate_chain.py`,
`tools/gen_rate_conv_2x.py`, `fpga/verify_sd_dac.py`); one is the #1944
instance itself, `tools/r0_capture.py`, still per-sample on `main` until
#2069 lands; `model/discrimination_features.py` now evaluates its kernel once
per tap width; and `tools/probes/fold_kernel_rows.py` evaluates the formula
to compare the table against, which is its job.

Widened to the class rather than the three names -- every window, every FIR
prototype and every resampler call (`blackman`, `hanning`, `hamming`,
`firwin`, `resample_poly`, `resample`) across those directories and `spec/`
and `audition/` -- 49 files, and every site in them is a fixed-length window
over a frame, an FIR prototype, or scipy's own resampler, built once per call.
The per-position reads written by hand in the tree are 30 `np.interp` sites
outside tests and one linear fractional read: arithmetic, and no kernel to
evaluate. On this tree, after this change, the only per-position kernel
evaluation left is `tools/r0_capture.py`'s `interp`, which #2069 tabulates.
