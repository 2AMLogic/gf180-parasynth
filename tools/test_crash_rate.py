#!/usr/bin/env python3
"""Controls for the crash-rate harness: the ways it could report a rate that
is not one must each turn something red.

A crash-rate measurement has exactly three failure modes that matter, and all
three produce the SAME reassuring output -- "0 crashes in N runs":

  1. it counts text instead of exit status, so a liar passes;
  2. it counts a run that never reached the code as a run that did not crash;
  3. it reports a rate when nothing ran at all.

So the controls here are mostly negative: a target that really does SIGSEGV
must be counted as a crash (`faulthandler._sigsegv()` is the injected defect),
a target that merely PRINTS "Segmentation fault" must not be, and a batch in
which nothing executed must REFUSE rather than report zero.

    python -m pytest tools/test_crash_rate.py -q
"""
from __future__ import annotations
import json, os, pathlib, signal, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import crash_rate as cr


# ---- the classifier reads status, never text -------------------------------
def test_signalled_run_is_a_crash():
    verdict, code, sig = cr.classify(signal.SIGSEGV)     # WIFSIGNALED, no core bit
    assert (verdict, code, sig) == (cr.CRASH, None, int(signal.SIGSEGV))


def test_signalled_with_core_dump_is_still_a_crash():
    verdict, _, sig = cr.classify(signal.SIGSEGV | 0x80)  # the 139 case
    assert verdict == cr.CRASH and sig == int(signal.SIGSEGV)


def test_sigterm_is_not_a_crash_but_is_not_a_pass_either():
    """A harness timeout kill must never be laundered into the crash count --
    that would manufacture a rate out of our own impatience."""
    verdict, _, _ = cr.classify(int(signal.SIGTERM))
    assert verdict == cr.ERROR


def test_exit_codes_are_bucketed_apart():
    assert cr.classify(0 << 8)[0] == cr.PASS
    assert cr.classify(1 << 8)[0] == cr.TESTFAIL     # ran, disagreed
    assert cr.classify(2 << 8)[0] == cr.ERROR        # pytest interrupted
    assert cr.classify(5 << 8)[0] == cr.ERROR        # no tests collected


# ---- end to end, against injected defects ---------------------------------
def _write(tmp_path: pathlib.Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_a_real_segfault_is_measured_as_one(tmp_path):
    """The injected defect. `faulthandler._sigsegv()` dereferences NULL, so
    this is the same wait status CI reported as 139."""
    target = _write(tmp_path, "test_boom.py", """
import faulthandler
def test_boom():
    faulthandler._sigsegv()
""")
    out = tmp_path / "r.json"
    rc = cr.main(["--target", target, "--runs", "2", "--json", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 1, "a batch containing a crash must not exit 0"
    assert rep["summary"]["counts"][cr.CRASH] == 2
    assert rep["summary"]["crash_rate"] == 1.0
    assert all(r["signal_name"] == "SIGSEGV" for r in rep["runs"])


def test_a_run_that_only_says_segmentation_fault_is_not_a_crash(tmp_path):
    """The liar control. Scraping stdout would call this a 100 % crash rate;
    reading the wait status calls it what it is."""
    target = _write(tmp_path, "test_liar.py", """
def test_liar():
    print("Fatal Python error: Segmentation fault")
    print("Current thread 0x00007ffbd80bcb80 (most recent call first):")
""")
    out = tmp_path / "r.json"
    rc = cr.main(["--target", target, "--runs", "2", "--json", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 0
    assert rep["summary"]["counts"][cr.CRASH] == 0
    assert rep["summary"]["counts"][cr.PASS] == 2


def test_a_batch_that_never_ran_refuses_instead_of_reporting_zero(tmp_path):
    """The failure mode this whole file exists for: a target that cannot be
    collected exits non-zero without executing a line of the code under test.
    Averaged in, it is indistinguishable from stability."""
    target = _write(tmp_path, "test_unimportable.py", """
import a_module_that_is_not_installed_zzz   # noqa
def test_never_runs():
    pass
""")
    out = tmp_path / "r.json"
    rc = cr.main(["--target", target, "--runs", "2", "--json", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 2, "REFUSED must be distinct from 'no crashes'"
    assert rep["summary"]["counts"][cr.ERROR] == 2
    assert rep["summary"]["runs_that_exercised_the_target"] == 0
    assert rep["summary"]["crash_rate"] is None, \
        "a rate over an empty denominator is a fabricated number"


def test_a_failing_test_is_a_result_not_a_crash(tmp_path):
    target = _write(tmp_path, "test_red.py", """
def test_red():
    assert False
""")
    out = tmp_path / "r.json"
    rc = cr.main(["--target", target, "--runs", "1", "--json", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 0 and rep["summary"]["counts"][cr.TESTFAIL] == 1
    assert rep["summary"]["crash_rate"] == 0.0


def test_a_missing_target_refuses_before_running_anything(tmp_path):
    rc = cr.main(["--target", str(tmp_path / "nope.py"), "--runs", "5"])
    assert rc == 2


def test_extra_env_reaches_the_child_and_is_recorded(tmp_path):
    """A mitigation measured without proving the variable was actually set is
    not a measurement of the mitigation."""
    target = _write(tmp_path, "test_env.py", """
import os
def test_env():
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1", os.environ.get("OPENBLAS_NUM_THREADS")
""")
    out = tmp_path / "r.json"
    rc = cr.main(["--target", target, "--runs", "1",
                  "--env", "OPENBLAS_NUM_THREADS=1", "--json", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 0, "the env override did not reach the child"
    assert rep["environment"]["extra_env"] == {"OPENBLAS_NUM_THREADS": "1"}


def test_malformed_env_refuses(tmp_path):
    assert cr.main(["--target", str(tmp_path), "--env", "NOTAKEYVALUE"]) == 2


def test_the_environment_block_carries_what_makes_a_rate_comparable():
    """0/200 on an 8-core box with 30 GB free says nothing about a 4-vCPU
    runner. The report has to say which box it was."""
    env = cr.environment({})
    for k in ("rlimit_stack_soft_kb", "sched_affinity", "meminfo_kb",
              "thread_limit_env", "python", "commit"):
        assert k in env, k
    assert env["rlimit_stack_soft_kb"] is None or env["rlimit_stack_soft_kb"] > 0
    assert env["sched_affinity"] >= 1


def test_peak_rss_is_per_run_not_a_running_maximum(tmp_path):
    """`resource.getrusage(RUSAGE_CHILDREN)` is monotone across children, so a
    naive harness reports the heaviest run's RSS for every run and the RSS
    column becomes meaningless. `os.wait4` is per child."""
    small = _write(tmp_path, "test_small.py", """
def test_small():
    assert True
""")
    big = _write(tmp_path, "test_big.py", """
def test_big():
    xs = bytearray(400 * 1024 * 1024)
    assert len(xs) == 400 * 1024 * 1024
""")
    out = tmp_path / "r.json"
    assert cr.main(["--target", big, "--runs", "1", "--json", str(out)]) == 0
    big_rss = json.loads(out.read_text())["summary"]["peak_rss_kb"]
    assert cr.main(["--target", small, "--runs", "1", "--json", str(out)]) == 0
    small_rss = json.loads(out.read_text())["summary"]["peak_rss_kb"]
    assert big_rss > 300 * 1024, big_rss
    assert small_rss < big_rss / 2, (small_rss, big_rss)
