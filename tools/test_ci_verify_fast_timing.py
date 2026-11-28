"""Offline tests for `tools/ci_verify_fast_timing.py`.

The script's one failure mode that matters is a false clean: reporting "no
timeouts, nothing to see" for a window it could not measure, or folding a
censored 600.0 s cap into a median as though the unit had finished in 600 s.
So the injected cases below are the point of the file. A NO-VERDICT in the
snapshot must come back red; an empty snapshot must come back REFUSED rather
than green; a censored unit must be excluded from every completed-only
statistic and counted separately.

Fixture lines are trimmed copies of real `gh run view <run> --attempt N --log`
output from PR #1683's runs 46112179003 (passed) and 46119792436 (attempt 1
NO-VERDICT, attempt 2 passed), with the `python` job's lines interleaved the
way the full-run log interleaves them.
"""
from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import asdict

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ci_verify_fast_timing import (                                # noqa: E402
    BATCH, REFERENCE, REFUSED, JobTiming, Refusal, UnitTiming, collect, main, n_files,
    parse_job_log, parse_summary_line, same_sha_groups, slug, spearman,
    summarise,
)

PASS_TABLE = [
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7860552Z PASS           275.7s  python3 -m pytest tools/test_run_case.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7860552Z               400 passed in 275.60s",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7868813Z PASS           450.0s  python3 -m pytest model/test_filter_rate_chain.py tools/test_rate_conv_2x.py tools/test_mono_m5a_score.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7876622Z PASS            77.7s  python3 -m pytest fpga/test_selected_preset.py fpga/test_build_selected.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7879987Z PASS             4.8s  python3 fpga/verify_live_midi.py --outdir build/live-midi-fast",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7887600Z PASS            71.9s  python3 tools/verify_mono_case.py",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7890041Z PASS             0.3s  python3 tools/check_workflows.py",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T05:35:24.7895936Z 12/12 passed",
]

TIMEOUT_TABLE = [
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7937909Z PASS           364.5s  python3 -m pytest tools/test_run_case.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7952032Z NO-VERDICT     600.0s  python3 -m pytest model/test_filter_rate_chain.py tools/test_rate_conv_2x.py tools/test_mono_m5a_score.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7960126Z               ........................................................................ [ 82%]",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7961068Z               ........................................................................ [ 96%]",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7964708Z PASS            98.4s  python3 -m pytest fpga/test_selected_preset.py fpga/test_build_selected.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7981395Z PASS            95.2s  python3 tools/verify_mono_case.py",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53.7996193Z 11/12 passed  --  NO-VERDICT python3 -m pytest model/test_filter_rate_cha",
]

FAIL_TABLE = [
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:01:40.4000000Z PASS           441.2s  python3 -m pytest model/test_filter_rate_chain.py tools/test_rate_conv_2x.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:01:40.4000000Z FAIL(1)        364.5s  python3 -m pytest tools/test_run_case.py -q",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:01:40.4000000Z FAIL(1)          0.1s  python3 tools/check_attested_hashes.py",
    "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:01:40.4000000Z 10/12 passed  --  FAIL python3 -m pytest tools/test_run_case.py; FAIL python3 tools/check_attested_hashes.py",
]

OTHER_JOB_LINES = [
    "python\tthe model suite\t2026-11-28T07:18:53.0000000Z PASS           999.9s  python3 -m pytest model/ spec/ -q",
    "changes\tSet up job\t2026-11-28T07:18:53.0000000Z PASS           999.9s  python3 -m pytest tools/test_run_case.py -q",
    "m5a-fast-x\tother\t2026-11-28T07:18:53.0000000Z PASS           999.9s  python3 -m pytest tools/test_run_case.py -q",
    "m5a-fast\tUNKNOWN STEP\t2026-11-28T07:18:53.4000000Z PASS             1.9s  python3 model/sound_report.py --check-locks",
]


# -- parse -------------------------------------------------------------------

