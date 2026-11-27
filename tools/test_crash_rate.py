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

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import crash_rate as cr


# ---- the classifier reads status, never text -------------------------------
def test_signalled_run_is_a_crash():
    verdict, code, sig = cr.classify(signal.SIGSEGV, ["started"])     # WIFSIGNALED, no core bit
    assert (verdict, code, sig) == (cr.CRASH, None, int(signal.SIGSEGV))


def test_signalled_with_core_dump_is_still_a_crash():
    verdict, _, sig = cr.classify(signal.SIGSEGV | 0x80, ["started"])  # the 139 case
    assert verdict == cr.CRASH and sig == int(signal.SIGSEGV)


def test_sigterm_is_not_a_crash_but_is_not_a_pass_either():
    """A harness timeout kill must never be laundered into the crash count --
    that would manufacture a rate out of our own impatience."""
    verdict, _, _ = cr.classify(int(signal.SIGTERM), ["started"])
    assert verdict == cr.ERROR


def test_exit_codes_are_bucketed_apart():
    completed = ["started", "completed"]
    assert cr.classify(0 << 8, completed)[0] == cr.PASS
    assert cr.classify(1 << 8, completed)[0] == cr.TESTFAIL     # ran, disagreed
    assert cr.classify(2 << 8, completed)[0] == cr.ERROR        # pytest interrupted
    assert cr.classify(5 << 8, completed)[0] == cr.ERROR        # no tests collected


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


def test_zero_requested_runs_have_no_rate_or_median(tmp_path):
    target = _write(tmp_path, "test_one.py", "def test_one(): pass")
    out = tmp_path / "r.json"
    assert cr.main(["--target", target, "--runs", "0", "--json", str(out)]) == 2
    summary = json.loads(out.read_text())["summary"]
    assert summary["runs_that_exercised_the_target"] == 0
    assert summary["crash_rate"] is None and summary["median_seconds"] is None


def test_a_skipped_target_does_not_supply_a_crash_rate(tmp_path):
    target = _write(tmp_path, "test_skip.py", """
import pytest
@pytest.mark.skip(reason="no device attached")
def test_device():
    assert False
""")
    out = tmp_path / "r.json"
    assert cr.main(["--target", target, "--runs", "1", "--json", str(out)]) == 2
    summary = json.loads(out.read_text())["summary"]
    assert summary["runs_that_exercised_the_target"] == 0
    assert summary["crash_rate"] is None


def test_safe_path_runs_the_requested_test(tmp_path):
    marker = tmp_path / "entered"
    target = _write(tmp_path, "test_safe.py", f"""
from pathlib import Path
def test_target():
    Path({str(marker)!r}).touch()
""")
    out = tmp_path / "report.json"
    rc = cr.main(["--target", target, "--runs", "1", "--json", str(out),
                  "--env", "PYTHONSAFEPATH=1", "--env", "PYTHONPATH="])
    assert marker.exists()
    assert rc == 0
    summary = json.loads(out.read_text())["summary"]
    assert summary["counts"][cr.PASS] == summary["runs_that_exercised_the_target"] == 1


@pytest.mark.parametrize("startup", ["missing_plugin", "exit_zero", "segfault"])
def test_child_startup_cannot_supply_a_crash_rate(tmp_path, startup):
    marker = tmp_path / "entered"
    target = _write(tmp_path, "test_target.py", f"""
from pathlib import Path
def test_target():
    Path({str(marker)!r}).touch()
""")
    args = []
    if startup == "missing_plugin":
        args = ["--env", "PYTEST_ADDOPTS=-p no_such_pytest_plugin_1506"]
    else:
        code = ("import os\nos._exit(0)" if startup == "exit_zero"
                else "import faulthandler\nfaulthandler._sigsegv()")
        (tmp_path / "conftest.py").write_text(code)
    out = tmp_path / "report.json"
    rc = cr.main(["--target", target, "--runs", "1", "--json", str(out), *args])
    assert not marker.exists()
    report = json.loads(out.read_text())
    run, = report["runs"]
    if startup == "missing_plugin":
        assert (type(run["exit"]) is int and run["exit"] > 0
                and "no_such_pytest_plugin_1506" in run["tail"])
    elif startup == "exit_zero":
        assert run["exit"] == 0
    else:
        assert run["signal"] == signal.SIGSEGV
    assert rc == 2
    assert run["verdict"] == cr.ERROR
    assert report["summary"]["runs_that_exercised_the_target"] == 0
    assert report["summary"]["crash_rate"] is None


def test_an_extra_pytest_target_cannot_supply_the_denominator(tmp_path):
    wanted_marker, other_marker = tmp_path / "wanted", tmp_path / "other"
    wanted = _write(tmp_path, "test_wanted.py", f"""
from pathlib import Path
import pytest
@pytest.mark.skip(reason="no device attached")
def test_wanted():
    Path({str(wanted_marker)!r}).touch()
""")
    other = _write(tmp_path, "test_other.py", f"""
from pathlib import Path
def test_other():
    Path({str(other_marker)!r}).touch()
""")
    out = tmp_path / "report.json"
    args = ["--runs", "1", "--json", str(out)]
    assert cr.main([*args, "--target", other]) == 0
    assert other_marker.is_file()
    other_marker.unlink()
    rc = cr.main([*args, "--target", wanted, "--env", f"PYTEST_ADDOPTS={other}"])
    assert not wanted_marker.exists()
    assert rc == 2
    assert not other_marker.exists()
    report = json.loads(out.read_text())
    assert "additional pytest targets" in report["runs"][0]["tail"]
    assert report["summary"]["runs_that_exercised_the_target"] == 0
    assert report["summary"]["crash_rate"] is None


