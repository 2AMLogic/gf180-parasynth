# The `m5a-fast` NO-VERDICT of 2026-11-28: what was measured, and what was not

Issue #1730. The 46-file pytest unit of `make verify-fast` hit `tools/run_all.py`'s
600 s per-command cap -- a **NO-VERDICT**, not a test failure -- on PR #1683 at
`29905da9`, run [46119792436](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46119792436)
attempt 1. It had passed in 450.0 s at `2d70924` one hundred minutes earlier,
and the question was whether the batch had grown or the runner had been slow.

**The batch did not grow. The runner was slow, and the same commit passed on a
faster one 49 minutes later.** Every one of the twelve units of the job was
slower in the failing execution by about the same factor, including ones that
take two seconds. This file is the measurement behind that, and also the record
of what could not be measured from the dispatch host that took it: the batch in
isolation on an idle machine, and the time of any single test inside it.

The instrument is `tools/ci_verify_fast_timing.py`; the numbers below are
reproduced offline from the committed snapshot
`docs/verification/m5a-fast-timing-2026-11-28.json` (section 7). Nothing here
claims the NO-VERDICT is gone: two were lost in the four hours measured, and on
the slower half of the runner fleet the batch sits within 30 s of the cap.

## 1. The facts, none of them inferred

The three executions of PR #1683's two revisions. `test_run_case` is
`tools/test_run_case.py`, a single test file that is almost never in a diff;
"batch" is the 46-file unit, slug `test_filter_rate_chain`. Both are units of
the same `run_all.py` invocation, on the same machine, started in the same
second.

| run | att | sha | conclusion | `test_run_case` | batch | runner image | to the gate step |
|---|---|---|---|---|---|---|---|
| 46112179003 | 1 | `2d70924` | success | 275.7 s | 450.0 s | ubuntu-26.04 `20261116.202` | 25 s |
| 46119792436 | 1 | `29905da9` | **failure, NO-VERDICT** | **364.5 s** | **≥600.0 s** (cap) | ubuntu-26.04 `20261122.9` | 24 s |
| 46119792436 | 2 | `29905da9` | success | 278.0 s | 445.4 s | ubuntu-26.04 `20261122.9` | 26 s |

Attempt 2 is the same commit as attempt 1: same source, same recipe, same
image, same unpinned `pip install`. The only variable is the machine GitHub
assigned. All twelve units of attempt 1, against attempt 2:

| unit | attempt 1 | attempt 2 | × |
|---|---|---|---|
| `test_run_case` | 364.5 | 278.0 | 1.31 |
| batch (46 files) | ≥600.0 | 445.4 | ≥1.35 |
| fpga batch (27 files) | 98.4 | 69.7 | 1.41 |
| `verify_mono_case` | 95.2 | 63.4 | 1.50 |
| `measure_m5a_signal_path` | 68.9 | 52.5 | 1.31 |
| `inject_manifest_defects` | 9.9 | 7.9 | 1.25 |
| `verify_live_midi` | 7.2 | 5.7 | 1.26 |
| `test_pulse_oversample` | 4.4 | 2.7 | 1.63 |
| `sound_report --check-locks` | 1.9 | 1.3 | 1.46 |
| `test_audio_measure` | 2.4 | 1.2 | 2.00 |
| `check_workflows` | 0.5 | 0.3 | 1.67 |
| `check_attested_hashes` | 0.1 | 0.0 | -- |

Every unit is slower. The batch's factor is in the middle of its siblings'. Its
last printed progress before the cap was `[ 96%]` followed by 27 dots, so it
was past 96 % of its tests when `run_all.py` killed it.

## 2. The two revisions: what changed, and what the batch could see

`2d70924` is an ancestor of `29905da9`; 26 commits apart, the branch absorbing
`main`. Read with `git`, not from a checkout:

| | |
|---|---|
| the 46 batch files, `git diff --stat 2d70924 29905da9 -- <46 paths>` | **empty** -- byte-identical |
| `def test_` functions across the 46 files | **667 at both** |
| `.py` modules changed between the revisions | 13 |
| of those, imported or named by any of the 46 batch files | **0** |
| `verify-fast` recipe, `Makefile` lines 269-282 | byte-identical |
| tracked files (what `test_refusal_exit_census.py` walks) | 3426 → 3428 (+0.06 %) |
| whole-tree change | 18 files, +1337 / −63 |

The ~1,300 lines the incident report cited as growth are real, and none of
them are in the batch, imported by it, or walked by it at a scale that could
matter: two new tracked files out of 3,426. There is no test-specific
regression to find between these two revisions because there is no change for
one to live in.

## 3. The population: 79 executions over four hours

