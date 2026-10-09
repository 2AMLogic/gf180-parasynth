#!/usr/bin/env python3
"""`synth_count.sh` must REFUSE rather than print a blank cell count.

    python3 -m pytest rtl-sketch/test_synth_count.py -q

#568. The old shell script discarded yosys's output and status, so an absent
yosys, a parse error or a changed `stat` format all printed
"ladder_dp, 16-entry tanh  :  cells" and exited 0 -- an unknown rendered in the
place where a result belongs. The controls here are the inputs that defeat it
(docs/verification-rules.md 8): an empty PATH, a yosys that exits 0 with no
stat line, and a yosys that exits non-zero. They drive the `.sh` entry point,
which is what README.md:279 documents, so they were red against the old script
and stay a gate on the shim. No real yosys is needed: PATH holds shims.
"""
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "synth_count.sh"
REFUSED_NO_YOSYS = 3
REFUSED_BAD_RUN = 4


def _bin(tmp_path, yosys_body=None):
    d = tmp_path / "bin"
    d.mkdir()
    (d / "python3").symlink_to(sys.executable)
    # The shim needs dirname; the red run against the OLD script also linked
    # grep awk tail rm so its failures were its own, not "grep: not found".
    for t in ("dirname",):
        (d / t).symlink_to(shutil.which(t))
    if yosys_body is not None:
        y = d / "yosys"
        y.write_text("#!/bin/sh\n" + yosys_body)
        y.chmod(y.stat().st_mode | stat.S_IXUSR)
    return d


def _run(path_dir):
    env = {"PATH": str(path_dir), "TMPDIR": str(path_dir)}
    return subprocess.run(["/bin/sh", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=60)


# A shim that honours `-l LOG` and writes a stat line like yosys's.
GOOD = ('while [ $# -gt 0 ]; do [ "$1" = -l ] && L=$2; shift; done\n'
        'echo "     1234 cells" > "$L"\n')


def test_empty_path_refuses(tmp_path):
    r = _run(_bin(tmp_path))
    assert r.returncode == REFUSED_NO_YOSYS, (r.returncode, r.stdout, r.stderr)
    assert "REFUSED" in r.stderr and "yosys" in r.stderr
    assert "cells" not in r.stdout


def test_exit0_without_stat_line_refuses(tmp_path):
    r = _run(_bin(tmp_path, "exit 0\n"))
    assert r.returncode == REFUSED_BAD_RUN, (r.returncode, r.stdout, r.stderr)
    assert "REFUSED" in r.stderr and "cells" in r.stderr


def test_nonzero_exit_refuses_even_with_stat_line(tmp_path):
    r = _run(_bin(tmp_path, GOOD + "exit 1\n"))
    assert r.returncode == REFUSED_BAD_RUN, (r.returncode, r.stdout, r.stderr)
    assert "REFUSED" in r.stderr


def test_good_shim_prints_three_rows(tmp_path):
    r = _run(_bin(tmp_path, GOOD))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "ladder_dp, 16-entry tanh  : 1234 cells",
        "ladder_dp, 256-entry tanh : 1234 cells",
        "modal_dp, 4 modes         : 1234 cells",
    ]


def test_picks_last_cells_line(tmp_path):
    body = ('while [ $# -gt 0 ]; do [ "$1" = -l ] && L=$2; shift; done\n'
            'printf "     9 cells\\n     77 cells\\n" > "$L"\n')
    r = _run(_bin(tmp_path, body))
    assert r.returncode == 0 and "77 cells" in r.stdout and "9 cells" not in r.stdout
