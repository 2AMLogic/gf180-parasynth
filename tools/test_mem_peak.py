#!/usr/bin/env python3
"""Controls for the process-tree memory wrapper. Its ONLY failure mode that
matters is a false green, and its one measurement is a SUM.

`tools/mem_peak.py` sits in front of `pytest tools/ -n N` for issue #1947,
where the question is how much memory several pytest workers and their child
processes hold AT ONCE. `tools/run_with_rss.py` reports `ru_maxrss`, the largest
single process in the tree, which is the right number for one process and
under-reads N of them by up to N-1. So the load-bearing controls here are: a
child that really segfaults still fails the step with 139, and a tree of two
processes that each hold 150 MB reads about 300, not about 150.

    python -m pytest tools/test_mem_peak.py -q
"""
from __future__ import annotations
import json
import pathlib
import subprocess
import sys

WRAPPER = str(pathlib.Path(__file__).resolve().parent / "mem_peak.py")

#: One page of a 150 MB buffer, touched, in a child. Untouched pages are not
#: resident, so `bytearray(150 << 20)` alone would read as almost nothing.
TOUCH_150MB = ("x = bytearray(150 << 20); "
               "x[::4096] = b'\\x01' * (len(x) // 4096); ")


def wrap(*cmd: str, interval: str | None = None, out: str | None = None):
    argv = [sys.executable, WRAPPER]
    if interval is not None:
        argv += ["--interval", interval]
    if out is not None:
        argv += ["--out", out]
    return subprocess.run([*argv, "--", *cmd], capture_output=True, text=True)


def peak_mb(stderr: str) -> int:
    line = [l for l in stderr.splitlines() if l.startswith("[mem] peak tree RSS")][0]
    return int(line.split()[4])


# ---- status propagation: the whole point -----------------------------------
def test_a_segfaulting_child_still_exits_139():
    """128+11 = 139, the code CI reported for issue #422. A memory wrapper that
    swallows it reports success while the suite dies."""
    r = wrap(sys.executable, "-c", "import faulthandler; faulthandler._sigsegv()")
    assert r.returncode == 139, (r.returncode, r.stderr[-500:])
    assert "SIGSEGV" in r.stderr and "CRASH" in r.stderr


