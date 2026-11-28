# `pytest tools/` on more workers: eighteen hosted runs of one commit, and what each candidate costs

Issue #1947. The `tools` job of `.github/workflows/rungs.yml` runs
`python -m pytest tools/ -q` in one process. #1816 established where its
42-84 minutes go -- real test work, spread over 41 files, no overhead to
remove -- so the one lever left that touches no test is running the directory
on more than one worker. The question was whether that cuts **whole-job wall
time** on a hosted `ubuntu-24.04` runner with every test still collected
exactly once and coming out the same.

This is the record of eighteen runs of ONE commit: four pytest-xdist
candidates (the shipped single process, `-n 2`, `-n auto`, `-n 8`) three
times each, and a two-shard split of the directory three times each. Every
figure below is the output of a script over the committed record
(`derive.py` in this directory, `tools/xdist_run_table.py`,
`tools/check_collection_union.py`); none was typed from a log.

## The answer

**Every test ran exactly once, in all eighteen runs.** 6678 tests
collected by directory, 6678 in each run's junit, none missing, none
extra, none twice. The check that says so was made to go red on these very
records by removing one test from them (below).

**`-n 2` is the only xdist candidate that changes nothing.** All three runs
report the single-process 6649 passed, 27 skipped, 2 xfailed. Whole-job time
32.9-48.8 min against 50.2-85.8 min single-process on the same commit, and
1.77x on the one CPU model where both landed.

