"""`tools/dphase_floor_sweep.py` through its CLI: the `-o/--out` destination
(#2098, a slice of the #2095 class)."""
from __future__ import annotations

import errno
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools", ROOT / "model", ROOT / "audition"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import dphase_floor_sweep as ds  # noqa: E402

#: One floor of the grid, one stimulus of each panel: the shape `report`
#: reads, with values chosen here and not produced by the measurement.
RES = {"rows": [{"floor_ppm": 20.0, "rate_gap": 0.5, "sees_real": 7.0, "n_mech": 7.0}],
       "detail": [{"floor": 20.0, "label": "330Hz", "pj_ref": 1.0, "pj_render": 1.5,
                   "ds_ref": 2.0, "ar1_ref": 0.9, "ar1_render": 0.4, "refused": False}],
       "mdetail": [{"floor": 20.0, "label": "glide", "pj": 3.0, "ds": 4.0, "ar1": 0.1,
                    "refused": False}]}


@pytest.fixture
def measured(monkeypatch):
    """The real CLI with the sweep doubled: `measure` renders nothing and
    returns `RES`; `report` is the real one."""
    calls = []
    monkeypatch.setattr(ds, "measure", lambda *a, **k: calls.append(1) or RES)
    return calls


#: The grid's one row as `report` prints it, with whitespace collapsed: the
#: values are the claim, the column padding is not (a hand-counted first
#: version of this string was wrong by two spaces and red for that alone).
ROW = "floor_ppm rate_gap sees_real n_mech 20 0.5000 7 7"


def _flat(text):
    return " ".join(text.split())


def _full_disk(target):
    """`Path.open` that fails for `target` in write mode only."""
    real = pathlib.Path.open

    def open_(self, mode="r", *args, **kwargs):
        if self == target and "w" in mode:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, mode, *args, **kwargs)

    return open_


# ---------------------------------------------------------------------------
# the CLI's -o/--out destination (#2098)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): `main` opened
# `args.out` for writing after `measure()` had run the whole grid, and nothing
# made the parent directory. Under a missing one the `open` raised
# FileNotFoundError out of `main`: exit 1 with a traceback, and the report --
# which `-o` had redirected away from stdout -- was lost entirely.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module under
# test: a destination that cannot be written is REFUSED (2), refused before
# the grid runs where the path says so itself; refused after it otherwise,
# with the report then on stdout, because `-o` only redirects it.
#
# WHAT RUNS FOR REAL: `main` and `report`. Doubled: `measure` (counted).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory; name the report file"),
    ("{tmp}/a-file/sweep.txt", "no report can be written there: [Errno 17] File exists"),
    ("nul\x00dir/sweep.txt", "no report can be written there: embedded null byte"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_out_destination_is_refused_before_the_grid_runs(measured, tmp_path, capsys,
                                                                       target, reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    rc = ds.main(["-o", target.format(tmp=tmp_path)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert measured == [], "the grid ran for a destination that could not be written"
    assert "floor_ppm" not in out, out


def test_a_report_under_a_missing_directory_is_created_and_written(measured, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "sweep.txt"
    rc = ds.main(["-o", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert measured == [1]
    text = target.read_text()
    assert ROW in _flat(text), text
    assert f"wrote {target}" in out and "floor_ppm" not in out, out


def test_a_write_failure_after_the_sweep_is_refused_with_the_report_on_stdout(measured, tmp_path,
                                                                              capsys,
                                                                              monkeypatch):
    target = tmp_path / "sweep.txt"
    monkeypatch.setattr(pathlib.Path, "open", _full_disk(target))
    rc = ds.main(["-o", str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert ROW in _flat(out), out
    assert "REFUSED" in err and "not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert "wrote" not in out and not target.exists(), out


def test_without_an_out_the_report_goes_to_stdout_and_nothing_is_made(measured, tmp_path, capsys,
                                                                      monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = ds.main([])
    out, err = capsys.readouterr()
    assert rc == 0 and measured == [1], (rc, out, err)
    assert ROW in _flat(out), out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_report(measured, tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = ds.main(["-o", str(parent / "sweep.txt")])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert ROW in _flat(out), out
    assert written == []