def test_a_child_killed_by_sigkill_is_not_laundered_into_success():
    r = wrap(sys.executable, "-c",
             "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    assert r.returncode == 137, (r.returncode, r.stderr[-500:])


def test_a_passing_child_exits_zero_and_a_failing_one_keeps_its_code():
    assert wrap(sys.executable, "-c", "pass").returncode == 0
    for code in (1, 2, 3, 5, 42):
        r = wrap(sys.executable, "-c", f"raise SystemExit({code})")
        assert r.returncode == code, (code, r.returncode, r.stderr[-300:])


def test_an_unlaunchable_command_is_127_and_never_0():
    r = wrap("a-binary-that-does-not-exist-zzz")
    assert r.returncode == 127
    assert "REFUSED" in r.stderr


def test_no_command_refuses():
    r = subprocess.run([sys.executable, WRAPPER], capture_output=True, text=True)
    assert r.returncode == 127 and "REFUSED" in r.stderr
    r = subprocess.run([sys.executable, WRAPPER, "--"], capture_output=True, text=True)
    assert r.returncode == 127 and "REFUSED" in r.stderr


def test_an_unknown_option_refuses_rather_than_being_ignored():
    r = subprocess.run([sys.executable, WRAPPER, "--intervall", "1", "--",
                        sys.executable, "-c", "print('RAN')"],
                       capture_output=True, text=True)
    assert r.returncode == 127 and "REFUSED" in r.stderr
    assert "RAN" not in r.stdout


# ---- the child's output must reach the log untouched ------------------------
def test_child_stdout_and_stderr_are_not_swallowed():
    r = wrap(sys.executable, "-c",
             "import sys; print('CHILD-STDOUT'); print('CHILD-STDERR', file=sys.stderr)")
    assert "CHILD-STDOUT" in r.stdout
    assert "CHILD-STDERR" in r.stderr


def test_the_report_goes_to_stderr_so_stdout_stays_parseable():
    r = wrap(sys.executable, "-c", "print('ONLY-THIS')")
    assert r.stdout.strip() == "ONLY-THIS"
    assert "[mem]" in r.stderr


# ---- the number it exists to record: a SUM over the tree --------------------
def test_peak_tracks_what_one_child_touched():
    small = wrap(sys.executable, "-c", "pass", interval="0.02")
    big = wrap(sys.executable, "-c", TOUCH_150MB + "import time; time.sleep(0.3)",
               interval="0.02")
    assert peak_mb(big.stderr) >= 140, big.stderr
    # An upper bound as well: this reads THIS tree, so another process on a
    # shared host must not be able to inflate it.
    assert peak_mb(big.stderr) <= 200, big.stderr
    assert peak_mb(small.stderr) < 60, small.stderr


def test_the_peak_is_the_sum_across_a_tree_not_its_largest_process():
    """The control this file exists for: parent and grandchild each hold
    150 MB at the same time. A `ru_maxrss`-style instrument reads about 150."""
    parent = ("import subprocess, sys, time\n"
              f"{TOUCH_150MB}"
              "p = subprocess.Popen([sys.executable, '-c', "
              f"{TOUCH_150MB!r} + 'import time; time.sleep(0.6)'])\n"
              "time.sleep(0.6)\n"
              "p.wait()\n")
    r = wrap(sys.executable, "-c", parent, interval="0.02")
    assert r.returncode == 0, r.stderr[-500:]
    got = peak_mb(r.stderr)
    assert got >= 290, (got, r.stderr)
    assert got <= 400, (got, r.stderr)


def test_samples_scale_with_the_child_lifetime_and_do_not_spin():
    """0.5 s at a 0.05 s interval is about ten samples. Too few and the sampler
    slept through the run; many more and it is a busy loop stealing a core from
    the very thing it measures."""
    r = wrap(sys.executable, "-c", "import time; time.sleep(0.5)", interval="0.05")
    n = int([l for l in r.stderr.splitlines() if l.startswith("[mem] peak")][0]
            .split("samples ")[1].split()[0])
    assert 8 <= n <= 20, (n, r.stderr)


def test_a_peak_between_two_samples_is_not_claimed():
    """Stated blind spot, pinned as behaviour: a child that holds 150 MB for
    30 ms under a 2 s interval is invisible, and the record says so through
    its sample count rather than through a number that looks measured."""
    r = wrap(sys.executable, "-c", TOUCH_150MB + "import time; time.sleep(0.03)",
             interval="2")
    n = int([l for l in r.stderr.splitlines() if l.startswith("[mem] peak")][0]
            .split("samples ")[1].split()[0])
    assert n <= 2, (n, r.stderr)


def test_the_json_record_carries_the_number_and_the_sample_count(tmp_path):
    out = tmp_path / "mem.json"
    r = wrap(sys.executable, "-c",
             TOUCH_150MB + "import time; time.sleep(0.4)",
             interval="0.05", out=str(out))
    assert r.returncode == 0, r.stderr
    rec = json.loads(out.read_text())
    assert rec["peak_tree_rss_mb"] == peak_mb(r.stderr)
    assert rec["samples"] >= 8, rec
    assert rec["interval_s"] == 0.05
    assert rec["exit"] == 0
    assert rec["max_processes"] >= 1
    assert rec["wall_s"] >= 0.3


def test_the_json_exit_code_is_the_child_even_when_it_fails(tmp_path):
    out = tmp_path / "mem.json"
    r = wrap(sys.executable, "-c", "raise SystemExit(3)", out=str(out))
    assert r.returncode == 3
    assert json.loads(out.read_text())["exit"] == 3