**`-n auto` is faster and not free.** The runner has 4 vCPUs, so `auto` is 4
workers: 26.6-45.3 min, but all three runs SKIP seven
tests that pass single-process. `fpga/midi_session.py` refuses `--port sim`
when the one-minute load average exceeds the CPU count ("the simulated board
runs on the wall clock, so this would measure the other load, not the
session"), and four pytest workers with their child processes push a 4-vCPU
runner past that.

**`-n 8` is unusable.** The same seven skips, and in two of three runs four
to six tests FAIL by their own `subprocess.run(timeout=)`: eight workers on
four vCPUs take a control that needs 55-109 s single-process
to its 300 s deadline and stop it there. In the one run that passed, that
control still took 233-274 s.

**The two-shard split gives `auto`'s cut with none of its costs.** Two file
lists, one process each, on two runners: 33.9-45.2 min for the slower half,
1.90x on the same CPU model, outcomes identical to the single process in all
three pairs, and the deadline-carrying controls at single-process speed.

**The spread at one commit is the runner.** Between 50.2 and 85.8 min
for three single-process runs of one command, every test took about the same
fraction longer (per-test ratio p10 1.43, median 1.69, p90 1.94),
and CPU-seconds grew with them (4110 -> 6699): nothing stalled, the
machine was slower.

## The facts, none of them inferred

| | |
|---|---|
| the xdist runs | [46155589951](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46155589951), `push`, head `7a3455a6`: 12 jobs, all started 2026-11-28T14:37:08Z, one per matrix cell, on 12 distinct runners |
| the shard runs | [46162689576](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46162689576), `push`, head `5aee6785`: 6 jobs, all started 2026-11-28T15:59:05Z |
| what `7a3455a6` is | `origin/main` at `8ebfe4cd` plus `tools/mem_peak.py` and its test, plus one file, `.github/workflows/tools-xdist-once.yml` (reproduced in this directory). The `tools/` tree is `94f35b97` |
| what `5aee6785` is | `7a3455a6` plus one file, `.github/workflows/tools-shards-once.yml`. The same `tools/` tree, `94f35b97` |
| the candidates | `n1`: the shipped command verbatim plus the recording flags; `n2`: `-n 2`; `nauto`: `-n auto`; `n8`: `-n 8`; `s0`/`s1`: two file lists, one pytest process each, on two runners |
| the recording flags | `--durations=0 --junitxml=<file> -rX`, which #1816 measured as free |
| the wrappers | `tools/mem_peak.py --interval 0.5` around `tools/run_with_rss.py` around pytest: peak of the summed `VmRSS` over the process tree, CPU user+sys seconds, and the exit status passed through unchanged |
| runner | `GitHub Actions 1000679031`-`…042` for the first twelve, `1000680370`-`…375` for the shards: `os.cpu_count()` 4, affinity 4, 2 cores x 2 threads, `MemTotal` 15983 MB, on every one of the 18 runners |
| CPUs seen | AMD EPYC 7763 64-Core Processor (7); AMD EPYC 9V74 80-Core Processor (4); AMD EPYC 9V45 96-Core Processor (3); Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz (2); Intel(R) Xeon(R) 6973P-C (1); INTEL(R) XEON(R) PLATINUM 8573C (1) |
| stack | Python 3.12.16, pytest 9.2.0, pytest-xdist 3.8.0, execnet 2.1.2, numpy 2.5.5, scipy 1.18.1, soundfile 0.14.0, PyYAML 6.0.4 (all `pip install --upgrade`, unpinned, as the shipped job installs them), iverilog 12.0 |
| what the suite is here | 6678 tests in 241 files; single-process 6649 passed, 27 skipped, 2 xfailed. At #1816's commit it was 6550 / 27 / 2: 99 more tests pass here, with the same 27 skipped and 2 xfailed by count |
| what is committed | under `docs/ci-tools-xdist-measurement-2026-11-28/`: per run `env.txt`, `mem.json`, `union.json` (the collection check's report), `pytest-tail.txt` (from the summary line on), `sha256.txt` and `collect-ids.sha256`; the two jobs documents the wall times come from; both workflows as they ran; the derived tables; `derive.py` and `build_record.py.txt` |
| what is not | `junit.xml` (0.45-0.95 MB each), `pytest.log` (0.16-0.52 MB) and `collect.txt` (0.65 MB): the run's artifacts on GitHub for 90 days, their sha256 in each run's record; the throwaway branches, deleted after the runs. Neither workflow was ever on `main`, and no `tools/` duplicate of the job exists |

The shipped job at the same commit is the measurement's external check: the
`n1` candidate IS that job, step for step, and reports the same
6649 passed, 27 skipped, 2 xfailed three times.

## Wall time

Whole-job time is the Actions API's `started_at` to `completed_at` for the
job -- checkout, pip, apt, the run, the collection, the upload -- the same
measure as #1816's table, so the two compare like with like. `pytest's own`
is the `in <t>s` of its summary line. For the shards, a pair's wall time is
the slower of its two jobs, since the split is done when both are.

| candidate | repeat | runner | job wall (min) | pytest step (min, API) | pytest's own (s) | CPU user+sys (s) | CPU / wall | peak tree RSS (MB) | max processes | pytest's summary line | collection union |
|---|---|---|---|---|---|---|---|---|---|---|---|
| n1 | 1 | GitHub Actions 1000679035 | 85.8 | 85.0 | 5101.14 | 6699 | 1.31 | 1485 | 8 | 6649 passed, 27 skipped, 2 xfailed in 5101.14s (1:25:01) | 6678 ran once; outcomes identical to baseline |
| n1 | 2 | GitHub Actions 1000679038 | 50.2 | 49.6 | 2975.45 | 4110 | 1.38 | 1461 | 8 | 6649 passed, 27 skipped, 2 xfailed in 2975.45s (0:49:35) | 6678 ran once; outcomes identical to baseline |
| n1 | 3 | GitHub Actions 1000679036 | 65.2 | 64.7 | 3876.44 | 5172 | 1.33 | 1490 | 8 | 6649 passed, 27 skipped, 2 xfailed in 3876.44s (1:04:36) | 6678 ran once; outcomes identical to baseline |
| n2 | 1 | GitHub Actions 1000679042 | 48.8 | 48.1 | 2886.71 | 7068 | 2.45 | 1811 | 12 | 6649 passed, 27 skipped, 2 xfailed in 2886.71s (0:48:06) | 6678 ran once; outcomes identical to baseline |
| n2 | 2 | GitHub Actions 1000679039 | 32.9 | 32.4 | 1942.66 | 4892 | 2.52 | 1960 | 12 | 6649 passed, 27 skipped, 2 xfailed in 1942.66s (0:32:22) | 6678 ran once; outcomes identical to baseline |
| n2 | 3 | GitHub Actions 1000679037 | 48.2 | 47.6 | 2857.87 | 7004 | 2.45 | 1808 | 12 | 6649 passed, 27 skipped, 2 xfailed in 2857.87s (0:47:37) | 6678 ran once; outcomes identical to baseline |
| nauto | 1 | GitHub Actions 1000679032 | 26.6 | 26.1 | 1567.51 | 5585 | 3.56 | 2668 | 18 | 6642 passed, 34 skipped, 2 xfailed in 1567.51s (0:26:07) | 6678 ran once; 7 outcome differences vs baseline |
| nauto | 2 | GitHub Actions 1000679033 | 45.3 | 44.7 | 2683.11 | 9404 | 3.50 | 2725 | 14 | 6642 passed, 34 skipped, 2 xfailed in 2683.11s (0:44:43) | 6678 ran once; 7 outcome differences vs baseline |
| nauto | 3 | GitHub Actions 1000679034 | 32.9 | 32.2 | 1934.22 | 7009 | 3.62 | 2820 | 18 | 6642 passed, 34 skipped, 2 xfailed in 1934.22s (0:32:14) | 6678 ran once; 7 outcome differences vs baseline |
| n8 | 1 | GitHub Actions 1000679041 | 47.5 | 46.9 | 2812.99 | 10042 | 3.57 | 4172 | 18 | 6 failed, 6636 passed, 34 skipped, 2 xfailed in 2812.99s (0:46:52) | 6678 ran once; 13 outcome differences vs baseline |
| n8 | 2 | GitHub Actions 1000679040 | 33.7 | 33.1 | 1986.44 | 7277 | 3.66 | 4689 | 23 | 4 failed, 6638 passed, 34 skipped, 2 xfailed in 1986.44s (0:33:06) | 6678 ran once; 11 outcome differences vs baseline |
| n8 | 3 | GitHub Actions 1000679031 | 24.9 | 24.4 | 1466.55 | 5457 | 3.72 | 4566 | 26 | 6642 passed, 34 skipped, 2 xfailed in 1466.55s (0:24:26) | 6678 ran once; 7 outcome differences vs baseline |
| s0 | 1 | GitHub Actions 1000680375 | 45.2 | 44.5 | 2669.55 | 3273 | 1.23 | 919 | 8 | 3211 passed, 20 skipped, 2 xfailed in 2669.55s (0:44:29) | 6678 ran once; outcomes identical to baseline |
| s0 | 2 | GitHub Actions 1000680372 | 33.9 | 33.3 | 1998.88 | 2573 | 1.29 | 927 | 8 | 3211 passed, 20 skipped, 2 xfailed in 1998.88s (0:33:18) | 6678 ran once; outcomes identical to baseline |
| s0 | 3 | GitHub Actions 1000680370 | 44.9 | 44.2 | 2649.46 | 3253 | 1.23 | 936 | 8 | 3211 passed, 20 skipped, 2 xfailed in 2649.46s (0:44:09) | 6678 ran once; outcomes identical to baseline |
| s1 | 1 | GitHub Actions 1000680374 | 45.0 | 44.3 | 2657.37 | 3669 | 1.38 | 1225 | 6 | 3438 passed, 7 skipped in 2657.37s (0:44:17) | 6678 ran once; outcomes identical to baseline |
| s1 | 2 | GitHub Actions 1000680373 | 33.6 | 32.9 | 1974.81 | 2761 | 1.40 | 1181 | 6 | 3438 passed, 7 skipped in 1974.81s (0:32:54) | 6678 ran once; outcomes identical to baseline |
| s1 | 3 | GitHub Actions 1000680371 | 43.6 | 43.0 | 2576.16 | 3611 | 1.40 | 1193 | 6 | 3438 passed, 7 skipped in 2576.16s (0:42:56) | 6678 ran once; outcomes identical to baseline |

Read as min / median / max of whole-job minutes over three runs of one
commit:

| candidate | runs | job wall min | job wall median | job wall max | pytest's own median (s) | CPU / wall median | peak tree RSS max (MB) |
| n1 | 3 | 50.2 | 65.2 | 85.8 | 3876 | 1.33 | 1490 |
| n2 | 3 | 32.9 | 48.2 | 48.8 | 2858 | 2.45 | 1960 |
| nauto | 3 | 26.6 | 32.9 | 45.3 | 1934 | 3.56 | 2820 |
| n8 | 3 | 24.9 | 33.7 | 47.5 | 1986 | 3.66 | 4689 |
| s0 | 3 | 33.9 | 44.9 | 45.2 | 2649 | 1.23 | 936 |
| s1 | 3 | 33.6 | 43.6 | 45.0 | 2576 | 1.40 | 1225 |
| two shards, the slower half | 3 | 33.9 | 44.9 | 45.2 | | | |

Three things in that table are not about workers.

**The runner moves the number more than the candidate does.** The same
command on the same commit ran 50.2 and 85.8 minutes (the
spread section below). A candidate's three runs span 1.5x (`n2`),
1.7x (`nauto`) and 1.9x (`n8`); the single process
spans 1.7x. With three samples per candidate, a difference between
candidates smaller than that spread is not resolved by this measurement.

**CPU-seconds are not a fixed quantity of work.** If the suite were so many
CPU-seconds of work spread over N workers, CPU user+sys would be constant
across candidates and CPU/wall would be N. It is neither: CPU/wall is
1.33 single-process, 2.45 at two workers, 3.56 at
four and 3.66 at eight, and the total CPU spent grows with the
number of workers and with the slowness of the runner (below). The extra is
spent inside the tests, not waiting: this is what contention looks like when
it is measured in CPU-seconds rather than in wall time.
<!-- claim: grep="^\| n8 \| 3 \| 24[.]9 \| 33[.]7 \| 47[.]5 \| 1986 \| 3[.]66 \| 4689 \|$" in=docs/ci-tools-xdist-measurement-2026-11-28/table.md mechanism=inferred why="that the extra CPU-seconds are contention (most plausibly BLAS threads spinning, since the tools job does not pin them -- only the python job does, rungs.yml env block) is a reading of CPU/wall and of the deadline table; no run pinned the threads to test it" note="the MEASURED part is the table: CPU user+sys per run, in table.md. The mechanism is not load-bearing for any decision here" -->

**Memory is not the constraint.** Peak summed RSS over the tree:
1490 MB single-process, 1960 MB at two workers,
2820 MB at four, 4689 MB at eight, against 15983 MB on the
runner. The largest single process never exceeded 1452 MB.
At eight workers the tree held up to 26 processes at once.

## Why the wall time is not work / N

The work profile, from the fastest single-process run's `--durations=0`
block:

single-process reference: `xdist-n1-r2`, pytest's own 2975.4 s

| tests with a printed duration | summed call (s) | longest one test (s) | >= 60 s | >= 30 s | >= 10 s |
|---|---|---|---|---|---|
| 2591 | 2782 | 238 | 2 | 13 | 69 |

the longest: `tools/test_r0_capture.py::test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught`

| workers | summed call / N (s) | floored by the longest test (s) | as minutes |
|---|---|---|---|
| 1 | 2782 | 2782 | 46.4 |
| 2 | 1391 | 1391 | 23.2 |
| 4 | 695 | 695 | 11.6 |
| 8 | 348 | 348 | 5.8 |

If tests ran at their single-process speed on N workers, two workers would
finish in 23.2 min and four in 11.6. The measured medians are
48.2 and 32.9 min. The gap is not scheduling: on a
4-vCPU runner each test runs slower while others run beside it, and the
deadline table in the next section but one shows how much, test by test.
The floor of the split is also visible: the longest single test is
238 s, so no schedule of any kind finishes this directory in under
4.0 min on that runner.

## Every test ran exactly once, and the check was seen to fail

`tools/check_collection_union.py` compares two things from two sources: the
node ids of a fresh single-process `pytest --collect-only -q tools/`, and the
junit of the run under test. A junit `<testcase>` exists only where pytest
produced a report, so it is evidence the test ran, not that it was
collected. junit carries no node ids; each expected id is keyed the way
pytest 9.2 keys its own junit row and the rows are matched to that table.
The verdict is PASS only if every expected id has exactly one testcase and
nothing else ran; with a baseline, any test whose outcome class moved is
listed by name.

Against the single-process baseline `n1-r1`:

| candidate | runs | union: collected, ran exactly once | outcomes against `n1-r1` | pytest's own summary line |
|---|---|---|---|---|
| the shipped command, single process | 3 | 6678, 6678 | identical | 6649 passed, 27 skipped, 2 xfailed |
| `-n 2` | 3 | 6678, 6678 | identical | 6649 passed, 27 skipped, 2 xfailed |
| `-n auto` (4 workers) | 3 | 6678, 6678 | 7 per run | 6642 passed, 34 skipped, 2 xfailed |
| `-n 8` | 3 | 6678, 6678 | 7, 11, 13 per run | 4 failed, 6638 passed, 34 skipped, 2 xfailed; 6 failed, 6636 passed, 34 skipped, 2 xfailed; 6642 passed, 34 skipped, 2 xfailed |
| two shards, their junit unioned, per pair | 3 | 6678, 6678 | identical | per half: 3211 passed, 20 skipped, 2 xfailed; 3438 passed, 7 skipped |

Each row is the union check over that candidate's three runs, run as

```
python tools/check_collection_union.py --expected <run>/collect.txt \
    --junit <run>/junit.xml --pytest-log <run>/pytest.log \
    --baseline-junit xdist-n1-r1/junit.xml --baseline-log xdist-n1-r1/pytest.log
```

and for the shards, `--junit xdist-s0-r<k>/junit.xml --junit
xdist-s1-r<k>/junit.xml`: the union of the two shards is what must equal the
by-directory collection. A file absent from both lists would have shown as
`missing`; a file in both, as `duplicate`. Neither happened.

Three things make that PASS worth more than a count:

1. **The collection itself was cross-checked between environments.** The
   `collect.txt` of every run has the same node-id lines as a collection of
   the same commit on a different machine (`git archive 7a3455a6` plus
   `pytest --collect-only`, 12.17 s, elsewhere): identical bytes except the
   count line's elapsed time, sha256 `903944b654f6c0ce` before that line in all of
   them.
2. **The check was made to go red on these records.** Its `control` mode
   injects each defect it names into a real run and prints which property saw
   it. On `n2-r1` against `n1-r1` the matrix is below the list. Removing the
   last collected test from the junit gives `FAIL`, `missing (1)`, that test's
   node id -- the outcome the issue's second criterion asks for -- and nothing
   else moved.
3. **Its parser was held to pytest's own arithmetic.** Given the log, the
   per-test classes it reads from junit are totalled and must equal pytest's
   summary line; a disagreement is REFUSED. Every one of the 18 runs agreed.
   The tool's own tests pin that against a real pytest run with every outcome
   class in it, including the one junit cannot see: a non-strict XPASS is a
   bare `<testcase>`, indistinguishable from a pass, and is split out from
   the log's `XPASS` lines instead. For the six shard runs the same
   cross-check was run against each shard's own file list and its own log;
   all six agreed.
```
control                  missing     extra duplicate   outcome   summary collectio     junit       key  baseline  verdict
-------------------------------------------------------------------------------------------------------------------------
clean                          -         -         -         -         -         -         -         -         -  PASS
omit-one                   MOVED     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND  FAIL
extra-one                  BLIND     MOVED     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND  FAIL
ran-twice                  BLIND     BLIND     MOVED     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND  FAIL
outcome-changed            BLIND     BLIND     BLIND     MOVED     BLIND     BLIND     BLIND     BLIND     BLIND  FAIL
junit-edited               BLIND     BLIND     BLIND     BLIND     MOVED     BLIND     BLIND     BLIND     BLIND  REFUSED
collection-truncated       BLIND     BLIND     BLIND     BLIND     BLIND     MOVED     BLIND     BLIND     BLIND  REFUSED
junit-empty                BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     MOVED     BLIND     BLIND  REFUSED
key-ambiguous              BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     MOVED     BLIND  REFUSED
baseline-incomplete        BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     BLIND     MOVED  REFUSED
```
The matrix is diagonal: every injected defect was seen by the property meant
to see it and by nothing else. On the red run `n8-r1` the same command exits
1 with `clean ... FAIL NOT CAUGHT`: it will not certify a record whose clean
comparison fails (`control-matrix-n8-r1.txt`).
<!-- claim: test=tools/test_check_collection_union.py::test_control_omitting_one_test_turns_the_check_red_and_names_it -->
<!-- claim: test=tools/test_check_collection_union.py::test_the_parser_agrees_with_pytest_s_own_count_on_every_class -->

## What changed the outcomes

### Seven tests skip at four workers and at eight

In every `nauto` and `n8` run, the seven CLI controls of
`tools/test_midi_session_kit_refusal.py` are SKIPPED where single-process and
`-n 2` PASS them. The skip reason is in each junit: `NO VERDICT: midi_session
refused --port sim because this host is loaded`. The chain is in the code:
`fpga/midi_session.py`'s `host_overloaded()` returns the load when
`os.getloadavg()[0] > os.cpu_count()`, `--port sim` REFUSES on it, and the
test file turns that refusal into a skip on purpose ("a busy machine cannot
turn them red for a reason that is not the kit's"). Four workers on four
vCPUs, each with its own child processes, put the one-minute load average
above 4 by the time that file runs; two workers do not.
<!-- claim: grep="^\| nauto \| 3 \| 26[.]6 \| 32[.]9 \| 45[.]3 \| 1934 \| 3[.]56 \| 2820 \|$" in=docs/ci-tools-xdist-measurement-2026-11-28/table.md mechanism=derived why="the refusal rule is read from fpga/midi_session.py host_overloaded() and the skip from tools/test_midi_session_kit_refusal.py; the load average itself was not recorded in the runs" note="the seven skips are MEASURED (union.json of every nauto and n8 run); what was not measured is the load average at the moment they ran" -->

So this is not a wrong answer and not corruption between tests: it is an
instrument with a precondition, refusing to measure a loaded host, exactly as
this repository's rules ask it to. The cost is real all the same: at four or
eight workers, seven refusal-boundary controls of the MIDI session path never
run.

Every skip, by the reason junit records, in the first repeat of each
candidate (`skips.md`):

skipped testcases by the reason recorded in junit, first repeat of each candidate (for the shards, the two halves sum to the single process)

| reason | n1 | nauto | n8 | s0 | s1 |
|---|---|---|---|---|---|
| `NO VERDICT: midi_session refused --port sim because this host is loaded` | 0 | 7 | 7 | 0 | 0 |
| `GF180_TR808_FISCHER (the Fischer corpus) not set` | 6 | 6 | 6 | 5 | 1 |
| `OPTIONAL local run, skipped: the Fischer corpus is not present at /tmp/tr808-ref (no cy8/); clone tidalcycles/sounds-tr808-fischer there or set GF180_TR808_REFS` | 5 | 5 | 5 | 5 | 0 |
| `OPTIONAL local run, skipped: the Fischer corpus is not at /tmp/tr808-ref (no bd8/BD5050.WAV); clone tidalcycles/sounds-tr808-fischer there or set GF180_TR808_REFS` | 4 | 4 | 4 | 0 | 4 |
| `could not import 'serial': No module named 'serial'` | 3 | 3 | 3 | 3 | 0 |
| `alpha-law-strike is not decidable without the corpus` | 2 | 2 | 2 | 2 | 0 |
| `alpha-law-tail is not decidable without the corpus` | 2 | 2 | 2 | 2 | 0 |
| `the Fischer corpus is not present at /tmp/tr808-ref/cy8` | 2 | 2 | 2 | 2 | 0 |
| `could not import 'sympy': No module named 'sympy'` | 1 | 1 | 1 | 0 | 1 |
| `frozen cache not restored; an absent cache is not a caught defect` | 1 | 1 | 1 | 0 | 1 |
| `the Fischer corpus is not present at /tmp/tr808-ref` | 1 | 1 | 1 | 1 | 0 |

The 27 skips of the single process are the corpus, `pyserial` and `sympy`
being absent on the runner, and one unrestored cache; every candidate has
the same 27, and `nauto` and `n8` add the one MIDI line and nothing else. In
the shards the two halves sum to the single process on every line.
<!-- claim: grep="midi_session refused --port sim because this host is loaded" in=docs/ci-tools-xdist-measurement-2026-11-28/skips.md note="the skip reason, read from the junit of nauto-r1 and n8-r1 by derive.py: 7 and 7 against 0 single-process" -->

### At eight workers, tests die by their own deadlines

Two files under `tools/` give a subprocess a wall-clock deadline:
`tools/test_audio_distance_floor_refusal.py` (`timeout=300`) and
`tools/test_m1a_refusal_boundaries.py` (`timeout=180`). The #1773 controls
they run are the three ~90-s full-run controls of #1816's table. Call
seconds, per run:

call seconds per repeat; `timed out` is the test's own `subprocess.run(timeout=)` expiring

| test (single-process s) | deadline | n1 | n2 r1 / r2 / r3 | nauto r1 / r2 / r3 | n8 r1 / r2 / r3 | its shard, r1 / r2 / r3 |
|---|---|---|---|---|---|---|
| `test_audio_distance_floor_refusal.py::test_full_run_with_mt50_cut_into_the_strike_exits_refused_` (55 s) | 300 s | 109 / 55 / 72 | 146 / 86 / 147 | 131 / 238 / 159 | timed out / timed out / 263 | 110 / 72 / 107 |
| `test_audio_distance_floor_refusal.py::test_full_run_on_an_unmodified_corpus_exits_0` (54 s) | 300 s | 109 / 54 / 72 | 145 / 101 / 146 | 137 / 243 / 154 | timed out / timed out / 274 | 110 / 73 / 106 |
| `test_audio_distance_floor_refusal.py::test_full_run_with_bd5050_cut_into_the_strike_exits_refuse` (54 s) | 300 s | 108 / 54 / 71 | 143 / 87 / 135 | 138 / 249 / 163 | timed out / timed out / 233 | 110 / 71 / 107 |
| `test_m1a_refusal_boundaries.py::test_unqualified_reference_manifest_exits_refused_2[analys` (33 s) | 180 s | 50 / 33 / 40 | 51 / 33 / 51 | 63 / 112 / 84 | timed out / 132 / 102 | 51 / 40 / 46 |
| `test_m1a_refusal_boundaries.py::test_non_refused_exception_is_not_swallowed_by_cli_boundar` (33 s) | 180 s | 50 / 33 / 40 | 51 / 33 / 51 | 55 / 89 / 67 | timed out / timed out / 113 | 51 / 40 / 50 |

worst observed call time as a share of the deadline, by candidate:

- n1: 36%
- n2: 49%
- nauto: 83%
- n8: 100%
- shard: 37%

Single-process, those controls use 18% of their deadline on the fast runner
and 36% on the slowest. At `-n 2`, up to 49%; at `-n auto`,
up to 83%; at `-n 8`, 100% of it, in two runs out of
three, the m1a controls in one of them; in the shards, 37%, the
single process's own figure. A deadline is a precondition of the same class as the load average
above, calibrated on a host where the suite is the only load.
<!-- claim: grep="timed out / timed out / 263" in=docs/ci-tools-xdist-measurement-2026-11-28/deadline-headroom.md mechanism=derived why="each failure's traceback in pytest.log is subprocess.TimeoutExpired at the test's own timeout= value, and the durations block gives the call seconds; the cause of the slowdown per se is the CPU/wall observation above" note="the timeouts are MEASURED; the files' timeout= values are read from the measured commit" -->

### Nothing else moved

Across the fifteen runs measured against `n1-r1`, the parity check found 52
outcome differences, and every one of them is one of three things: the seven
MIDI controls going PASSED -> SKIPPED (42, in the six `nauto` and `n8` runs),
the five deadline-carrying controls going PASSED -> FAILED (9), and
`tools/test_mem_peak.py`'s sampling control (1, the entry under "Wrong before
right" below). The two xfails stayed xfails in all 18 runs and the 27 skips of
the single process stayed skipped. No test produced a different value: what
xdist changed here is whether tests ran inside their preconditions, never what
they concluded.

## The spread on one commit

| run | CPU | pytest's own (s) | CPU user+sys (s) | CPU / wall |
|---|---|---|---|---|
| -r1 | AMD EPYC 7763 64-Core Processor | 5101.1 | 6699 | 1.31 |
| -r2 | AMD EPYC 9V45 96-Core Processor | 2975.4 | 4110 | 1.38 |
| -r3 | AMD EPYC 9V74 80-Core Processor | 3876.4 | 5172 | 1.33 |

per-test ratio of call seconds, fast run -> slow run (tests of >= 1 s in the fast run):

- `-r2` -> `-r1`: 295 tests, p10 1.43, median 1.69, p90 1.94; summed call 2782 -> 4772 s (1.72)
- `-r3` -> `-r1`: 344 tests, p10 1.17, median 1.28, p90 1.48; summed call 3631 -> 4772 s (1.31)
- `-r2` -> `-r3`: 295 tests, p10 1.16, median 1.30, p90 1.44; summed call 2782 -> 3631 s (1.31)

Three single-process runs of one commit span 50.2-85.8 min. <!-- claim: grep="^\| n1 \| 3 \| 50[.]2 \| 65[.]2 \| 85[.]8 \|" in=docs/ci-tools-xdist-measurement-2026-11-28/table.md note="min, median and max whole-job minutes of the three single-process runs" -->
#1816's twenty runs on `main` spanned 42.1-84.4; this is that spread, on a
fixed revision, so the code cannot be its cause.

What the per-test ratios say: between the fastest and the slowest run,
essentially every test took the same fraction longer. Nothing stalled, no
test waited on a port or a download, and the suite's shape did not change --
the machine executed the same work 1.72x slower. CPU-seconds grew
by nearly the same proportion (4110 -> 6699, 1.63x), which rules out waiting (waiting lengthens wall time
without spending CPU) and leaves a slower effective clock: the CPU model, its
frequency under a neighbour's load, or SMT siblings busy with someone else's
work.
<!-- claim: grep="^- .-r2. -> .-r1.: 295 tests, p10 1[.]43, median 1[.]69, p90 1[.]94" in=docs/ci-tools-xdist-measurement-2026-11-28/spread.md mechanism=inferred why="the uniform per-test ratio and the equal growth in CPU-seconds are MEASURED (spread.md); WHICH property of the runner makes it slower -- model, frequency, a co-tenant -- was not measured: each run recorded only its CPU model, not its frequency or its host" note="runner identity is the only variable the measurement holds; the cause inside the runner is not load-bearing for the decision, which is that the spread is not the code's" -->

What the CPU models say: the slow runs were the AMD EPYC 7763 runners and the
fast ones the EPYC 9V45, in every candidate where both appear. Holding the
model fixed separates the runner from the candidate:

whole-job minutes, CPU model down and candidate across; a cell lists every run of that pair

| CPU | n1 | n2 | nauto | n8 | s0 | s1 |
|---|---|---|---|---|---|---|
| AMD EPYC 7763 64-Core Processor | 85.8 | 48.2, 48.8 | 45.3 | - | 44.9, 45.2 | 45.0 |
| AMD EPYC 9V74 80-Core Processor | 65.2 | - | 32.9 | 33.7 | - | 33.6 |
| AMD EPYC 9V45 96-Core Processor | 50.2 | - | 26.6 | 24.9 | - | - |
| Intel(R) Xeon(R) 6973P-C | - | 32.9 | - | - | - | - |
| Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz | - | - | - | 47.5 | - | 43.6 |
| INTEL(R) XEON(R) PLATINUM 8573C | - | - | - | - | 33.9 | - |

same-model speed-up against the single process (n1 wall / candidate wall):

- AMD EPYC 7763 64-Core Processor: n2 1.77x, nauto 1.89x, s0 1.90x, s1 1.91x
<!-- claim: grep="7763 64-Core Processor: n2 1[.]77x, nauto 1[.]89x, s0 1[.]90x, s1 1[.]91x" in=docs/ci-tools-xdist-measurement-2026-11-28/by-model.md note="the same-model speed-ups this section quotes" -->
- AMD EPYC 9V45 96-Core Processor: nauto 1.89x, n8 2.01x
- AMD EPYC 9V74 80-Core Processor: nauto 1.98x, n8 1.93x, s1 1.94x

So, on one model, `-n 2` is worth 1.77x, `-n auto` 1.89-1.98x, `-n 8`
1.93-2.01x and the shard split 1.90-1.94x, where the runner alone is worth up
to 1.7x. Each model has one to seven samples, and the twelve runs of the first
measurement shared the Actions pool at one time, so co-tenancy cannot be
separated from the model here.

## The two-shard split

The second mechanism the issue named, run because xdist at four workers and
above changes outcomes and at eight kills tests by deadline. The 241 files
were split into two lists, packed on `n1-r2`'s per-test seconds, and each
list ran as ONE pytest process on its OWN runner; a pair is done when both
halves are.

| pair | `s0`: CPU, whole-job min | `s1`: CPU, whole-job min | the pair (slower half) | union of both junit files | outcomes against `n1-r1` |
|---|---|---|---|---|---|
| 1 | AMD EPYC 7763 64-Core Processor, 45.2 | AMD EPYC 7763 64-Core Processor, 45.0 | 45.2 | 6678, 6678 ran once | identical |
| 2 | INTEL(R) XEON(R) PLATINUM 8573C, 33.9 | AMD EPYC 9V74 80-Core Processor, 33.6 | 33.9 | 6678, 6678 ran once | identical |
| 3 | AMD EPYC 7763 64-Core Processor, 44.9 | Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz, 43.6 | 44.9 | 6678, 6678 ran once | identical |

- **Wall.** The slower half of a pair took 33.9-45.2 min
  (median 44.9). On the EPYC 7763, the one CPU model where both
  a single-process run and a whole pair landed, that is 1.90x
  against 85.8 min -- the cut `-n auto` gives on the same model
  (1.89x), without its costs below. On the EPYC 9V74 the `s1`
  half alone gives 1.94x.
- **Balance.** The two halves finished within 73 s of each
  other in all three pairs, including the pair whose halves ran on different
  CPU models (Xeon Platinum 8573C and EPYC 9V74). Packed on one run's
  durations, the split held on four other models.
- **Outcomes.** Identical to the single process in all three pairs:
  6649 passed, 27 skipped, 2 xfailed. The seven MIDI controls ran and passed, and the deadline
  controls ran at single-process speed: at worst 37% of
  their deadline, against 36% single-process and
  83% at `-n auto`.
- **Cost.** CPU/wall 1.23-1.40, the single process's own
  range, where `-n auto` holds 3.56 CPUs busy: no CPU is spent on
  contention. Peak summed RSS 936-1225 MB. What it costs
  instead is a second runner for the length of the slower half, about
  90 runner-minutes a pair on the slow runners against
  85.8 for the single process there -- the same runner time, spent
  twice.
- **The guard is the point.** Two by-name lists can lose a file silently,
  which the shipped job's by-directory invocation cannot. The union check over
  both junit files, per pair, is what says these did not, and would say so on
  every future run.

## What this does not establish

- **Which runner property makes the same work cost 1.3-1.7x more.** Measured:
  that it is uniform across tests and shows in CPU-seconds. Not measured:
  the cause inside the runner.
- **That pinning BLAS threads would recover the per-test slowdown.** The
  `tools` job does not pin them (`rungs.yml` sets the five thread variables
  only on the `python` job), so each numpy-heavy test may start four BLAS
  threads; N such tests on four vCPUs oversubscribe. That fits the CPU/wall
  column and the deadline table. No run pinned them.
- **A two-shard split balanced on anything but one run's durations.** The
  lists were packed on `n1-r2`; another runner would pack them slightly
  differently. They are fixed text, so the balance drifts as the suite grows.
- **Whether the 7 skips and the deadline failures happen at `-n 3`.** Not
  run.
- **That the junit and the log are the same run.** Nothing in the check binds
  a junit to a commit or to a log; the workflow does, by collecting, testing
  and logging in one job on one checkout.
- **A peak between two memory samples.** `tools/mem_peak.py` samples every
  0.5 s; a 100-ms spike is invisible, and its record carries the sample count
  so a reader can tell.

## Wrong before right, in this measurement

Each was caught by a control, a parser pinned to its producer's line, or by
reading a number against the file it came from -- not by inspection.

1. The first workflow was invalid YAML: a plain `run:` scalar containing
   `: `. GitHub does not fail a PR for that; it silently registers nothing.
   Parsing the file locally before the push found it.
2. The runner record's `pip list | grep` would not have matched
   `pytest-xdist`, and `pip` is absent from a `uv` venv; the step now reads
   versions through `importlib.metadata`. Running the step locally found
   both.
3. The collection check counted an xpassed test as passed, and so disagreed
   with pytest's own summary line on the first real fixture. The summary
   cross-check refused. pytest's `passed` excludes `xpassed`.
4. A hand-computed expectation in that tool's test said 10 passed where
   pytest says 9: the xpass counted twice, by hand.
5. One of `tools/mem_peak.py`'s own controls pinned 8..20 samples for a
   0.5-s child and FAILED with 7 in `tools-n8-r1` of the hosted run -- a
   wall-clock bound calibrated on an idle host, this document's own subject.
   It is now a ratio between two lifetimes, which load does not change.
6. One control demanded a peak from a child shorter than the sampling
   interval, which the instrument cannot see by construction; replaced by one
   that pins the blind spot as behaviour.
7. The first form of `xdist_run_table.py` read `cpu_count` from the wrong
   half of its line, and its union cell said only `FAIL` for runs in which
   every test had run. The pinned-line test caught the first; reading the
   real table caught the second.
8. The shard workflow's upload step named its artifacts from a matrix key
   that does not exist in that workflow, so all six are called `xdist--r<k>`.
   `gh run download` by name found nothing; each was fetched by id and bound
   to its shard by what it ran.
9. The first fill of this document quoted the spread from the wrong pair of
   runs (`-r2` against `-r3`, the two fast ones) and so said the slow runner
   was "0.8x slower" with CPU-seconds falling, and ranged the passing `-n 8`
   run's controls by a subtraction instead of by reading the three values.
   Reading the rendered text against `spread.md` and the deadline table
   caught both; the pair is now oriented fast-to-slow by the derivation and
   the range is read from the table. The range was wrong a second time before
   it was right: the pattern that reads it swept the two 180-s rows into the
   300-s control's range. It now reads the `| 300 s |` rows only.


## Appendix: how the runs were made

Both workflows are in this directory as they ran, `tools-xdist-once.yml` and
`tools-shards-once.yml`, each on its own throwaway branch and deleted with
it. The `tools` job of `rungs.yml` at `8ebfe4cd` is copied step for step.
Differences, all deliberate: no `needs: changes` gate; pytest-xdist
installed; the matrix; the recording flags on the pytest line; the two
wrappers; a fresh by-directory `--collect-only` saved after the run; the
records uploaded with `if: always()`.

The shard lists are the 241 files of the by-directory collection, assigned
greedily to the lighter shard by `n1-r2`'s summed per-test seconds: 120
files / 3233 tests / 1513 s and 121 / 3445 / 1513 s. Checked before the push
on another machine: the two lists collect 3233 + 3445 tests with no overlap,
and their union equals the by-directory 6678.

To rebuild this record:

```
gh run download 46155589951 -D artifacts        # 12 directories
gh run download 46162689576 -D artifacts        # 6 more: see the note on their names below
gh api repos/2AMLogic/gf180-parasynth/actions/runs/46155589951/jobs > jobs-xdist.json
gh api repos/2AMLogic/gf180-parasynth/actions/runs/46162689576/jobs > jobs-shards.json
python docs/ci-tools-xdist-measurement-2026-11-28/build_record.py . artifacts n1-r1
python docs/ci-tools-xdist-measurement-2026-11-28/derive.py artifacts derived jobs-xdist.json jobs-shards.json
```

The shard run's six artifacts are all named `xdist--r<k>`: that workflow's
upload step still read `matrix.candidate.label`, which its matrix does not
have. They are distinct artifacts with distinct ids, named here by the job
each came from; fetch each by id and unpack it into `xdist-<shard>-r<k>`:

| artifact id | unpack into |
|---|---|
| 15271318566 | `xdist-s0-r1` |
| 15271544552 | `xdist-s1-r1` |
| 15271686331 | `xdist-s0-r2` |
| 15271752071 | `xdist-s1-r2` |
| 15272335801 | `xdist-s0-r3` |
| 15271338235 | `xdist-s1-r3` |

(`gh api repos/2AMLogic/gf180-parasynth/actions/artifacts/<id>/zip > a.zip`.)
Which artifact is which shard was read from what it ran, not from its name:
the `s0` files hold 3233 tests and `s1` 3445.

`build_record.py` is reproduced in `build_record.py.txt` beside this file; it
is not run by anything.
