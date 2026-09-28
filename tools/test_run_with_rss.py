#!/usr/bin/env python3
"""Controls for the RSS wrapper. Its ONLY failure mode that matters is a false
green.

`tools/run_with_rss.py` is about to sit in front of the one CI step that has
already died of SIGSEGV once (issue #422). If it ever swallows the child's
status, the job reports success while the suite dies at 30 % -- which is exactly
the state the issue was filed to make visible. So the load-bearing test here is
not "it prints an RSS number", it is **a child that really segfaults still fails
the step, with 139**.

    python -m pytest tools/test_run_with_rss.py -q
"""
from __future__ import annotations
import pathlib, subprocess, sys

WRAPPER = str(pathlib.Path(__file__).resolve().parent / "run_with_rss.py")

THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def wrap(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, WRAPPER, "--", *cmd],
                          capture_output=True, text=True)


# ---- status propagation: the whole point -----------------------------------
def test_a_segfaulting_child_still_exits_139():
    """The injected defect, and the reason this file exists. 128+11 = 139: the
    same number CI reported, so a reader comparing logs sees the same code."""
    r = wrap(sys.executable, "-c", "import faulthandler; faulthandler._sigsegv()")
    assert r.returncode == 139, (r.returncode, r.stderr[-500:])
    assert "SIGSEGV" in r.stderr
    assert "CRASH" in r.stderr, "a crash must be named as one, not left as a number"


def test_a_child_killed_by_sigkill_is_not_laundered_into_success():
    """137 is the OOM-kill code the issue contrasts 139 with; it must pass
    through just as faithfully."""
    r = wrap(sys.executable, "-c",
             "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    assert r.returncode == 137, (r.returncode, r.stderr[-500:])


def test_a_passing_child_exits_zero():
    r = wrap(sys.executable, "-c", "pass")
    assert r.returncode == 0, r.stderr[-500:]


def test_a_failing_child_keeps_its_own_code():
    """This repo's verifiers distinguish 1 (ran and disagreed) from 2 (did not
    run). Flattening either would erase a negative control's meaning."""
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


# ---- the child's output must reach the log untouched ------------------------
def test_child_stdout_and_stderr_are_not_swallowed():
    """A wrapper that captures its child loses the tail of a run that dies
    mid-write -- which is the only part of a segfaulting log anybody reads."""
    r = wrap(sys.executable, "-c",
             "import sys; print('CHILD-STDOUT'); print('CHILD-STDERR', file=sys.stderr)")
    assert "CHILD-STDOUT" in r.stdout
    assert "CHILD-STDERR" in r.stderr


def test_the_report_goes_to_stderr_so_stdout_stays_parseable():
    r = wrap(sys.executable, "-c", "print('ONLY-THIS')")
    assert r.stdout.strip() == "ONLY-THIS"
    assert "[rss]" in r.stderr


# ---- the numbers it exists to record ---------------------------------------
def test_peak_rss_tracks_what_the_child_allocated():
    small = wrap(sys.executable, "-c", "pass")
    big = wrap(sys.executable, "-c", "x = bytearray(400*1024*1024); print(len(x))")

    def rss(txt: str) -> int:
        line = [l for l in txt.splitlines() if l.startswith("[rss] peak RSS")][0]
        return int(line.split()[3])

    assert rss(big.stderr) > 300, big.stderr
    assert rss(small.stderr) < rss(big.stderr) / 2, (small.stderr, big.stderr)


def test_the_envelope_is_printed_before_the_command_runs():
    """Printed first on purpose: a child that takes the runner down with it
    leaves no chance to print it afterwards."""
    r = wrap(sys.executable, "-c", "import faulthandler; faulthandler._sigsegv()")
    lines = [l for l in r.stderr.splitlines() if l.startswith("[rss]")]
    assert "ulimit -s" in lines[0], lines[:3]
    assert "thread limits" in lines[1], lines[:3]


def test_pinned_thread_limits_are_reported_when_set(monkeypatch):
    env_cmd = [sys.executable, WRAPPER, "--", sys.executable, "-c", "pass"]
    import os
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    r = subprocess.run(env_cmd, capture_output=True, text=True, env=env)
    assert "OPENBLAS_NUM_THREADS" in r.stderr and "'1'" in r.stderr


def test_unpinned_thread_limits_say_so_rather_than_printing_nothing():
    import os
    env = {k: v for k, v in os.environ.items()
           if k not in THREAD_VARS}
    r = subprocess.run([sys.executable, WRAPPER, "--", sys.executable, "-c", "pass"],
                       capture_output=True, text=True, env=env)
    assert "none pinned" in r.stderr


# ---- --require-thread-pins: the mitigation asserting its own precondition ----
#
# The pin measured in docs/ci-segfault-2026-09-28.md §2 is what takes the
# failing path from 15 OS threads to 1. It lives in a job-level `env:` block,
# which can stop being in force without changing one line of the step that
# depends on it. These controls exercise BOTH directions -- a gate that only
# ever passes is the unsatisfiable-gate failure CLAUDE.md names, and a gate
# that only ever fails is worse.


def _env_without_pins() -> dict:
    import os
    return {k: v for k, v in os.environ.items() if k not in THREAD_VARS}


def _env_with_pins(**override: str) -> dict:
    env = _env_without_pins()
    env.update({k: "1" for k in THREAD_VARS})
    env.update(override)
    return env


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, WRAPPER, *args], capture_output=True,
                          text=True, env=env)