def test_an_expected_failure_counts_only_if_the_test_body_ran(tmp_path):
    marker = tmp_path / "entered"
    target = _write(tmp_path, "test_expected.py", f"""
import pathlib
import pytest
@pytest.mark.xfail(strict=True)
def test_executed():
    pathlib.Path({str(marker)!r}).touch()
    assert False

@pytest.mark.xfail(run=False)
def test_disabled():
    pathlib.Path({str(marker)!r}).touch()
""")
    out = tmp_path / "report.json"
    for test, expected_rc, ran in [("test_disabled", 2, False), ("test_executed", 0, True)]:
        rc = cr.main(["--target", f"{target}::{test}", "--runs", "1", "--json", str(out)])
        assert marker.exists() is ran
        assert rc == expected_rc
        summary = json.loads(out.read_text())["summary"]
        assert summary["runs_that_exercised_the_target"] == int(ran)
        assert summary["crash_rate"] == (0 if ran else None)


def test_a_body_that_never_started_is_not_an_exercised_run(tmp_path):
    marker = tmp_path / "entered"
    target = _write(tmp_path, "test_empty.py", f"""
import pathlib
import unittest
import pytest

class Case(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("setup fails before the method")
    def test_measure(self):
        pathlib.Path({str(marker)!r}).touch()

@pytest.mark.xfail(strict=True)
async def test_coroutine():
    pathlib.Path({str(marker)!r}).touch()
    assert False

@pytest.mark.xfail(strict=True)
def test_sync():
    pathlib.Path({str(marker)!r}).touch()
    assert False
""")
    out = tmp_path / "report.json"
    for test, expected_rc, ran in [("Case::test_measure", 2, False),
                                   ("test_coroutine", 2, False),
                                   ("test_sync", 0, True)]:
        marker.unlink(missing_ok=True)
        rc = cr.main(["--target", f"{target}::{test}", "--runs", "1", "--json", str(out)])
        assert marker.exists() is ran, test
        assert rc == expected_rc, test
        summary = json.loads(out.read_text())["summary"]
        assert summary["runs_that_exercised_the_target"] == int(ran), test
        assert summary["crash_rate"] == (0 if ran else None), test


@pytest.mark.parametrize("option", ["--fixtures", "--fixtures-per-test"])
def test_listing_fixtures_cannot_supply_a_crash_rate(tmp_path, option):
    marker = tmp_path / "entered"
    target = _write(tmp_path, "test_marker.py", f"""
from pathlib import Path
def test_mark():
    Path({str(marker)!r}).touch()
""")
    out = tmp_path / "report.json"
    args = ["--target", target, "--runs", "1", "--json", str(out)]
    assert cr.main(args) == 0
    assert marker.is_file()
    marker.unlink()
    rc = cr.main([*args, "--env", f"PYTEST_ADDOPTS={option}"])
    assert not marker.exists()
    assert rc == 2
    summary = json.loads(out.read_text())["summary"]
    assert summary["runs_that_exercised_the_target"] == 0 and summary["crash_rate"] is None


@pytest.mark.parametrize("runs", [0, -1])
def test_a_zero_or_negative_run_count_refuses_instead_of_raising(runs, capsys):
    """The empty batch. `--runs 0` used to reach the summary and index
    `sorted(...)[len(runs) // 2]` on an empty list: exit 1 -- this tool's "a run
    crashed" code -- with a traceback where the answer belonged, and the
    `exercised == 0` guard below it never ran. An invalid count is a
    precondition failure, so it must REFUSE and name the count itself; a
    refusal about some other precondition must not satisfy this."""
    rc = cr.main(["--runs", str(runs)])
    out, err = capsys.readouterr()
    assert rc == 2, "an invalid batch size must REFUSE, not raise or exit as a crash"
    assert "REFUSED" in err and "--runs" in err, err
    assert "at least 1" in err, "the refusal must state the minimum"
    assert f"got {runs}" in err, "the refusal must name the offending count"
    assert "Traceback" not in err, err
    assert "crash rate" not in out, out


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
    # kB, not bytes: macOS's ru_maxrss is in bytes and would read as ~400 GB here.
    assert big_rss < 4 * 1024 * 1024, big_rss
    assert small_rss < big_rss / 2, (small_rss, big_rss)


def test_peak_rss_excludes_the_harness_own_memory(tmp_path):
    """On Linux a child's ru_maxrss absorbs the high-water mark of the address
    space it exec'd out of -- which, for a child spawned straight from the
    harness, is the HARNESS (fork/vfork/posix_spawn all start there). Under
    `pytest tools/` that parent is a few hundred MB, and every run reported
    that instead of its own peak. Make the parent large on purpose, with pages
    actually touched, and require a trivial run to report a small number."""
    small = _write(tmp_path, "test_small.py", """
def test_small():
    assert True
""")
    n = 600 * 1024 * 1024
    ballast = bytearray(n)
    ballast[::4096] = b"\x01" * len(range(0, n, 4096))   # resident, not just reserved
    out = tmp_path / "r.json"
    assert cr.main(["--target", small, "--runs", "1", "--json", str(out)]) == 0
    small_rss = json.loads(out.read_text())["summary"]["peak_rss_kb"]
    del ballast
    # A bare pytest+numpy interpreter is well under 200 MB; the ballast alone is 600.
    assert small_rss < 300 * 1024, small_rss
