"""Offline tests for `tools/ci_acceptance_timing.py`.

The script's one failure mode that matters is a false clean: reporting "no
cancellations" for a window it could not actually measure. So the injected
cases below are the point of the file -- a snapshot with a cancellation in it
must come back red, and a window that cannot be filled must come back REFUSED
rather than green.

Payload shapes are trimmed copies of real `gh api
repos/.../actions/runs/<id>/jobs` responses from the #264 investigation.
"""
from __future__ import annotations

import datetime as dt
import errno
import json
import os
import pathlib
import sys
from dataclasses import asdict

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_acceptance_timing as mod                               # noqa: E402
from ci_acceptance_timing import (                                # noqa: E402
    JobRecord, Refusal, REFUSED, main, parse_jobs, runner_class, summarise,
    timeout_cancellations,
)


def _run(run_id: int, event: str = "push", branch: str = "main") -> dict:
    return {"databaseId": run_id, "event": event, "headBranch": branch,
            "headSha": "0123456789abcdef", "createdAt": "2026-09-26T08:39:45Z",
            "status": "completed"}


_T0 = dt.datetime(2026, 9, 26, 8, 39, 45, tzinfo=dt.timezone.utc)


def _iso(offset_s: float) -> str:
    return (_T0 + dt.timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jobs(conclusion: str, runner: str, *, rest_s: int, props_s: int = 120,
          name: str = "acceptance") -> dict:
    """One `/jobs` payload with the two steps the #264 argument turned on.

    Offsets are computed from a single epoch rather than written out by hand:
    the first version of this helper formatted them with `//`/`%` arithmetic
    and produced 44s where it meant 440s, which is exactly the class of
    fixture bug that makes a timing test assert the wrong thing quietly.
    """
    return {"jobs": [{
        "name": name,
        "run_id": 1,
        "conclusion": conclusion,
        "runner_name": runner,
        "created_at": _iso(0),
        "started_at": _iso(3),
        "completed_at": _iso(3 + 1220),
        "steps": [
            {"name": "Set up job", "conclusion": "success",
             "started_at": _iso(3), "completed_at": _iso(4)},
            {"name": "acceptance properties", "conclusion": "success",
             "started_at": _iso(15), "completed_at": _iso(15 + props_s)},
            {"name": "the rest of the model suite", "conclusion": conclusion,
             "started_at": _iso(600), "completed_at": _iso(600 + rest_s)},
        ],
    }]}


# -- runner_class -----------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("blacksmith-01m3fes43q9sbc86127gwgrtdr-4vcpu", "blacksmith"),
    ("GitHub Actions 1000052299", "github-hosted"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_runner_class_buckets_the_two_classes_seen_in_264(name, expected):
    assert runner_class(name) == expected


def test_an_unrecognised_runner_is_reported_verbatim_not_guessed():
    """A third class must surface as itself. Folding it into one of the two
    known buckets is how a real difference disappears into a median."""
    assert runner_class("self-hosted-m4-mini") == "self-hosted-m4-mini"


# -- parse_jobs -------------------------------------------------------------

def test_parse_jobs_extracts_step_durations_and_queue_time():
    (rec,) = parse_jobs(_run(36230444886), _jobs("success", "GitHub Actions 1", rest_s=300),
                        "acceptance")
    assert rec.run_id == 36230444886
    assert rec.runner == "github-hosted"
    assert rec.queue_s == 3.0
    assert rec.steps["Set up job"] == 1.0
    assert rec.job_minutes == pytest.approx(1220 / 60, abs=0.01)   # past the 20m cap


def test_parse_jobs_ignores_other_jobs_in_the_same_run():
    payload = _jobs("success", "GitHub Actions 1", rest_s=300, name="lint")
    assert parse_jobs(_run(1), payload, "acceptance") == []


def test_a_step_with_no_end_timestamp_is_omitted_not_recorded_as_zero():
    """A still-running step has no `completed_at`. Recording it as 0s would
    read as 'instant' in every median downstream -- a fast number where the
    truthful answer is 'no measurement'."""
    payload = _jobs("cancelled", "GitHub Actions 1", rest_s=300)
    payload["jobs"][0]["steps"].append(
        {"name": "never started", "conclusion": None,
         "started_at": None, "completed_at": None})
    (rec,) = parse_jobs(_run(1), payload, "acceptance")
    assert "never started" not in rec.steps


# -- summarise --------------------------------------------------------------

def _records(spec: list[tuple[str, str, int]]) -> list[JobRecord]:
    out = []
    for i, (concl, runner, rest_s) in enumerate(spec):
        out.extend(parse_jobs(_run(1000 + i), _jobs(concl, runner, rest_s=rest_s),
                              "acceptance"))
    return out


def test_summarise_splits_cancellation_rate_by_runner_class():
    """The #264 shape: cancellations on one runner class and none on the other."""
    recs = _records([("cancelled", "GitHub Actions 1", 550),
                     ("success", "GitHub Actions 2", 440),
                     ("success", "blacksmith-a-4vcpu", 360),
                     ("success", "blacksmith-b-4vcpu", 370)])
    s = summarise(recs)
    assert s["total"] == 4
    assert s["by_runner"]["github-hosted"]["cancelled"] == 1
    assert s["by_runner"]["github-hosted"]["cancelled_pct"] == 50.0
    assert s["by_runner"]["blacksmith"]["cancelled"] == 0
    assert s["by_runner"]["blacksmith"]["cancelled_pct"] == 0.0


def test_cancelled_jobs_are_excluded_from_step_timing():
    """A cancelled job's last step was truncated by the cap. Folding it into
    the median biases the very number used to argue about the cap."""
    recs = _records([("cancelled", "GitHub Actions 1", 550),
                     ("success", "GitHub Actions 2", 440)])
    step = summarise(recs)["by_runner"]["github-hosted"]["steps"]["the rest of the model suite"]
    assert step["n"] == 1
    assert step["median"] == 440.0


# -- the window gate --------------------------------------------------------

def test_timeout_cancellations_only_looks_inside_the_window():
    recs = _records([("success", "blacksmith-a-4vcpu", 360)] * 3
                    + [("cancelled", "GitHub Actions 1", 550)])
    assert timeout_cancellations(recs, window=3) == []
    assert len(timeout_cancellations(recs, window=4)) == 1


def _snapshot(tmp_path, recs, name="snap.json"):
    p = tmp_path / name
    p.write_text(json.dumps([asdict(r) for r in recs]))
    return p


def test_a_clean_window_exits_zero(tmp_path, capsys):
    snap = _snapshot(tmp_path, _records([("success", "blacksmith-a-4vcpu", 360)] * 4))
    assert main(["--offline", str(snap), "--require-clean", "--window", "4"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_injected_cancellation_turns_the_gate_red(tmp_path):
    """Start red. One cancellation in the window must fail the gate -- if it
    does not, the green above proves nothing."""
    recs = _records([("cancelled", "GitHub Actions 1", 550)]
                    + [("success", "blacksmith-a-4vcpu", 360)] * 3)
    snap = _snapshot(tmp_path, recs)
    assert main(["--offline", str(snap), "--require-clean", "--window", "4"]) == 1


@pytest.mark.parametrize("window", ["0", "-1"])
def test_nonpositive_window_cannot_hide_a_cancellation(tmp_path, capsys, window):
    snap = _snapshot(tmp_path, _records([("cancelled", "GitHub Actions 1", 550)]))
    args = ["--offline", str(snap), "--require-clean", "--window"]
    assert main([*args, "1"]) == 1
    capsys.readouterr()
    assert main([*args, window]) == 2
    assert "positive" in capsys.readouterr().err


def test_a_window_too_short_to_fill_is_refused_not_passed(tmp_path, capsys):
    """The failure mode this file exists for: a short window must never read
    as a clean one. REFUSED (2) is distinct from both pass (0) and fail (1)."""
    snap = _snapshot(tmp_path, _records([("success", "blacksmith-a-4vcpu", 360)] * 3))
    assert main(["--offline", str(snap), "--require-clean", "--window", "40"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_filtering_to_an_absent_runner_class_is_refused_not_passed(tmp_path):
    """`--runner-class blacksmith` over a window with no Blacksmith jobs is an
    empty claim, not a clean one."""
    snap = _snapshot(tmp_path, _records([("success", "GitHub Actions 1", 440)] * 4))
    assert main(["--offline", str(snap), "--require-clean", "--window", "4",
                 "--runner-class", "blacksmith"]) == 2


def test_refusal_is_raised_when_gh_is_missing(monkeypatch):
    import ci_acceptance_timing as mod

    def _boom(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(mod.subprocess, "run", _boom)
    with pytest.raises(Refusal):
        mod._gh(["run", "list"], mod.pathlib.Path("."))


# ---------------------------------------------------------------------------
# the CLI's --snapshot destination (#2098, a slice of the #2095 class)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): `main` wrote
# `args.snapshot` with `write_text` inside the `try` that catches `Refusal`
# only -- after `collect()` had walked the live API and before a line of the
# report was printed -- and nothing made the parent directory. Under a
# missing one the write raised FileNotFoundError: exit 1 with a traceback,
# the same status `--require-clean` uses for a measured cancellation.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module under
# test: a destination that cannot be written is REFUSED (2) -- the status
# this script already returns through `Refusal` -- refused before `collect`
# runs where the path says so itself. A write that fails later is refused
# with the report printed and the gate's PASS/FAIL withheld: a window whose
# record is gone is neither clean nor unclean.
#
# WHAT RUNS FOR REAL: `main`, the offline load, `render`, `summarise` and the
# window gate. Doubled: `collect` (counted), which in production is the `gh`
# API walk. The late failure is made by a `write_text` that raises ENOSPC,
# because a full disk cannot be produced on demand.
# ---------------------------------------------------------------------------
def _full_disk(target):
    """`Path.write_text` that fails for `target` only."""
    real = pathlib.Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *args, **kwargs)

    return write_text


@pytest.fixture
def collected(monkeypatch):
    """`collect` double that counts its calls and returns nothing."""
    calls = []
    monkeypatch.setattr(mod, "collect", lambda *a, **k: calls.append(1) or [])
    return calls


@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory; name the record file"),
    ("{tmp}/a-file/snap.json", "no record can be written there: [Errno 17] File exists"),
    ("nul\x00dir/snap.json", "embedded null"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_snapshot_destination_is_refused_before_anything_is_collected(collected,
                                                                                   tmp_path, capsys,
                                                                                   target, reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    rc = main(["--snapshot", target.format(tmp=tmp_path)])
    out, err = capsys.readouterr()
    assert rc == REFUSED, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert collected == [], "the API was walked for a destination that could not be written"
    assert "job(s) measured" not in out, out


def test_a_snapshot_under_a_missing_directory_is_created_and_written(tmp_path, capsys):
    recs = _records([("success", "blacksmith-a-4vcpu", 360)] * 4)
    snap = _snapshot(tmp_path, recs, name="offline.json")
    target = tmp_path / "no-such-dir" / "snap.json"
    argv = ["--offline", str(snap), "--snapshot", str(target), "--require-clean", "--window", "4"]
    rc = main(argv)
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert "PASS: no cancellations in the last 4 jobs" in out, out
    assert [JobRecord(**r) for r in json.loads(target.read_text())] == recs


def test_a_snapshot_write_failure_is_refused_with_the_report_and_without_a_verdict(tmp_path, capsys,
                                                                                   monkeypatch):
    recs = _records([("success", "blacksmith-a-4vcpu", 360)] * 4)
    snap = _snapshot(tmp_path, recs, name="offline.json")      # the input; the target is another file
    target = tmp_path / "snap.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = main(["--offline", str(snap), "--snapshot", str(target), "--require-clean", "--window", "4"])
    out, err = capsys.readouterr()
    assert rc == REFUSED, (rc, out, err)
    assert "4 `acceptance` job(s) measured" in out, out          # the report is still printed
    assert "PASS: no cancellations" not in out and "FAIL:" not in out, out   # the gate's verdict is withheld
    assert "REFUSED" in err and "not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert not target.exists()


def test_a_snapshot_that_cannot_be_written_does_not_hide_a_later_refusal(tmp_path, capsys,
                                                                         monkeypatch):
    """Both reasons, one exit. The snapshot is attempted where it always was,
    before the runner-class filter, so a window refused afterwards still
    reports its own reason and the unwritten snapshot beside it."""
    recs = _records([("success", "GitHub Actions 1", 440)] * 4)
    snap = _snapshot(tmp_path, recs, name="offline.json")
    target = tmp_path / "snap.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = main(["--offline", str(snap), "--snapshot", str(target), "--require-clean", "--window", "4",
               "--runner-class", "blacksmith"])
    out, err = capsys.readouterr()
    assert rc == REFUSED, (rc, out, err)
    assert "no jobs on runner class 'blacksmith'" in err, err
    assert "not written" in err, err
    assert "job(s) measured" not in out, out


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_snapshot(tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    snap = _snapshot(tmp_path, _records([("success", "blacksmith-a-4vcpu", 360)] * 4))
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = main(["--offline", str(snap), "--snapshot", str(parent / "snap.json")])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == REFUSED, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert "4 `acceptance` job(s) measured" in out, out
    assert written == []