@pytest.mark.parametrize("line,state,rc,secs,censored,n", [
    (PASS_TABLE[0], "PASS", None, 275.7, False, 1),
    (PASS_TABLE[2], "PASS", None, 450.0, False, 3),
    (PASS_TABLE[4], "PASS", None, 4.8, False, 1),
    (TIMEOUT_TABLE[1], "NO-VERDICT", None, 600.0, True, 3),
    (FAIL_TABLE[1], "FAIL", "1", 364.5, False, 1),
    (FAIL_TABLE[2], "FAIL", "1", 0.1, False, 1),
])
def test_each_state_tag_parses_with_its_status(line, state, rc, secs, censored, n):
    row = parse_summary_line(line.split("\t", 2)[2].split(" ", 1)[1])
    assert row is not None
    assert (row.state, row.rc, row.secs, row.censored, row.n_files) == (state, rc, secs, censored, n)


def test_tail_lines_and_the_passed_count_are_not_rows():
    for line in (PASS_TABLE[1], PASS_TABLE[7], TIMEOUT_TABLE[2], TIMEOUT_TABLE[6]):
        assert parse_summary_line(line.split("\t", 2)[2].split(" ", 1)[1]) is None


def test_ansi_escapes_do_not_hide_a_row():
    line = "\x1b[32m  PASS\x1b[0m           275.7s  python3 -m pytest tools/test_run_case.py -q"
    row = parse_summary_line(line)
    assert row is not None and row.state == "PASS" and row.slug == "test_run_case"


def test_parse_job_log_takes_only_the_named_job_in_order():
    rows = parse_job_log(OTHER_JOB_LINES[:2] + PASS_TABLE + OTHER_JOB_LINES[2:])
    assert [r.slug for r in rows] == ["test_run_case", BATCH, "test_selected_preset",
                                      "verify_live_midi", "verify_mono_case",
                                      "check_workflows", "sound_report"]


def test_a_summary_printed_twice_keeps_the_last_table():
    rows = parse_job_log(PASS_TABLE + PASS_TABLE)
    assert len(rows) == 6
    assert [r.slug for r in rows].count("test_run_case") == 1


def test_slug_and_file_count():
    assert slug("python3 -m pytest model/test_filter_rate_chain.py tools/test_rate_conv_2x.py -q") == BATCH
    assert slug("python3 tools/verify_mono_case.py") == "verify_mono_case"
    assert slug("python3 -m pytest model/test_audio_measure.py -q -k foldback") == "test_audio_measure"
    assert n_files("python3 -m pytest a/b.py c.py -q") == 2
    assert n_files("python3 tools/check_workflows.py") == 1


# -- summarise: the censoring discipline --------------------------------------

def _job(run_id, attempt, sha, rows, *, conclusion="success", branch="feature/x",
         started="2026-11-28T07:08:28Z", completed="2026-11-28T07:18:57Z",
         overlap=None, error=None):
    return JobTiming(run_id=run_id, attempt=attempt, event="pull_request", branch=branch,
                     sha=sha, created_at=started, conclusion=conclusion,
                     runner_name="GitHub Actions 1", started_at=started,
                     completed_at=completed, gate_secs=None, units=rows,
                     table=bool(rows), overlap_runs=overlap, error=error)


PASS_A = _job(1, 1, "aaaa1111", parse_job_log(PASS_TABLE))
TIMEOUT_B = _job(2, 1, "bbbb2222", parse_job_log(TIMEOUT_TABLE), conclusion="failure")
RERUN_B = _job(2, 2, "bbbb2222", parse_job_log(PASS_TABLE))
FAIL_C = _job(3, 1, "cccc3333", parse_job_log(FAIL_TABLE), conclusion="failure")
CANCELLED_D = _job(4, 1, "dddd4444", [], conclusion="cancelled")
ERRORED_E = _job(5, 1, "eeee5555", [], error="`gh run view` exited 1: boom")