def test_require_thread_pins_refuses_when_the_pin_is_not_in_force():
    """The injected defect for the mitigation itself: delete the job's env:
    block and this step must REFUSE, not quietly measure a different machine."""
    r = _run(_env_without_pins(), "--require-thread-pins", "--",
             sys.executable, "-c", "print('SHOULD-NOT-RUN')")
    assert r.returncode == 126, (r.returncode, r.stderr[-400:])
    assert "REFUSED" in r.stderr
    assert "SHOULD-NOT-RUN" not in r.stdout, "it must refuse BEFORE launching"
    for var in THREAD_VARS:
        assert var in r.stderr, f"the refusal must name {var}, not just refuse"


def test_require_thread_pins_refuses_on_a_partial_pin():
    """Four of five is not pinned. A future edit that drops one variable is the
    likeliest way this silently regresses."""
    env = _env_with_pins()
    del env["MKL_NUM_THREADS"]
    r = _run(env, "--require-thread-pins", "--", sys.executable, "-c", "pass")
    assert r.returncode == 126, (r.returncode, r.stderr[-400:])
    named = r.stderr.split("unset: ", 1)[1].split(".", 1)[0]
    assert named.strip() == "MKL_NUM_THREADS", (
        f"the refusal must name only the missing variable, got {named!r}")


def test_an_empty_pin_is_not_a_pin():
    """`OMP_NUM_THREADS=` in YAML sets the variable to the empty string, which
    caps nothing. It must not read as satisfied."""
    r = _run(_env_with_pins(OMP_NUM_THREADS=""), "--require-thread-pins", "--",
             sys.executable, "-c", "pass")
    assert r.returncode == 126 and "OMP_NUM_THREADS" in r.stderr


def test_require_thread_pins_runs_normally_when_the_pin_is_in_force():
    """The other direction: against the state actually committed in rungs.yml,
    the gate passes. An unsatisfiable gate trains everyone to ignore gates."""
    r = _run(_env_with_pins(), "--require-thread-pins", "--",
             sys.executable, "-c", "print('RAN')")
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    assert "RAN" in r.stdout
    assert "'1'" in r.stderr and "none pinned" not in r.stderr


def test_the_pin_check_never_launders_a_segfault():
    """The load-bearing interaction. Adding a precondition must not weaken the
    property the whole wrapper exists for: with the pin in force, a child that
    segfaults still exits 139, not 126 and not 0."""
    r = _run(_env_with_pins(), "--require-thread-pins", "--",
             sys.executable, "-c", "import faulthandler; faulthandler._sigsegv()")
    assert r.returncode == 139, (r.returncode, r.stderr[-400:])
    assert "SIGSEGV" in r.stderr


def test_refusal_is_not_2_because_pytest_uses_2_for_interrupted():
    """126 and 127 are chosen because no pytest run produces them. If this ever
    becomes 2, a refusal and an interrupted suite become indistinguishable."""
    r = _run(_env_without_pins(), "--require-thread-pins", "--",
             sys.executable, "-c", "pass")
    assert r.returncode not in (0, 1, 2, 3, 4, 5)


def test_an_unknown_flag_refuses_rather_than_being_ignored():
    """A typo'd --require-thread-pins that is silently dropped leaves the job
    looking gated when it is not -- the exact failure this flag prevents."""
    r = _run(_env_without_pins(), "--require-thread-pinz", "--",
             sys.executable, "-c", "print('SHOULD-NOT-RUN')")
    assert r.returncode == 126 and "SHOULD-NOT-RUN" not in r.stdout
    assert "unknown option" in r.stderr


def test_without_the_flag_an_unpinned_run_is_still_allowed():
    """The gate is opt-in: tools/crash_rate.py's unpinned arm and any local
    debugging run must keep working."""
    r = _run(_env_without_pins(), "--", sys.executable, "-c", "print('RAN')")
    assert r.returncode == 0 and "RAN" in r.stdout
