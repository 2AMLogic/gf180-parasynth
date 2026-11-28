#!/usr/bin/env python3
"""`model/k_comp_sweep.py`'s `--json`, checked before anything is measured (#2097).

THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): `main` bisected the
onset at every cutoff (about three minutes, and still tens of seconds with
`--quick`) and only then wrote the rows with `open(a.json, "w")`. Under a
missing directory that is a `FileNotFoundError` out of `main`: exit 1 and a
traceback, after the whole sweep, with every row lost.

WHAT RUNS FOR REAL: `main` from `parse_args` to its return, the residual table
(`voice_fx.make_k_rom` / `kc_from_cut`) and the JSON dump. `measure` is the one
thing doubled -- it rings the fixed-point ladder hundreds of times -- by a row
of its own shape that records each cutoff it was asked for, so a test can say
the sweep ran, or that it never started.

    .venv/bin/python -m pytest model/test_k_comp_sweep.py -q
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import k_comp_sweep as ks                             # first: must import on its own


@pytest.fixture
def swept(monkeypatch) -> list[int]:
    """`measure` replaced by a row of its own shape; returns the cuts asked for."""
    cuts = []

    def measure(cut, iters=14, verbose=False):
        cuts.append(cut)
        return dict(cut=cut, g=1, k_lin=0.5, f_lin=float(cut), k_lo=0, k_hi=16384,
                    k_meas=0.5, f_meas=float(cut))
    monkeypatch.setattr(ks, "measure", measure)
    return cuts


def _not_a_file(tmp_path: pathlib.Path, kind: str) -> pathlib.Path:
    """A `--json` that can never be a file: a directory, or a path under one."""
    if kind == "a-directory":
        dest = tmp_path / "sweep.json"
        dest.mkdir()
    else:
        (tmp_path / "as-parent").write_text("not a directory", encoding="utf-8")
        dest = tmp_path / "as-parent" / "sweep.json"
    return dest


# -------------------------------------------------------------------- the CLI
def test_the_json_is_written_under_a_directory_that_did_not_exist(tmp_path, swept):
    out = tmp_path / "sweeps" / "ladder" / "onset.json"
    assert ks.main(["--quick", "--json", str(out)]) == 0
    assert swept == ks.QUICK                            # the sweep ran, to the end
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [r["cut"] for r in rows] == ks.QUICK


@pytest.mark.parametrize("kind", ["a-directory", "a-file-as-parent"])
def test_a_json_that_cannot_be_a_file_is_refused_before_anything_is_measured(
        tmp_path, swept, capsys, kind):
    dest = _not_a_file(tmp_path, kind)
    assert ks.main(["--quick", "--json", str(dest)]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and str(dest) in err, err
    assert swept == []                                  # nothing was bisected
    if kind == "a-file-as-parent":
        assert (tmp_path / "as-parent").read_text(encoding="utf-8") == "not a directory"


def test_a_json_that_cannot_be_written_after_the_sweep_is_refused(tmp_path, swept, capsys):
    """A symlink at the destination whose target directory is gone: the parent
    exists and the path is not a directory, so the pre-flight passes and only
    the write fails -- for every user, root included. The table stays printed
    and the exit is REFUSED (2), not the traceback exit 1."""
    dest = tmp_path / "sweep.json"
    dest.symlink_to(tmp_path / "gone" / dest.name)
    assert ks.main(["--quick", "--json", str(dest)]) == 2
    out, err = capsys.readouterr()
    assert swept == ks.QUICK                            # the sweep ran first
    assert "ONSET OF SELF-OSCILLATION" in out and "| worst |" in out, out
    assert "REFUSED" in err and str(dest) in err, err
    assert not dest.exists()


def test_a_bare_filename_writes_into_the_working_directory(tmp_path, swept, monkeypatch):
    """`--json out.json` has no parent to make: `Path("out.json").parent` is the
    working directory, and the guard must not refuse or stray from it."""
    monkeypatch.chdir(tmp_path)
    assert ks.main(["--quick", "--json", "out.json"]) == 0
    assert len(json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))) == len(ks.QUICK)