def test_a_censored_unit_is_counted_apart_and_excluded_from_completed_stats():
    s = summarise([PASS_A, TIMEOUT_B, RERUN_B])
    u = s["units"][BATCH]
    assert u["n"] == 3 and u["n_censored"] == 1
    assert (u["min"], u["max"]) == (450.0, 450.0)       # the 600.0 cap is NOT a duration
    assert u["censored_at"] == [600.0]
    assert s["no_verdict_units"] == [(2, 1, BATCH, "bbbb2222", "feature/x")]


def test_a_fail_is_not_a_timeout():
    s = summarise([FAIL_C])
    assert s["no_verdict_units"] == []
    assert {(r[2], r[3]) for r in s["fail_units"]} == {("test_run_case", "1"), ("check_attested_hashes", "1")}


def test_ratio_is_a_lower_bound_when_the_focus_is_censored():
    s = summarise([TIMEOUT_B])
    r = s["ratio"]
    assert r["n"] == 0 and r["n_censored_lb"] == 1
    assert r["censored_lb"] == [pytest.approx(600.0 / 364.5)]


def test_ratio_is_absent_not_infinite_when_the_reference_is_missing():
    only_focus = _job(9, 1, "ffff9999", [UnitTiming(BATCH, "PASS", None, 450.0, False, 3)])
    s = summarise([only_focus])
    assert s["ratio"]["n"] == 0 and "median" not in s["ratio"]


def test_jobs_without_a_table_or_with_an_error_contribute_no_unit_seconds():
    skipped = _job(6, 1, "eeee6666", [], conclusion="skipped")
    s = summarise([PASS_A, CANCELLED_D, ERRORED_E, skipped])
    assert s["total"] == 4 and s["measured"] == 1
    assert s["no_table_cancelled"] == 1 and s["skipped"] == 1
    assert s["no_table_other"] == []
    assert s["errors"] == [(5, 1, "`gh run view` exited 1: boom")]
    assert s["units"]["test_run_case"]["n"] == 1


def test_empty_records_summarise_to_nothing_rather_than_to_zero_seconds():
    s = summarise([])
    assert s["units"] == {} and s["ratio"]["n"] == 0


# -- same-SHA pairs -----------------------------------------------------------

def test_same_sha_groups_pair_attempts_of_one_run_and_nothing_else():
    groups = same_sha_groups([PASS_A, TIMEOUT_B, RERUN_B, FAIL_C])
    assert len(groups) == 1
    g = groups[0]
    assert g["sha"] == "bbbb2222" and g["n"] == 2
    assert g["attempts"] == [1, 2]
    assert g["reference"]["values"] == [364.5, 275.7]
    assert g["reference"]["spread"] == pytest.approx(364.5 / 275.7)
    assert g["focus"]["n"] == 1 and g["focus"]["n_censored"] == 1   # 600.0 is not a value


def test_a_group_needs_two_completed_reference_values():
    """One record whose reference unit itself was censored leaves only one
    completed reference value: no pair, so no group."""
    half = _job(7, 1, "bbbb2222", [UnitTiming(REFERENCE, "NO-VERDICT", None, 600.0, True, 1),
                                   UnitTiming(BATCH, "PASS", None, 450.0, False, 3)],
                conclusion="failure")
    assert same_sha_groups([TIMEOUT_B, half, CANCELLED_D]) == []


# -- spearman -----------------------------------------------------------------

def test_spearman_on_a_known_example():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None                 # no variance: refuse, not 0.0


# -- main: offline verdicts ----------------------------------------------------

def _snapshot(tmp_path, records):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps([asdict(r) for r in records]))
    return p


def test_offline_clean_window_exits_zero(tmp_path, capsys):
    rc = main(["--offline", str(_snapshot(tmp_path, [PASS_A, RERUN_B])), "--require-clean", "--window", "2"])
    assert rc == 0
    assert "PASS:" in capsys.readouterr().out


def test_offline_no_verdict_in_window_exits_one(tmp_path, capsys):
    rc = main(["--offline", str(_snapshot(tmp_path, [TIMEOUT_B, RERUN_B])), "--require-clean", "--window", "2"])
    assert rc == 1
    assert "NO-VERDICT" in capsys.readouterr().err


