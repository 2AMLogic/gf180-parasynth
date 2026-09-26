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
import json
import os
import sys
from dataclasses import asdict

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ci_acceptance_timing import (                                # noqa: E402
    JobRecord, Refusal, main, parse_jobs, runner_class, summarise,
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
