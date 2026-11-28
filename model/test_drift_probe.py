#!/usr/bin/env python3
"""`model/drift_probe.py`'s validation cases: it must read the OLD tree.

    .venv/bin/python -m pytest model/test_drift_probe.py -q

The probe answers "which commit moved this measurement" by materialising each
commit with `git archive` and importing **that tree's** modules. Its one
failure mode that matters is a false attribution: importing HEAD's code, or
silently reusing an already-imported module, and reporting today's number
against a historical sha. That looks exactly like data and would blame the
wrong commit -- the same shape as every failure `docs/failure-modes.md`
records.

So the check here is tree isolation against a value that is *written down* in
each tree and differs between them: `sound_report.LOCKS`. At `28dfd55` the
LADDER corner-ratio-drift lock is 9.40462; after issue #242 it is 4.21473. A
probe that cannot tell those apart cannot tell any two commits apart.

This is deliberately cheap (an import, not a render). The expensive
validation -- that the probe's *measurements* are right -- is recorded in
`drift_probe.py`'s own docstring: re-measuring `28dfd55` reproduced both of
that commit's locked values to six figures, 9.4046 and 16.6250, which is an
answer written down eight months earlier by a different agent and not
available to the probe.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import drift_probe as dp                                            # noqa: E402

BASE = "28dfd55"
CORNER_LOCK = 'float(sr.LOCKS[("LADDER", "corner ratio drift")])'


def _have(rev: str) -> bool:
    r = subprocess.run(["git", "-C", dp.REPO, "cat-file", "-t", rev],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "commit"


requires_history = pytest.mark.skipif(
    not _have(BASE), reason=f"{BASE} not present in this checkout")


@requires_history
def test_the_probe_reads_the_archived_tree_not_the_working_tree():
    """The false-attribution guard. `28dfd55` records 9.40462 for this lock
    and today's tree records 2.74577 (re-locked in #242, #546); the probe must
    return the archived tree's number for the archived tree."""
    old = dp.measure_commit(BASE, {"lock": CORNER_LOCK}, timeout=120)
    assert not old.get("_import"), old
    assert old["lock"] == pytest.approx(9.40462, rel=1e-9), (
        f"the probe read {old['lock']} at {BASE}, where that tree's own LOCKS "
        "table says 9.40462 -- it is not importing the archived tree")

    import sound_report as sr                                       # noqa: E402
    here = dp._run_tree(dp.REPO, {"lock": CORNER_LOCK}, timeout=120)
    assert not here.get("_import"), here
    assert here["lock"] == pytest.approx(
        sr.LOCKS[("LADDER", "corner ratio drift")], rel=1e-9)
    assert here["lock"] != old["lock"], (
        "the probe returns the same value for two trees that disagree -- it "
        "cannot attribute a move to a commit")


@requires_history
def test_a_probe_that_cannot_run_refuses_instead_of_returning_a_number():
    """REFUSED is a first-class outcome. An expression that raises must come
    back as None with its reason attached, never as a plausible float."""
    got = dp.measure_commit(BASE, {"bad": 'sr.no_such_function()'}, timeout=120)
    assert got.get("bad") is None
    assert "AttributeError" in got.get("bad!why", "")


@requires_history
def test_the_walk_lists_the_commits_it_says_it_walks():
    rows = dp.commits_in(f"{BASE}~1..{BASE}", [])
    assert len(rows) == 1, rows
    assert rows[0][0].startswith(BASE[:7]), rows


def test_a_non_finite_probe_value_is_n_a_not_a_number(tmp_path, monkeypatch):
    """#1929. `_run_tree` json.loads the tree's own stdout, and the driver
    prints json.dumps(float(v)), which writes a bare NaN. Measured on the tree
    before the fix: NaN and inf came back as floats to be compared. They now
    come back as `n/a` with the reason, like any probe that could not answer."""
    (tmp_path / "model").mkdir()
    monkeypatch.setattr(dp, "DRIVER",
                        "import json\n"
                        "print(json.dumps({'a': float('nan'), 'b': float('inf'), "
                        "'c': 2.5, 'd': None}))\n")
    got = dp._run_tree(str(tmp_path), {"a": "x", "b": "x", "c": "x", "d": "x"}, 60)
    assert got["a"] is None and got["a!why"] == "non-finite answer: nan"
    assert got["b"] is None and got["b!why"] == "non-finite answer: inf"
    assert got["c"] == 2.5 and "c!why" not in got
    assert got["d"] is None and "d!why" not in got


def test_a_move_across_a_commit_that_answered_nan_is_marked_moved(monkeypatch, capsys):
    """CONTROL, through `main` and the real `_run_tree`. Three commits measure
    2.0, NaN, 2.5. On the tree before the fix the NaN was a float, and
    `abs(v - prev) > 1e-6` is False on both sides of one, so the 2.0 -> 2.5
    move printed no MOVED line at all: it was hidden by the commit in between.
    Needs no history: each commit's "tree" is an empty directory whose driver
    prints a canned answer."""
    answers = {"c1": 2.0, "c2": float("nan"), "c3": 2.5}

    def measure(commit, probes, timeout):
        tmp = tempfile.mkdtemp(prefix="drift-probe-test-")
        os.mkdir(os.path.join(tmp, "model"))
        monkeypatch.setattr(dp, "DRIVER", "import json\nprint(%r)\n"
                            % json.dumps({"p": answers[commit]}))
        try:
            return dp._run_tree(tmp, probes, timeout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    monkeypatch.setattr(dp, "PROBES", {"p": "x"})
    monkeypatch.setattr(dp, "commits_in", lambda rng, paths: [(h, h) for h in answers])
    monkeypatch.setattr(dp, "measure_commit", measure)
    assert dp.main(["--range", "c1..c3", "--jobs", "1"]) == 0
    out = capsys.readouterr().out
    assert "c2 " in out and "n/a" in out
    assert "** MOVED: p 2.0000 -> 2.5000" in out