def test_offline_cancelled_job_in_window_exits_one(tmp_path):
    rc = main(["--offline", str(_snapshot(tmp_path, [CANCELLED_D, RERUN_B])), "--require-clean", "--window", "2"])
    assert rc == 1


def test_offline_empty_snapshot_is_refused_not_clean(tmp_path, capsys):
    rc = main(["--offline", str(_snapshot(tmp_path, [])), "--require-clean", "--window", "1"])
    assert rc == REFUSED
    assert "REFUSED" in capsys.readouterr().err


def test_offline_window_larger_than_the_records_is_refused(tmp_path):
    rc = main(["--offline", str(_snapshot(tmp_path, [PASS_A])), "--require-clean", "--window", "5"])
    assert rc == REFUSED


def test_offline_garbage_is_refused(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert main(["--offline", str(p)]) == REFUSED
    assert "REFUSED" in capsys.readouterr().err


def test_unknown_focus_slug_is_refused_rather_than_silently_unmeasured(tmp_path):
    rc = main(["--offline", str(_snapshot(tmp_path, [PASS_A])), "--focus", "no_such_unit"])
    assert rc == REFUSED


# -- collect: the apparatus preconditions --------------------------------------

def _jobs_payload(job_id, attempt, conclusion, started, completed):
    return {"jobs": [{
        "id": job_id, "run_id": 1, "run_attempt": attempt, "name": "m5a-fast",
        "conclusion": conclusion, "runner_name": "GitHub Actions 42",
        "created_at": "2026-11-28T07:08:16Z", "started_at": started, "completed_at": completed,
        "steps": [{"name": "focused sound and selected-path gate", "conclusion": conclusion,
                   "started_at": started, "completed_at": completed}],
    }]}


RUNS = {"workflow_runs": [
    {"id": 1, "run_attempt": 2, "status": "completed", "conclusion": "success",
     "event": "pull_request", "head_sha": "bbbb2222bbbb", "head_branch": "feature/x",
     "created_at": "2026-11-28T07:08:12Z", "updated_at": "2026-11-28T08:05:17Z"},
    {"id": 2, "run_attempt": 1, "status": "in_progress", "conclusion": "",
     "event": "push", "head_sha": "zzzz0000zzzz", "head_branch": "main",
     "created_at": "2026-11-28T07:50:00Z", "updated_at": "2026-11-28T07:59:00Z"},
]}


def test_collect_fetches_each_attempt_by_number_and_never_by_job_id(monkeypatch):
    """The control for the defect met on #1730: `gh run view --job <id> --log`
    served attempt 2's log for attempt 1's job id, turning a red run green
    with no error. The collector must address the attempt explicitly."""
    import ci_verify_fast_timing as m
    seen = []

    def fake_gh(args, cwd):
        seen.append(args)
        if args[:2] == ["api", "repos/o/r/actions/workflows/rungs.yml/runs?per_page=100"]:
            return json.dumps(RUNS)
        if args[0] == "api" and args[1].endswith("/attempts/1/jobs"):
            return json.dumps(_jobs_payload(11, 1, "failure", "2026-11-28T07:08:28Z", "2026-11-28T07:18:57Z"))
        if args[0] == "api" and args[1].endswith("/attempts/2/jobs"):
            return json.dumps(_jobs_payload(22, 2, "success", "2026-11-28T07:57:21Z", "2026-11-28T08:05:17Z"))
        if args[:2] == ["run", "view"] and args[3:5] == ["--attempt", "1"]:
            return "\n".join(TIMEOUT_TABLE) + "\n"
        if args[:2] == ["run", "view"] and args[3:5] == ["--attempt", "2"]:
            return "\n".join(PASS_TABLE) + "\n"
        raise AssertionError(f"unexpected gh call {args}")

    monkeypatch.setattr(m, "_gh", fake_gh)
    records = collect("o/r", "rungs.yml", "m5a-fast", 100, pathlib.Path("."))
    assert [(r.run_id, r.attempt, r.conclusion, r.sha) for r in records] == [
        (1, 1, "failure", "bbbb2222"), (1, 2, "success", "bbbb2222")]     # in_progress run skipped
    assert records[0].units[1].censored is True                           # attempt 1 really is the red one
    assert records[1].units[1].censored is False
    assert records[0].job_id == 11 and records[1].job_id == 22
    assert records[0].gate_secs == pytest.approx(629.0)
    views = [c for c in seen if c[:2] == ["run", "view"]]
    assert [c[2:5] for c in views] == [["1", "--attempt", "1"], ["1", "--attempt", "2"]]
    for call in seen:
        assert "--job" not in call, call                                  # never the attempt-swapping path
    assert seen[1][1].endswith("/actions/runs/1/attempts/1/jobs")
    assert seen[3][1].endswith("/actions/runs/1/attempts/2/jobs")


def test_collect_records_a_job_cancelled_before_the_table_as_no_table(monkeypatch):
    import ci_verify_fast_timing as m
    cancelled = _jobs_payload(33, 1, "cancelled", "2026-11-28T07:08:28Z", "2026-11-28T07:23:28Z")
    cancelled["jobs"][0]["steps"][0]["completed_at"] = None

    def fake_gh(args, cwd):
        if args[0] == "api" and "runs?per_page" in args[1]:
            return json.dumps({"workflow_runs": [dict(RUNS["workflow_runs"][0], run_attempt=1)]})
        if args[0] == "api":
            return json.dumps(cancelled)
        return "m5a-fast\tfocused sound and selected-path gate\t2026-11-28T07:18:53Z [50%]\n"

    monkeypatch.setattr(m, "_gh", fake_gh)
    [rec] = collect("o/r", "rungs.yml", "m5a-fast", 5, pathlib.Path("."))
    assert rec.table is False and rec.units == [] and rec.conclusion == "cancelled"
    assert rec.gate_secs is None                         # never started its end: no zero-second step


def test_collect_refuses_when_the_window_is_empty(monkeypatch):
    import ci_verify_fast_timing as m
    monkeypatch.setattr(m, "_gh", lambda a, c: json.dumps({"workflow_runs": []}))
    with pytest.raises(Refusal):
        collect("o/r", "rungs.yml", "m5a-fast", 5, pathlib.Path("."))


def test_collect_keeps_going_past_one_broken_log_but_refuses_when_too_many_break(monkeypatch):
    import ci_verify_fast_timing as m

    def fake_gh(args, cwd):
        if args[0] == "api" and "runs?per_page" in args[1]:
            return json.dumps({"workflow_runs": [dict(RUNS["workflow_runs"][0], id=i, run_attempt=1) for i in range(5)]})
        if args[0] == "api":
            return json.dumps(_jobs_payload(1, 1, "success", "2026-11-28T07:08:28Z", "2026-11-28T07:18:57Z"))
        if args[2] == "2":
            raise m.Refusal("`gh run view` exited 1: HTTP 401: Bad credentials")
        return "\n".join(PASS_TABLE) + "\n"

    monkeypatch.setattr(m, "_gh", fake_gh)
    records = collect("o/r", "rungs.yml", "m5a-fast", 5, pathlib.Path("."), max_error_frac=0.5)
    assert [r.run_id for r in records if r.error] == [2]
    assert len([r for r in records if r.table]) == 4
    with pytest.raises(Refusal, match="1 of 5"):
        collect("o/r", "rungs.yml", "m5a-fast", 5, pathlib.Path("."), max_error_frac=0.1)


def test_the_real_gh_wrapper_reports_gh_absence_as_a_refusal(monkeypatch):
    """Exercise `_gh` itself: `subprocess.run` raising FileNotFoundError (no
    `gh` on PATH) must surface as Refusal, never as a traceback or a zero."""
    import ci_verify_fast_timing as m

    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory: 'gh'")

    monkeypatch.setattr(m.subprocess, "run", boom)
    with pytest.raises(Refusal, match="gh"):
        m._gh(["api", "x"], pathlib.Path("."))
