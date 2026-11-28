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

Two of these bounds were wall-clock counts until #2139 and each now says
whether it is a ratio or a calibration. `tools/mem_peak_load_probe.py` runs
this file beside a CPU hog and prints what the two of them observed.
"""
from __future__ import annotations
import json
import pathlib
import subprocess
import sys

import pytest

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


#: The tree control's grandchild, as source. The order is the point: `TOUCHED`
#: goes out only AFTER the pages are written, and the pages are then held.
TREE_GRANDCHILD = TOUCH_150MB + "print('TOUCHED', flush=True); import time; time.sleep(0.6)"


def tree_parent(grandchild: str, wait_s: float = 60.0) -> str:
    """Source for a parent that holds 150 MB of its own, will not exit before
    `grandchild` has, and REFUSES (exit 70) if `grandchild` never says TOUCHED
    within `wait_s` -- instead of going on to a tree the control could misread."""
    return ("import select, subprocess, sys\n"
            f"{TOUCH_150MB}"
            "p = subprocess.Popen([sys.executable, '-c', " + repr(grandchild) + "],\n"
            "                     stdout=subprocess.PIPE)\n"
            "ready, _, _ = select.select([p.stdout], [], [], " + repr(wait_s) + ")\n"
            "if not ready or p.stdout.readline() != b'TOUCHED\\n':\n"
            "    print('REFUSED: the grandchild never said TOUCHED', file=sys.stderr)\n"
            "    p.kill()\n"
            "    sys.exit(70)\n"
            "sys.exit(p.wait())\n")


def test_the_peak_is_the_sum_across_a_tree_not_its_largest_process():
    """The control this file exists for: parent and grandchild each hold
    150 MB at the same time. A `ru_maxrss`-style instrument reads about 150.

    Which bound is which (issue #2139):

      - `290 <= got <= 400` MB is a CALIBRATION on what was touched: two
        buffers of 150 MB with every page written. Load changes WHEN pages
        become resident, not WHICH, so it cannot move these. Measured with
        `tools/mem_peak_load_probe.py`: 320 in all 80 runs, beside a hog,
        pinned to one core, and neither.
      - the OVERLAP is a synchronisation, not a sleep. `TREE_GRANDCHILD` says
        `TOUCHED` only after writing its pages, the parent blocks on that for
        60 s, and it does not exit before the grandchild does. The first form
        slept 0.6 s in each process, which reads as though the sum needed the
        two sleeps to coincide. It never did -- `p.wait()` already had the
        overlap -- but nothing said so, and a grandchild that died before
        touching would have surfaced as a low number that looked measured.
      - one timing quantity is left, the grandchild's 0.6 s hold after
        `TOUCHED`: 30 sampling intervals at 0.02 s, so the sampler must stall
        30x to miss it. The worst stall observed is 0.18 samples per
        interval -- a 5.5x stall, which still puts about five samples inside
        the hold.
      - what satisfies this while violating its intent: a wrapper answering
        `2 * max(tree)` passes here, and is caught by the 200 bound of
        `test_peak_tracks_what_one_child_touched`, not by this test.
    """
    r = wrap(sys.executable, "-c", tree_parent(TREE_GRANDCHILD), interval="0.02")
    assert r.returncode == 0, r.stderr[-500:]
    got = peak_mb(r.stderr)
    n, wall = samples_and_wall(r.stderr)
    print(f"[obs] scenario=tree interval=0.02 samples={n} wall={wall} peak={got}")
    assert got >= 290, f"tree peak {got} MB, under two touched buffers"
    assert got <= 400, f"tree peak {got} MB, over two buffers and two interpreters"


def samples_and_wall(stderr: str) -> tuple[int, float]:
    line = [l for l in stderr.splitlines() if l.startswith("[mem] peak")][0]
    return int(line.split("samples ")[1].split()[0]), float(line.split("wall ")[1].rstrip("s"))


def test_samples_scale_with_the_child_lifetime_and_do_not_spin():
    """Sampling is periodic, and the proof is a RATIO, not a count: a child
    three times as long must draw about three times the samples, in whatever
    load the two share. The first form of this control pinned 8..20 samples
    for half a second and failed with 7 under `pytest tools/ -n 8` on a
    4-vCPU runner (docs/ci-tools-xdist-measurement-2026-11-28.md): a
    wall-clock bound calibrated on an idle host, which is the defect class
    that document records."""
    short = wrap(sys.executable, "-c", "import time; time.sleep(0.6)", interval="0.05")
    long = wrap(sys.executable, "-c", "import time; time.sleep(1.8)", interval="0.05")
    n_s, _ = samples_and_wall(short.stderr)
    n_l, w_l = samples_and_wall(long.stderr)
    assert n_s >= 3, (n_s, short.stderr)                 # some sampling happened
    assert n_l >= 2 * n_s, (n_s, n_l, long.stderr)       # and it tracks the lifetime
    assert n_l <= 1.25 * (w_l / 0.05), (n_l, long.stderr)  # but is not a busy loop


def test_a_peak_between_two_samples_is_not_claimed():
    """Stated blind spot, pinned as behaviour: a child that holds 150 MB for
    30 ms under a 2 s interval is invisible, and the record says so through
    its sample count rather than through a number that looks measured."""
    r = wrap(sys.executable, "-c", TOUCH_150MB + "import time; time.sleep(0.03)",
             interval="2")
    n, _ = samples_and_wall(r.stderr)
    assert n <= 2, (n, r.stderr)


def test_the_json_record_carries_the_number_and_the_sample_count(tmp_path):
    """The record on disk says what the log said, in both of its terms.

    Which bound is which (issue #2139):

      - `samples` equal to the log's count is EXACT, like the peak beside it:
        the record carries the count itself.
      - `samples >= 0.2 * wall_s / interval_s` is a RATIO between three fields
        of one record. It says the sampler's period stayed within 5x of its
        interval, which holds in whatever load stretches all three together.
        The first form said `>= 8` for a 0.4 s child. On a quiet 16-vCPU
        worker that is 8 or 9 -- the bound itself in 19 of 40 runs. With one
        hog and the whole run pinned to a core, `tools/mem_peak_load_probe.py`
        measured 6, 7 and 7 in 20 runs: all red, and the only reds. The ratio
        ran 0.80..0.90 quiet and 0.27 at the tail, so the 0.5 that #2139's
        curation suggested would have been red in 3 of the 60 pinned runs.
      - `wall_s >= 0.3` is a calibration on the child's own `sleep(0.4)`,
        which load can only lengthen.
      - what satisfies the ratio while violating its intent: a sampler that
        counted a fifth of the run and then stalled, and a `samples` written
        as `wall_s / interval_s` rather than counted. Both need a deliberately
        stalling sampler, which would mean editing `tools/mem_peak.py`'s loop,
        so they are stated here rather than pinned. The one-shot form IS
        pinned: `test_control_a_sampler_that_stops_after_one_sample_goes_red`.
    """
    out = tmp_path / "mem.json"
    r = wrap(sys.executable, "-c",
             TOUCH_150MB + "import time; time.sleep(0.4)",
             interval="0.05", out=str(out))
    assert r.returncode == 0, r.stderr
    rec = json.loads(out.read_text())
    n, _ = samples_and_wall(r.stderr)
    print(f"[obs] scenario=record interval={rec['interval_s']} samples={rec['samples']} "
          f"wall={rec['wall_s']} peak={rec['peak_tree_rss_mb']}")
    assert rec["peak_tree_rss_mb"] == peak_mb(r.stderr)
    assert rec["samples"] == n, (rec, r.stderr)
    periods = rec["wall_s"] / rec["interval_s"]
    assert rec["samples"] >= 0.2 * periods, (
        f"samples {rec['samples']} is under a fifth of the {periods:.0f} periods in the record")
    assert rec["interval_s"] == 0.05
    assert rec["exit"] == 0
    assert rec["max_processes"] >= 1
    assert rec["wall_s"] >= 0.3


def test_the_json_exit_code_is_the_child_even_when_it_fails(tmp_path):
    out = tmp_path / "mem.json"
    r = wrap(sys.executable, "-c", "raise SystemExit(3)", out=str(out))
    assert r.returncode == 3
    assert json.loads(out.read_text())["exit"] == 3


# ---- the load-robust bounds above, each shown going red or refusing (#2139) -
# These call the tests themselves with the input that defeats them, so what
# goes red is the shipped assertion and not a copy of it.
def test_control_a_sampler_that_stops_after_one_sample_goes_red(tmp_path, monkeypatch):
    """The record ratio's adversary. `p.wait(timeout=interval)` becomes
    `p.wait()` in a copy of the wrapper: one sample at t=0, then a plain wait
    for the child, so the WALL is unchanged and only the count collapses."""
    src = pathlib.Path(WRAPPER).read_text()
    assert src.count("p.wait(timeout=interval)") == 1, "the wrapper's loop moved; follow it"
    stub = tmp_path / "mem_peak_one_sample.py"
    stub.write_text(src.replace("p.wait(timeout=interval)", "p.wait()"))
    monkeypatch.setitem(globals(), "WRAPPER", str(stub))
    (tmp_path / "run").mkdir()
    with pytest.raises(AssertionError, match=r"samples 1 is under a fifth of the \d+ periods"):
        test_the_json_record_carries_the_number_and_the_sample_count(tmp_path / "run")


def test_control_a_grandchild_that_says_touched_without_touching_goes_red(monkeypatch):
    """The tree floor's adversary: `TOUCHED` with no 150 MB behind it. The
    handshake passes -- it is a synchronisation, not a memory check -- and the
    sum must then read ONE buffer, on the tree test's own lower bound."""
    monkeypatch.setitem(globals(), "TREE_GRANDCHILD", TREE_GRANDCHILD.replace(TOUCH_150MB, ""))
    with pytest.raises(AssertionError, match=r"tree peak \d+ MB, under two touched buffers"):
        test_the_peak_is_the_sum_across_a_tree_not_its_largest_process()


def test_control_a_grandchild_that_never_says_touched_is_refused():
    """The handshake's own adversary: pages touched, nothing said. The parent
    must exit 70 with REFUSED rather than hand the test a tree to misread."""
    silent = TOUCH_150MB + "import time; time.sleep(0.6)"
    r = wrap(sys.executable, "-c", tree_parent(silent, wait_s=1.0), interval="0.02")
    assert r.returncode == 70, (r.returncode, r.stderr[-500:])
    assert "REFUSED" in r.stderr