Every `m5a-fast` execution in the last 100 runs of `rungs.yml`, every attempt,
2026-11-28 03:07 → 07:17 UTC: 82 executions, 79 with a summary table, 3 skipped
by the scope filter, 0 unreadable. 78 distinct SHAs, 52 branches, 69
`pull_request` and 10 `push`. All on GitHub-hosted `ubuntu-latest`.

Per-unit wall clock, completed executions only:

| unit | n | censored | min | median | max |
|---|---|---|---|---|---|
| batch (46 files) | 79 | **2** | 320.3 | 459.0 | 596.1 |
| `test_run_case` | 79 | 0 | 215.8 | 281.8 | 372.4 |
| fpga batch | 79 | 0 | 51.6 | 76.8 | 98.4 |
| `verify_mono_case` | 79 | 0 | 51.0 | 74.0 | 101.8 |
| `measure_m5a_signal_path` | 79 | 0 | 33.2 | 56.2 | 75.6 |
| `inject_manifest_defects` | 79 | 0 | 6.2 | 8.4 | 11.1 |
| the six remaining units | 79 each | 0 | 0.0 | ≤5.6 | ≤7.7 |

**The ratio batch / `test_run_case`, same job, same machine:** n = 77
completed, median **1.616**, p05-p95 1.464-1.673, range 1.208-1.738. The two
censored executions give lower bounds of **1.646** and **1.663** -- inside the
completed distribution. A batch that had grown would carry a higher ratio on
the runs where it was slow; it carries the same one. The rank correlation of
the two units' log times across the 77 pairs is **0.925**: two independent
test suites in two processes, moving together run by run.

The one execution with a ratio below 1.4 (1.208, `c38077e4`, PR #1686) is on
a branch whose diff removes `tools/test_mono_m1a_score.py` from the batch. It
is a real change to the unit, and it is what a real change looks like to this
instrument.

**Two modes, interleaved.** Splitting `test_run_case` at 310 s:

| | n | `test_run_case` median | batch median | batch ≥ 570 s | batch ≥ cap |
|---|---|---|---|---|---|
| fast | 41 | 273.9 | 441.2 | 0 | 0 (max 500.3) |
| slow | 38 | 357.9 | 579.5 | **27** | **2** |

The slow executions are spread across the whole window, not clustered in one
incident, and `push` and `pull_request` share them (5 of 10, 33 of 69). The
batch is the last unit to finish in **79 of 79** executions: it is the gate's
critical path, and the gate's wall time is its wall time.

**The second NO-VERDICT.** Run 46113592887, `933cb995`, `feature/issue-1677`,
05:46 UTC: `test_run_case` 360.8 s, batch censored at 600.0 s, 18 other runs in
flight. A different branch, a different commit, the same slow mode. The
incident report knew of one lost verdict; there were two.

**FAIL is not NO-VERDICT.** The same window holds 10 `FAIL(1)` unit results in
5 executions: the batch and `tools/check_attested_hashes.py` together in four,
the batch alone in one. Those are assertions -- results -- and are not counted
above. No unit other than the batch was ever censored.

## 4. What this supports, and what it leaves open

**Supported:** the timeout is runner variation, not growth. The batch's code,
test count and imports are identical at the two revisions (section 2); its
duration tracks an unrelated sibling's at a stable ratio across 77 executions
and 78 SHAs (section 3); the same commit is fast or slow depending only on
which machine it lands on (section 1). On a fast runner the batch has never
been above 500.3 s, 99.7 s under the cap; on a slow one it is within 30 s of
the cap 27 times in 38.

**Not supported:** the concurrency explanation as stated. "15 runs in flight"
does not establish contention on the particular VM a job received. Of the 38
slow executions, **16 had ≤ 21 other runs of this workflow in flight**; the
overlap count's rank correlation with `test_run_case` is 0.283. It is a
partial predictor at best (every execution with ≥ 28 in flight was slow), and
it cannot see the neighbours GitHub put on the host.

**Open.** *Why* roughly half the runners are slow is below what a job can
observe: same image, same agent version, same stack, 31 % apart. Whether the
batch alone on an idle machine would show the same ratio to its sibling as it
does under the parallel recipe is unmeasured (section 5). Which tests inside
the batch take the time is unmeasured, because the recipe runs `pytest -q`
without `--durations`.

**Corroboration with a different ruler.** PR #1686's `Makefile` comment
normalised the same runs by `measure_m5a_signal_path` instead and predicted
578 s for the batch on run 46113592887; the slow-mode median here is 579.5 s.

## 5. What was not measured, and why

- **The pinned build box.** The acceptance criteria ask for matched timings of
  the batch at both revisions on an idle box, alternating order. The dispatch
  host that did this work has no build box provisioned -- no `REPO_REMOTE_*`
  in a `.env`, no `repo-remote` ssh alias, no `~/.config/repo/` -- and is a
  shared 8-vCPU worker at load 11-15, where a 450-600 s pytest batch does not
  belong. The hosted-CI population is the substitute, and it is **not
  controlled**: GitHub assigns the runner, the sample is whatever the fleet
  sent, and `2d70924` ran once. The same-SHA pair in section 1 is the one
  comparison that holds everything but the machine fixed.
- **The isolated command.** Everything here is the **shipped parallel
  recipe** -- 12 units, `run_all.py`'s `min(len(cmds), os.cpu_count())`
  workers -- never the batch alone. The job log does not print the core count,
  so the worker allocation is "whatever the same image reported", which is the
  same on both attempts and is all the comparison needs.
- **Per-test attribution.** `-q` prints no durations. Adding `--durations` to
  the recipe would supply it, and is a recipe change (section 6).

## 6. The recipe: no change here

Raising the cap and dropping coverage are out of scope. De-duplicating the
batch is owned: #812 rules on it, #1360 drops 37 `tools/` paths (its reduced
command ran in 44.6 s hosted on `b5a7ad1c`), and PR #1686 drops one more. This
file changes no recipe and is the **before** measurement for all three.

