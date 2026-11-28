"""verify_live_midi's `--control` record: the output path (#2096, a slice of
the #2095 census from #2040).

THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): `--control X --json
NEWDIR/c.json` ran the control and then died at
`(a.json or a.outdir / "verification.json").write_text(...)` on a
FileNotFoundError -- a traceback and exit 1, FAIL's code, for a control that
had been caught and whose record was lost. Under `--expect-fail` that exit 1
is the code of a MISSED control. The default record under `--outdir` was never
exposed: `_run` makes that directory first.

THE CONTRACT, the #2040 pattern: --json is checked BEFORE the control runs (a
directory given as the file, or a parent that cannot be made, is REFUSED, 2,
with nothing run); a missing parent is created; and an OSError at the write is
REFUSED (2), never a traceback and never the 0 of a caught control.

WHAT IS DOUBLED: `run_control` (a whole simulated session per control). Real:
`main`, `_run` around it, the write and the exit mapping.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

import verify_live_midi as vlm

#: what a CAUGHT control looks like: the injected defect failed the session.
CONTROL = {"control": "DROP_NOTE_OFF", "scenario": "coverage", "caught": True,
           "verdict": "FAIL", "must_move": ["voice_gate"], "matrix": {"voice_gate": "MOVED"}}

READ_ONLY = pytest.param("read-only", marks=pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only directory, so nothing refuses"))


def _double_the_control(monkeypatch, calls, *, then=None):
    def run_control(name):
        calls.append(name)
        if then:
            then()
        return dict(CONTROL)

    monkeypatch.setattr(vlm, "run_control", run_control)


def _lose_the_directory(parent, how):
    """Between the pre-flight and the write: the directory is removed, or made
    read-only -- the input that passes `mkdir(exist_ok=True)` (rule 8)."""
    if how == "removed":
        shutil.rmtree(parent, ignore_errors=True)
    else:
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o500)


def _argv(tmp_path, rec):
    return ["--control", "DROP_NOTE_OFF", "--outdir", str(tmp_path / "out"),
            "--json", str(rec), "--expect-fail"]


def _an_unwritable_json(tmp_path, how):
    if how == "directory":
        return tmp_path
    if how == "file-in-the-way":
        (tmp_path / "file").write_text("not a directory")
        return tmp_path / "file" / "control.json"
    (tmp_path / "read-only").mkdir()
    (tmp_path / "read-only").chmod(0o500)
    return tmp_path / "read-only" / "new" / "control.json"


def test_control_json_under_a_missing_directory_is_created_and_the_verdict_kept(
        tmp_path, monkeypatch, capsys):
    calls = []
    _double_the_control(monkeypatch, calls)
    rec = tmp_path / "new" / "dir" / "control.json"
    assert vlm.main(_argv(tmp_path, rec)) == 0
    assert json.loads(rec.read_text())["control"] == CONTROL
    assert calls == ["DROP_NOTE_OFF"]
    assert "live-midi control DROP_NOTE_OFF: CAUGHT" in capsys.readouterr().out


def test_the_control_record_still_lands_under_outdir_without_json(tmp_path, monkeypatch):
    """Green before and after the fix: `_run` makes `--outdir` itself, so the
    branch `a.json` replaces is pinned, not repaired."""
    _double_the_control(monkeypatch, [])
    assert vlm.main(["--control", "DROP_NOTE_OFF", "--outdir", str(tmp_path / "out"),
                     "--expect-fail"]) == 0
    assert (tmp_path / "out" / "verification.json").is_file()


@pytest.mark.parametrize("how", ["directory", "file-in-the-way", READ_ONLY],
                         ids=["directory-given-as-the-file", "parent-uncreatable", "parent-read-only"])
def test_an_unwritable_json_is_refused_before_the_control_runs(tmp_path, monkeypatch, capsys, how):
    rec = _an_unwritable_json(tmp_path, how)
    calls = []
    _double_the_control(monkeypatch, calls)
    try:
        code = vlm.main(_argv(tmp_path, rec))
        out = capsys.readouterr().out
    finally:
        if how == "read-only":
            (tmp_path / "read-only").chmod(0o700)
    assert code == 2, out
    assert "live-midi: REFUSED --" in out, out
    assert calls == []


@pytest.mark.parametrize("how", ["removed", READ_ONLY], ids=["directory-removed", "parent-read-only"])
def test_a_write_failure_after_the_control_is_refused_not_a_traceback(
        tmp_path, monkeypatch, capsys, how):
    """The destination is lost between the pre-flight and the write: the real
    `write_text` then raises OSError. Under `--expect-fail` a caught control is
    worth 0, so a lost record must not read as one."""
    rec = tmp_path / "dir" / "control.json"
    calls = []
    _double_the_control(monkeypatch, calls, then=lambda: _lose_the_directory(rec.parent, how))
    try:
        code = vlm.main(_argv(tmp_path, rec))
        out = capsys.readouterr().out
    finally:
        if how == "read-only":
            rec.parent.chmod(0o700)
    assert code == 2
    assert "live-midi: REFUSED --" in out and str(rec) in out, out
    assert calls == ["DROP_NOTE_OFF"]
