# The `tools` job's 42-84 minutes: where they went on 2026-11-28, and what one run cannot say

Issue #1816. The `tools` job of `.github/workflows/rungs.yml` runs
`python -m pytest tools/ -q` by directory, so every test file lands in it and
none is timed on its own. Its cap was 105 minutes. This file is the
measurement behind the cap now pinned in that job (150) and behind the two
follow-ups filed from it, #1944 and #1947.

It is also the record of what was **not** established. One run can show where
the time is concentrated. It cannot say why the same suite ran 42 minutes once
and 84 another, and it does not.

## The facts, none of them inferred

| | |
|---|---|
| measurement run | [46139990892](https://github.com/2AMLogic/gf180-parasynth/actions/runs/46139990892), job `tools`, `push`, head `13a184d7` |
| what `13a184d7` is | `ed00ea2c` (`origin/main` at the time) plus one file, `.github/workflows/tools-durations-once.yml`; the `tools/` tree is `3507b7fa` on both |
| what that workflow is | the `tools` job of `rungs.yml` copied step for step, without `needs: changes`, with `--durations=0 --junitxml` on the pytest line and the junit file uploaded (full text in the appendix) |
| runner | `GitHub Actions 1000675648`, label `ubuntu-24.04` |
| stack | Python 3.12.16, pytest 9.2.0, numpy 2.5.5, scipy 1.18.1, soundfile 0.14.0, pyyaml 6.0.4 (all `pip install --upgrade`, unpinned), iverilog 12.0-2build2 |
| whole job | 11:27:48Z to 12:41:08Z: 73 m 20 s |
| the pytest step | 72 m 48 s. Everything else is 33 s: checkout 6, pip 11, apt 10, upload 1 |
| pytest's own line | 6550 passed, 27 skipped, 2 xfailed in 4366.77 s (1:12:46) <!-- claim: grep="6550 passed, 27 skipped, 2 xfailed in 4366[.]77s" in=docs/ci-tools-job-durations-2026-11-28/durations.txt --> |
| the shipped job at the same SHA | run 46139409831 at `ed00ea2c`: 6550 passed, 27 skipped, 2 xfailed in 4469.68 s; whole job 75 m 03 s; success |
| completeness | 6579 junit testcases, which is 6550 + 27 + 2; 3828 of them have every phase under the 0.005 s print threshold and so no duration line; the worst per-test gap between junit and the block is 0.0100 s |

The shipped job at the same commit is the measurement's external check. The
same 6550 / 27 / 2 ran, and the instrumented run was the faster of the two, so
`--durations=0 --junitxml` cost nothing measurable.

## The two suspected causes, against the data

The issue named two candidates, "in the order I would check them".

| candidate, as the issue named it | how it is defined here | files | s | share | verdict |
|---|---|---:|---:|---:|---|
| subprocess-driving refusal controls that shell out to a real probe | the file PR #1773 added its three full-run controls to (`groups/probe-refusal-1773.txt`) | 1 | 282.4 | 6.5 % | **confirmed as a real cost**: 90.45 to 91.21 s per full run, not the 50-70 s assumed |
| the same, read by name | every `tools/test_*refus*.py` (`groups/refusal-named.txt`) | 20 | 659.0 | 15.2 % | |
| the same, read by behaviour | every test file under `tools/` that imports `subprocess` (`--group-grep`) | 60 | 1543.4 | 35.5 % | the largest identifiable class, and a third of the job |
| the five files that shell out to `iverilog` | the five `rungs.yml` names (`groups/iverilog-5.txt`) | 5 | 13.7 | 0.3 % | **refuted** |
| the same, read wider | every test file under `tools/` that mentions `iverilog` (`--group-grep`) | 10 | 19.3 | 0.4 % | refuted |

The three #1773 controls cost 90.45 to 91.21 s each here, where that PR
budgeted "about +180 s" for the three on the strength of 50-70 s per
invocation. Each is a full corpus pass, which is what makes it a control. The
number is recorded so the next such proposal budgets 91.

The iverilog files are not a cost centre, and `rungs.yml` now says so where it
names them.

## What was measured

Every number from here to "What one run cannot say" was produced by
`tools/pytest_durations_report.py` (its controls are in
`tools/test_pytest_durations_report.py`) from the committed record in
`docs/ci-tools-job-durations-2026-11-28/`:

- `durations.txt` is the block as pytest printed it, header through the summary line.
- `junit.xml` is the same run's junit, the oracle for the completeness check.
- `report.md` and `report.json` are the tool's whole output, every test and all 190 files.
- `groups/` holds the three explicit group lists.
- `main-runs.txt` is the Actions API table of whole-job times (next section).

To regenerate `report.md`:

```
python3 tools/pytest_durations_report.py \
  --durations docs/ci-tools-job-durations-2026-11-28/durations.txt \
  --junit docs/ci-tools-job-durations-2026-11-28/junit.xml \
  --group-file "iverilog-5=docs/ci-tools-job-durations-2026-11-28/groups/iverilog-5.txt" \
  --group-file "probe-refusal-1773=docs/ci-tools-job-durations-2026-11-28/groups/probe-refusal-1773.txt" \
  --group-file "refusal-named=docs/ci-tools-job-durations-2026-11-28/groups/refusal-named.txt" \
  --group-grep "iverilog-mentions=iverilog" \
  --group-grep "imports-subprocess=^(import subprocess|from subprocess import)" \
  --top 25 --top-files 190
```

Of 4366.77 s of pytest wall time, 4347.0 s (99.5 %) is attributable to named
tests. What is left is 20 s of collection, of the 16,824 phases under 5 ms
that are not printed, and of pytest's own reporting. There is no hidden
overhead to find: the job is its tests.

- files to reach 50 / 80 / 90% of listed time: 8 / 24 / 41 of 190
- tests to reach 50 / 80 / 90% of listed time: 41 / 150 / 321 of 2751
- top-5 files hold 38.1%; top-10 files 57.8%; top-25 tests 40.5%

93 of the 2751 timed tests take 10 s or more, and they hold 70 % of the time.
<!-- claim: grep="tests at 10 s or more: 93 of 2751 \(3\.4%\) hold 70\.0%" in=docs/ci-tools-job-durations-2026-11-28/report.md -->

### Tests by duration bracket

| bracket (s) | tests | of tests | s | share of listed |
|---|---:|---:|---:|---:|
| >=60 | 11 | 0.4% | 1193.6 | 27.5% |
| 30-60 | 17 | 0.6% | 658.5 | 15.1% |
| 10-30 | 65 | 2.4% | 1192.7 | 27.4% |
| 1-10 | 300 | 10.9% | 953.2 | 21.9% |
| <1 | 2358 | 85.7% | 349.0 | 8.0% |

### The 25 slowest tests

| # | s | share | call s | nodeid |
|---:|---:|---:|---:|---|
| 1 | 372.39 | 8.6% | 372.39 | `tools/test_r0_capture.py::test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught` |
| 2 | 91.21 | 2.1% | 91.21 | `tools/test_audio_distance_floor_refusal.py::test_full_run_on_an_unmodified_corpus_exits_0` |
| 3 | 90.83 | 2.1% | 90.83 | `tools/test_audio_distance_floor_refusal.py::test_full_run_with_mt50_cut_into_the_strike_exits_refused_2` |
| 4 | 90.45 | 2.1% | 90.45 | `tools/test_audio_distance_floor_refusal.py::test_full_run_with_bd5050_cut_into_the_strike_exits_refused_2` |
| 5 | 90.02 | 2.1% | 90.02 | `tools/test_cymbal_tone_render.py::test_the_hats_are_bit_exact_at_the_corners` |
| 6 | 89.11 | 2.0% | 89.11 | `tools/test_run_case.py::test_balance_trajectory_db_refuses_white_noise_at_both_operating_points` |
| 7 | 80.23 | 1.8% | 0.00 | `tools/test_cymbal_tone_render.py::test_the_clean_row_passes_every_decay_property` |
| 8 | 78.22 | 1.8% | 0.00 | `tools/probes/test_tom_distortion.py::test_every_clean_property_passes_so_the_matrix_is_not_vacuous` |
| 9 | 74.17 | 1.7% | 74.17 | `tools/test_performance_suite.py::test_synthtop_backend_full_suite_passes` |
| 10 | 69.51 | 1.6% | 69.51 | `tools/test_mutation_record_baselines.py::test_fpga_real_suite_kills_all_mutants_at_recorded_assertions` |
| 11 | 67.49 | 1.6% | 67.49 | `tools/test_pulse2x_headroom_refusal.py::test_normal_gain_in_probe_still_measures_and_writes_the_report` |
| 12 | 51.99 | 1.2% | 51.99 | `tools/test_cymbal_refusal_exit.py::test_cymbal_low_tail_full_run_records_prepare_refusal_not_traceback` |
| 13 | 51.44 | 1.2% | 51.44 | `tools/test_r0_capture.py::test_a_real_sound_defect_is_still_a_measured_fail_at_both_entry_points` |
| 14 | 45.79 | 1.1% | 45.79 | `tools/probes/dc_blocker.py::test_the_screen_brackets_the_rendered_attenuation_on_every_voice` |
| 15 | 45.02 | 1.0% | 45.02 | `tools/probes/dc_blocker.py::test_coupling_off_is_the_block_bit_for_bit` |
| 16 | 41.56 | 1.0% | 41.56 | `tools/test_stage_case.py::test_the_controls_command_refuses_every_injection` |
| 17 | 41.30 | 1.0% | 41.30 | `tools/test_mono_m5a_score.py::test_saw_cutoff_override_is_scored_on_complete_phrase_and_leaves_pulse_fixed` |
| 18 | 40.41 | 0.9% | 40.41 | `tools/test_pulse2x_headroom_refusal.py::test_silenced_pulse_gain_exits_refused_2_without_partial_report` |
| 19 | 38.62 | 0.9% | 38.62 | `tools/test_m1a_refusal_boundaries.py::test_unqualified_reference_manifest_exits_refused_2[analyse_m1a_phase_cycle.py-docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json]` |
| 20 | 38.57 | 0.9% | 38.57 | `tools/test_m1a_refusal_boundaries.py::test_non_refused_exception_is_not_swallowed_by_cli_boundary[analyse_m1a_phase_cycle.py-docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json]` |
| 21 | 36.88 | 0.8% | 36.88 | `tools/test_run_case.py::test_line_is_resolved_refuses_white_noise` |
| 22 | 36.53 | 0.8% | 36.53 | `tools/test_mono_wav_destination.py::test_the_unconditional_m5_write_is_a_control_that_stays_red` |
| 23 | 34.46 | 0.8% | 34.46 | `tools/probes/dc_blocker.py::test_the_coupled_switch_step_does_not_exceed_the_uncoupled_by_the_truncation_bound` |
| 24 | 34.42 | 0.8% | 34.42 | `tools/test_cymbal_tone_render.py::test_the_historical_saturation_is_caught` |
| 25 | 30.62 | 0.7% | 30.62 | `tools/test_pekonen_coloration_probe.py::test_every_injected_control_behaves` |

### Files

The first 20 of 190; `report.md` in the record has all of them.

| # | s | share | cum | tests | file |
|---:|---:|---:|---:|---:|---|
| 1 | 600.6 | 13.8% | 13.8% | 59 | `tools/test_r0_capture.py` |
| 2 | 285.2 | 6.6% | 20.4% | 26 | `tools/test_mono_wav_destination.py` |
| 3 | 282.4 | 6.5% | 26.9% | 23 | `tools/test_audio_distance_floor_refusal.py` |
| 4 | 256.4 | 5.9% | 32.8% | 61 | `tools/test_run_case.py` |
| 5 | 233.5 | 5.4% | 38.1% | 17 | `tools/test_cymbal_tone_render.py` |
| 6 | 209.3 | 4.8% | 43.0% | 34 | `tools/test_mutation_record_baselines.py` |
| 7 | 174.9 | 4.0% | 47.0% | 10 | `tools/probes/dc_blocker.py` |
| 8 | 169.0 | 3.9% | 50.9% | 44 | `tools/test_performance_suite.py` |
| 9 | 152.6 | 3.5% | 54.4% | 19 | `tools/test_pulse2x_headroom_refusal.py` |
| 10 | 149.5 | 3.4% | 57.8% | 21 | `tools/test_mono_m1a_score.py` |
| 11 | 131.6 | 3.0% | 60.8% | 30 | `tools/probes/test_tom_distortion.py` |
| 12 | 97.4 | 2.2% | 63.1% | 5 | `tools/test_analyse_m1a_phase_cycle.py` |
| 13 | 83.4 | 1.9% | 65.0% | 10 | `tools/test_stage_case.py` |
| 14 | 82.6 | 1.9% | 66.9% | 13 | `tools/test_m1a_refusal_boundaries.py` |
| 15 | 79.1 | 1.8% | 68.7% | 22 | `tools/test_mono_m5a_score.py` |
| 16 | 75.3 | 1.7% | 70.5% | 17 | `tools/test_r1_capture.py` |
| 17 | 64.2 | 1.5% | 71.9% | 12 | `tools/probes/test_rate_tol_sweep.py` |
| 18 | 62.6 | 1.4% | 73.4% | 143 | `tools/probes/test_tom_pitch_drop_fischer.py` |
| 19 | 62.4 | 1.4% | 74.8% | 24 | `tools/probes/test_tom_conga_gate.py` |
| 20 | 61.5 | 1.4% | 76.2% | 36 | `tools/probes/test_permitted_differences.py` |

Two of the heavy files spend their time in a fixture rather than in a test:
`tools/test_cymbal_tone_render.py` (80.2 s of setup) and
`tools/probes/test_tom_distortion.py` (78.2 s). That is why tests 7 and 8 of
the table above show 0.00 s of call against 80 s and 78 s of total. Fixtures
are 258 s in all, 5.9 %.

## What one run cannot say

**Why the same suite varies.** Twenty green whole-jobs on `main` on
2026-11-28, 07:24Z to 11:29Z, ran 42.1 to 84.4 min with a median of 71.8:

```
42.1 50.0 58.9 59.2 59.6 59.7 59.9 60.1 61.3 68.5 75.0 75.3 76.0 76.0 77.4 77.5 78.0 78.1 80.9 84.4
```

This measurement says nothing about the cause of that spread. The only
same-code pair it holds is two runs of `ed00ea2c`, 4366.77 s and 4469.68 s of
pytest, 2.4 % apart. Attributing the spread to runner variance would take
repeated runs of one revision; that is an acceptance criterion of #1947.

**What a test's time is made of.** The block gives one number per phase. Why
the slowest test takes 372 s is not in it; that is #1944.

**Whether the directory could run in less wall time on more workers.** Not
measured; #1947.

The committed table, re-derived from the Actions API:

```
  76.0 min  2026-11-28T07:24:31Z  a3e7b0ba  46121049691  GitHub Actions 1000672093
  50.0 min  2026-11-28T07:28:56Z  8913199b  46121382484  GitHub Actions 1000672148
  75.3 min  2026-11-28T07:34:35Z  b8d9f583  46121823983  GitHub Actions 1000672203
  77.5 min  2026-11-28T07:47:10Z  da867a30  46122770813  GitHub Actions 1000672387
  77.4 min  2026-11-28T07:53:39Z  edc09c72  46123253334  GitHub Actions 1000672500
  59.9 min  2026-11-28T08:01:07Z  f8feeb96  46123811492  GitHub Actions 1000672746
  59.6 min  2026-11-28T08:16:05Z  f7ba34f2  46124992762  GitHub Actions 1000672855
  61.3 min  2026-11-28T08:23:52Z  9b41b633  46125601412  GitHub Actions 1000673016
  60.1 min  2026-11-28T08:31:06Z  97a12f05  46126168800  GitHub Actions 1000673179
  68.5 min  2026-11-28T08:38:15Z  a56683c9  46126727944  GitHub Actions 1000673291
  76.0 min  2026-11-28T08:45:54Z  6328e156  46127307824  GitHub Actions 1000673375
  59.2 min  2026-11-28T08:55:49Z  3309dd59  46128060849  GitHub Actions 1000673466
  80.9 min  2026-11-28T09:36:04Z  ed1c4480  46131215416  GitHub Actions 1000674077
  78.0 min  2026-11-28T09:49:06Z  e26d7f29  46132214933  GitHub Actions 1000674255
  58.9 min  2026-11-28T09:57:03Z  6aa09d26  46132823173  GitHub Actions 1000674355
  42.1 min  2026-11-28T10:05:26Z  a3870ddb  46133478985  GitHub Actions 1000674401
  59.7 min  2026-11-28T10:12:38Z  02394528  46134058464  GitHub Actions 1000674502
  78.1 min  2026-11-28T10:21:08Z  1c56aaf6  46134750581  GitHub Actions 1000674637
  75.0 min  2026-11-28T11:20:35Z  ed00ea2c  46139409831  GitHub Actions 1000675559
  84.4 min  2026-11-28T11:29:23Z  474bd2ff  46140092199  GitHub Actions 1000675681
```

The snippet that produced it, run from the repository root. It reaches the
last 24 `rungs.yml` runs on `main` and keeps the green `tools` jobs, so a
later run reaches different rows; the file above is what it returned at about
12:55Z.

```python
import datetime as dt
import json
import subprocess


def gh(*a):
    return subprocess.run(["gh", *a], capture_output=True, text=True, check=True).stdout


def t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


runs = json.loads(gh("run", "list", "--workflow", "rungs.yml", "--branch", "main",
                     "--limit", "24", "--json", "databaseId"))
for r in runs:
    jobs = json.loads(gh("api", f"repos/{{owner}}/{{repo}}/actions/runs/{r['databaseId']}/jobs"))["jobs"]
    for j in jobs:
        if j["name"] == "tools" and j["conclusion"] == "success":
            mins = (t(j["completed_at"]) - t(j["started_at"])).total_seconds() / 60
            print(f"{mins:6.1f} min  {j['started_at']}  {j['head_sha'][:8]}  {r['databaseId']}  {j['runner_name']}")
```

## What was decided, and why

The cap is 150 minutes. The comment above the job justified 105 as about 1.75x the hosted maximum of its day. Applied to the 84.4 min maximum of 2026-11-28 the same rule gives 147.7, which rounds up to a multiple of five.
<!-- claim: grep="    timeout-minutes: 150" in=.github/workflows/rungs.yml -->

No cheaper response was available. The time is real test work spread over 41
files, so it will not shrink by itself, and the directory grows by
construction. The maximum moved from 80.9 to 84.4 min while it was being
measured: at 12:45Z it was 80.9 (run 46131215416, started 09:36Z); a run that
started two minutes after the measurement run (46140092199, `474bd2ff`) then
finished at 84.4.

What 150 does not fix. A genuinely hung run now costs 150 minutes of runner
before the red, not 105. And if the 42-84 min spread is a property of the
runners rather than of the code, 1.78x is not obviously enough. The repeated
runs that would tell are in #1947.

**No split here.** Whether the directory should run on more workers is #1947,
with the collection control its acceptance criteria require. The Curator's
reading of `tools/test_fpga_uncovered_workflow.py` stands: it pins the
`fpga-uncovered` job, so it would stay green if a `tools/` test fell out of a
shard.

## What the instrument is blind to

`tools/pytest_durations_report.py` refuses (exit 2) rather than reports on a malformed or truncated line inside the block, a second block, a duration line after the block ended, and an empty block, and each refusal has a control that goes red when its guard is disabled. Given `--junit`, it also refuses when a test with real time in the junit file has no line in the block, which is the only way a dropped line is visible at all.
<!-- claim: test=tools/test_pytest_durations_report.py::test_a_slow_test_missing_from_the_block_is_refused_by_name -->

Stated, because nothing checks them:

- Without `--junit` the report carries no completeness verdict. A complete-looking block with a test removed is indistinguishable from a run in which that test was fast, and the report says so in its output instead of implying completeness.
- The junit check reconciles two records of one run. A block and a junit file edited consistently would pass it.
- A row whose nodeid has trailing words parses as a nodeid with spaces, which parametrized ids legitimately have.
- `--group-grep` groups are text matches. A test can run a child process through a helper without importing `subprocess`, and a file can import it without spawning anything in the tests that ran. `imports-subprocess` is a proxy for "spawns", not a measurement of it.
- A `--group-grep` regex that matches nothing gives a group of 0.00 s, the number a genuinely free group gives. The report marks it (`NO FILES MATCHED`, `n_files` 0) and does not refuse it.

## Wrong before right, in this measurement

Each was caught by a control or by checking a typed number against the data,
not by reading:

1. The junit key split on every `::`, so a parametrization carrying a nodeid was mis-keyed. The completeness check refused the real run by name; `test_junit_key_does_not_split_on_a_double_colon_inside_the_brackets` now pins it.
2. `check_junit` was written before its tests and read the wrong object. Its first run failed on that.
3. Two hand-computed expectations in the fixture were wrong: one file, not two, reaches 90 %, and two tests, not three.
4. A summary said 93 tests hold 80 %. The three brackets sum to 70 %, and the sentence is now read from the tool's output.
5. A typed share of 5.2 % was 5.25 %.
6. `--group-grep` scanned only the top level of `tools/` and would have omitted `tools/probes/` without saying so.
7. The first cap written was 145, from a maximum of 80.9 min read at 12:45Z. A run that started two minutes after the measurement run finished at 84.4, and the cap is 150.

## Appendix: the workflow that ran the measurement

It lived only on branch `throwaway/tools-durations-1816`, which is deleted.
Any later re-measurement can recreate it from this text.

```yaml
# ONE-OFF measurement for issue 1816: where does the `tools` job's 60-80 min go?
# Lives only on branch throwaway/tools-durations-1816; never merged, deleted
# after the run. The `tools` job below is copied step for step from rungs.yml
# at ed00ea2c (origin/main, 2026-11-28). Differences, all deliberate:
#   * no `needs: changes` gate -- this measures the job, not what feeds it
#   * --durations=0 and --junitxml on the pytest line (shipped command unchanged)
#   * junit.xml uploaded as an artifact so per-file totals come from structured
#     data and can be cross-checked against the --durations=0 text
name: tools-durations-once
on:
  push:
    branches: [throwaway/tools-durations-1816]
permissions:
  contents: read
jobs:
  tools:
    runs-on: ubuntu-24.04
    timeout-minutes: 105
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: python -m pip install --upgrade pip numpy scipy pytest pyyaml soundfile
      - run: sudo apt-get update && sudo apt-get install -y iverilog
      - name: "every tools/ test, by directory -- durations measurement for issue 1816"
        run: python -m pytest tools/ -q --durations=0 --junitxml="$RUNNER_TEMP/junit.xml"
      - name: "upload junit.xml for the issue 1816 durations analysis"
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: tools-durations-junit
          path: ${{ runner.temp }}/junit.xml
          if-no-files-found: error
```