What a repair has to clear, from section 3: on the slow half of the fleet the
batch's median is 579.5 s against a 600.0 s cap, so a repair that leaves the
batch as the critical path leaves the NO-VERDICT rate at about what it is
(2 of 79 in four hours). A repair that removes it from the critical path --
#1360's shape -- removes the failure mode. Re-measure on the revision that
ships it, with the gate this tool carries:

```
python tools/ci_verify_fast_timing.py --limit 100 --require-clean --window 40
```

It exits 1 while any execution in the window lost a verdict, 2 if it could not
read the window, 0 only when it read the window and found none.

## 7. Reproduce

```
python tools/ci_verify_fast_timing.py \
  --offline docs/verification/m5a-fast-timing-2026-11-28.json --list
python tools/ci_verify_fast_timing.py --limit 100 --snapshot /tmp/m5a.json   # live, ~5 min
```

The committed snapshot was taken on 2026-11-28 at about 08:15 UTC with
`gh` 2.102.0. Its records carry every unit's state, wall clock and censoring
per execution, so every number above is one `--offline` run away.

## 8. Apparatus: `gh run view --job` serves the latest attempt

`gh run view --job 140901610556 --log` (gh 2.102.0) printed a summary reading
`12/12 passed` at 08:05:13Z. The API says that job id is attempt 1 of run
46119792436, conclusion `failure`, started 07:08:28Z: the red execution. The
command had served **attempt 2's log** for attempt 1's job id, with no error.
`gh run view 46119792436 --attempt 1 --log` prints the right one
(`11/12 passed -- NO-VERDICT`, 07:18:53Z).

This was caught only because the timestamp disagreed with the incident
report's; a builder who trusted it would have recorded the failing run as a
pass. The collector never uses `--job`; it addresses each attempt explicitly,
and the control that pins this is the test below.

<!-- claim: test=tools/test_ci_verify_fast_timing.py::test_collect_fetches_each_attempt_by_number_and_never_by_job_id -->

A censored unit is the other place this instrument could lie: its printed
`secs` is the cap it ran into, and a median that counted 600.0 as a duration
would read as a batch that got slower and then finished. Censored values are
counted apart and kept out of every min, median and max.

<!-- claim: test=tools/test_ci_verify_fast_timing.py::test_a_censored_unit_is_counted_apart_and_excluded_from_completed_stats -->

## 9. Wrong, then right

Four numbers or readings of this session were wrong before they were right.
Three were caught by a control; the fourth by re-deriving it from its log
before commit, which is the weaker catch and is listed as such.

1. A shell probe of which changed modules the batch imports printed "46 of 46
   batch files" for all thirteen modules. The loop matched `$m` but the
   variable was `$M`, so the pattern was empty. Impossible uniformity was the
   tell; redone in Python, the answer is 0 (section 2).
2. The first copy of the failing log was attempt 2's (section 8). The
   timestamp disagreed with the report's; the attempt was then fetched by
   number.
3. From the first 30 rows of the listing, the slow runs looked concentrated
   between 06:27 and 07:15 UTC, which would have read as one capacity event.
   The full 79 show them interleaved across four hours (section 3).
4. The first draft of section 1's table gave `2d70924`'s setup as 17 s, typed
   from the neighbouring rows instead of read from its log. It is 25 s.

A reader should weight any single figure above accordingly; every figure in
sections 1-3 was re-read from the committed snapshot after these.
