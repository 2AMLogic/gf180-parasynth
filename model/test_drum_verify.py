#!/usr/bin/env python3
"""`model/drum_verify.py`'s `--json`, checked before anything is measured (#2097).

THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): `main` wrote its metrics
with `open(a.json, "w")` after every voice had been read and measured, and
nothing made the directory. With `--json` under a missing directory that is a
`FileNotFoundError` out of `main` -- exit 1 and a traceback, which is what a
caller reads as a failed comparison, and the table's record is lost.

WHAT RUNS FOR REAL: `main`, `read_wav`, `segment_hits` and `measure` on a
synthetic bass-drum pair written here (an 80 Hz sine with an exponential decay,
so the peak the table reports is known: 80 Hz), and the JSON dump. Nothing is
doubled except where a test says the measurement must NOT run.

The input that defeats a `mkdir` guard is carried on purpose: a symlink at the
destination whose target directory is gone. Its parent exists and it is not a
directory, so the pre-flight passes and only the write fails -- for every user,
root included, where a read-only directory would not.

    .venv/bin/python -m pytest model/test_drum_verify.py -q
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import drum_verify as dv                                # first: must import on its own

SR_REF, SR_OURS = 44100, 48000                          # the set's rate, our render's
F0 = 80.0                                               # the sine every fixture carries


# ----------------------------------------------------------------- fixtures
def _decaying(sr: int, f0: float) -> np.ndarray:
    t = np.arange(sr // 2) / sr
    return np.round(0.5 * 32767 * np.sin(2 * np.pi * f0 * t) * np.exp(-t / 0.15)).astype(np.int16)


def _a_pair(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """The two directories the CLI compares, for the bass drum alone: one
    reference recording, and our solo render with its three scheduled hits."""
    refs, ours = tmp_path / "refs", tmp_path / "ours"
    (refs / "bd8").mkdir(parents=True)
    ours.mkdir()
    wavfile.write(str(refs / "bd8" / "BD5050.WAV"), SR_REF, _decaying(SR_REF, F0))
    solo = np.zeros(int(2.0 * SR_OURS), dtype=np.int16)
    hit = _decaying(SR_OURS, F0)
    for t0 in dv.HIT_TIMES:
        i0 = int(t0 * SR_OURS)
        solo[i0:i0 + len(hit)] = hit
    wavfile.write(str(ours / "00-solo-00-BD.wav"), SR_OURS, solo)
    return refs, ours


def _cli(refs, ours, tmp_path, json_path) -> list[str]:
    return ["--refs", str(refs), "--ours", str(ours), "--out", str(tmp_path / "plots"),
            "--no-plots", "--voices", "BD", "--json", str(json_path)]


def _run(monkeypatch, argv) -> int:
    """The CLI. `main` takes no argv and reads `sys.argv`, so that is what is set."""
    monkeypatch.setattr(sys, "argv", ["drum_verify.py", *argv])
    return dv.main()


def _not_a_file(tmp_path: pathlib.Path, kind: str) -> pathlib.Path:
    """A `--json` that can never be a file: a directory, or a path under one."""
    if kind == "a-directory":
        dest = tmp_path / "metrics.json"
        dest.mkdir()
    else:
        (tmp_path / "as-parent").write_text("not a directory", encoding="utf-8")
        dest = tmp_path / "as-parent" / "metrics.json"
    return dest


def _nothing_measured(*_a, **_k):
    raise AssertionError("a hit was measured before --json was checked")


# -------------------------------------------------------------------- the CLI
def test_the_json_is_written_under_a_directory_that_did_not_exist(tmp_path, monkeypatch, capsys):
    refs, ours = _a_pair(tmp_path)
    out = tmp_path / "reports" / "drums" / "metrics.json"
    assert _run(monkeypatch, _cli(refs, ours, tmp_path, out)) == 0
    capsys.readouterr()
    res = json.loads(out.read_text(encoding="utf-8"))
    # the measurement is real: the peak reads back as the frequency written in
    assert res["BD"]["ref"]["peak_hz"] == pytest.approx(F0, abs=1.0)
    assert len(res["BD"]["ours"]) == 3
    assert res["BD"]["ours"][0]["peak_hz"] == pytest.approx(F0, abs=1.0)


@pytest.mark.parametrize("kind", ["a-directory", "a-file-as-parent"])
def test_a_json_that_cannot_be_a_file_is_refused_before_anything_is_measured(
        tmp_path, monkeypatch, capsys, kind):
    refs, ours = _a_pair(tmp_path)
    dest = _not_a_file(tmp_path, kind)
    monkeypatch.setattr(dv, "measure", _nothing_measured)
    assert _run(monkeypatch, _cli(refs, ours, tmp_path, dest)) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and str(dest) in err, err
    assert not (tmp_path / "plots").exists()           # refused before any output was made
    if kind == "a-file-as-parent":
        assert (tmp_path / "as-parent").read_text(encoding="utf-8") == "not a directory"


def test_a_json_that_cannot_be_written_after_the_measurement_is_refused(tmp_path, monkeypatch, capsys):
    """The pre-flight passes -- the parent is there and the path is not a
    directory -- and the write fails anyway. The table stays printed; the exit
    is REFUSED (2), not the traceback exit 1 a reader takes for a FAIL."""
    refs, ours = _a_pair(tmp_path)
    dest = tmp_path / "metrics.json"
    dest.symlink_to(tmp_path / "gone" / dest.name)
    assert _run(monkeypatch, _cli(refs, ours, tmp_path, dest)) == 2
    out, err = capsys.readouterr()
    assert "BD    real" in out and "BD    ours" in out, out
    assert "REFUSED" in err and str(dest) in err, err
    assert not dest.exists()


def test_a_bare_filename_writes_into_the_working_directory(tmp_path, monkeypatch):
    """`--json out.json` has no parent to make: `Path("out.json").parent` is the
    working directory, and the guard must not refuse or stray from it."""
    refs, ours = _a_pair(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _run(monkeypatch, _cli(refs, ours, tmp_path, "out.json")) == 0
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["BD"]["ref"]["peak_hz"] \
        == pytest.approx(F0, abs=1.0)
