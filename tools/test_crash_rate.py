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
import errno, json, os, pathlib, signal, sys

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


@pytest.mark.parametrize("test, expected_rc, expected_verdict, ran", [
    ("test_fixture", 0, cr.PASS, True),
    ("test_posonly", 2, cr.ERROR, False),
    ("test_posonly_xfail", 2, cr.ERROR, False),
    ("test_asyncgen", 2, cr.ERROR, False),
    ("test_coroutine", 2, cr.ERROR, False),
    ("test_bad_patch", 2, cr.ERROR, False),
    ("test_bad_partial", 2, cr.ERROR, False),
    ("Case::test_measure", 2, cr.ERROR, False),
    ("OkCase::test_measure", 0, cr.PASS, True),
    ("test_failed_assertion", 0, cr.TESTFAIL, True),
    ("test_sync", 0, cr.PASS, True),
])
def test_a_body_that_never_started_is_not_an_exercised_run(
    tmp_path, test, expected_rc, expected_verdict, ran,
):
    marker = tmp_path / "entered"
    target = _write(tmp_path, "test_empty.py", f"""
import functools
import pathlib
import unittest
from unittest import mock
import pytest

M = pathlib.Path({str(marker)!r})

def test_fixture(tmp_path):
    assert tmp_path.is_dir()
    M.touch()

def test_posonly(tmp_path, /):
    M.touch()

@pytest.mark.xfail(strict=True)
def test_posonly_xfail(tmp_path, /):
    M.touch()

@pytest.mark.xfail(strict=True)
async def test_asyncgen():
    M.touch()
    yield

@pytest.mark.xfail(strict=True)
async def test_coroutine():
    M.touch()
    assert False

@pytest.mark.xfail(strict=True)
@mock.patch("no_such_module_1506.attr")
def test_bad_patch(_m):
    M.touch()

def _posonly_helper(x, /):
    M.touch()

test_bad_partial = functools.partial(_posonly_helper)

class Case(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("setup fails before the method")
    def test_measure(self):
        M.touch()

class OkCase(unittest.TestCase):
    def test_measure(self):
        M.touch()

def test_failed_assertion():
    M.touch()
    assert False

@pytest.mark.xfail(strict=True)
def test_sync():
    M.touch()
    assert False
""")
    out = tmp_path / "report.json"
    marker.unlink(missing_ok=True)
    rc = cr.main(["--target", f"{target}::{test}", "--runs", "1", "--json", str(out)])
    assert marker.exists() is ran, test
    assert rc == expected_rc, test
    report = json.loads(out.read_text())
    assert report["runs"][0]["verdict"] == expected_verdict, test
    summary = report["summary"]
    assert summary["runs_that_exercised_the_target"] == int(ran), test
    assert summary["crash_rate"] == (0.0 if ran else None), test


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


# ---------------------------------------------------------------------------
# the CLI's --json destination (#2098, a slice of the #2095 class)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): `--json` was
# written after the work -- in `--probe` mode by `main` at the line the #2095
# census named, and in batch mode by `batch_report` before its own summary
# was printed -- and nothing made the parent directory. Under a missing one
# the write raised FileNotFoundError: exit 1, "the batch ran and at least one
# run crashed", for a batch in which nothing crashed, with the record lost.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and this file's own EXIT
# STATUS block, and not from the module under test: a destination that cannot
# be written is a precondition that was not met, so REFUSED (2) whichever way
# the batch went -- never 1, a crash rate whose evidence is gone. Refused
# before a run where the path says so itself.
#
# WHAT RUNS FOR REAL: `main`, `batch_report`, the `--runs` precondition and
# the summary. Doubled: `run_once`, `probe` and `preconditions` (the last
# spawns an interpreter per import and is not what is under test).
# ---------------------------------------------------------------------------
class _Harness:
    """The real CLI with its child processes doubled: every run PASSes."""

    def __init__(self, monkeypatch):
        self.runs = self.probes = 0
        self.run = {"verdict": cr.PASS, "peak_rss_kb": 2048, "seconds": 0.5,
                    "tail": "", "signal": None, "signal_name": None}
        monkeypatch.setattr(cr, "run_once", lambda *a: self.run_once())
        monkeypatch.setattr(cr, "probe", self.probe)
        monkeypatch.setattr(cr, "preconditions", lambda target: [])

    def run_once(self):
        self.runs += 1
        return dict(self.run)

    def probe(self):
        self.probes += 1
        return {"threads_before_import": 1}


@pytest.fixture
def harness(monkeypatch):
    return _Harness(monkeypatch)


def _full_disk(target):
    """`Path.write_text` that fails for `target` only."""
    real = pathlib.Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *args, **kwargs)

    return write_text


BATCH_ARGV = ["--target", "model/x.py", "--runs", "2"]


@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory; name the record file"),
    ("{tmp}/a-file/r.json", "no record can be written there: [Errno 17] File exists"),
    ("nul\x00dir/r.json", "no record can be written there: embedded null byte"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_json_destination_is_refused_before_anything_runs(harness, tmp_path, capsys,
                                                                       target, reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    rc = cr.main([*BATCH_ARGV, "--json", target.format(tmp=tmp_path)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert harness.runs == 0, "a run was launched for a destination that could not be written"
    assert "crash rate" not in out, out
    assert cr.main(["--probe", "--json", str(tmp_path)]) == 2
    assert harness.probes == 0, "the probe ran for a destination that could not be written"


def test_a_batch_record_under_a_missing_directory_is_created_and_written(harness, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "r.json"
    rc = cr.main([*BATCH_ARGV, "--json", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert harness.runs == 2 and "crash rate 0/2" in out, out
    report = json.loads(target.read_text())
    assert report["summary"]["counts"][cr.PASS] == 2 and report["summary"]["crash_rate"] == 0.0
    assert len(report["runs"]) == 2


def test_a_probe_record_under_a_missing_directory_is_created_and_written(harness, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "probe.json"
    rc = cr.main(["--probe", "--json", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert harness.probes == 1
    assert json.loads(target.read_text())["probe"] == {"threads_before_import": 1}


def test_a_write_failure_after_the_batch_is_refused_with_the_rate_printed(harness, tmp_path, capsys,
                                                                          monkeypatch):
    target = tmp_path / "r.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = cr.main([*BATCH_ARGV, "--json", str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert harness.runs == 2 and "crash rate 0/2" in out, out      # the rate is still printed
    assert "REFUSED" in err and "not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert not target.exists()


def test_a_crashing_batch_whose_record_is_unwritten_is_refused_not_a_crash_rate(harness, tmp_path,
                                                                               capsys, monkeypatch):
    """The fourth corner. A crash is a result and exits 1 while its record
    exists; with the record unwritten the 1 must not be reached."""
    harness.run["verdict"] = cr.CRASH
    argv = [*BATCH_ARGV, "--json", str(tmp_path / "r.json")]
    assert cr.main(argv) == 1
    assert "crash rate 2/2" in capsys.readouterr().out
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(tmp_path / "r.json"))
    assert cr.main(argv) == 2
    out, err = capsys.readouterr()
    assert "crash rate 2/2" in out and "not written" in err, (out, err)


def test_a_probe_whose_record_is_unwritten_is_refused(harness, tmp_path, capsys, monkeypatch):
    target = tmp_path / "probe.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = cr.main(["--probe", "--json", str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert harness.probes == 1 and '"threads_before_import": 1' in out, out
    assert "REFUSED" in err and "not written" in err, err
    assert not target.exists()


def test_the_zero_runs_refusal_still_names_the_count_when_the_record_is_unwritten(harness, tmp_path,
                                                                                 capsys,
                                                                                 monkeypatch):
    """`--runs 0` writes an empty-batch record and then REFUSES. With the
    record unwritten both reasons are printed and the exit is still 2: the
    refusal `test_a_zero_or_negative_run_count_refuses_instead_of_raising`
    pins is not replaced by one about the path."""
    target = tmp_path / "r.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = cr.main(["--target", "model/x.py", "--runs", "0", "--json", str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED: --runs must be at least 1, got 0" in err, err
    assert "not written" in err, err
    assert "Traceback" not in err, err


def test_an_unwritable_record_path_is_refused_before_the_zero_runs_branch(harness, tmp_path, capsys):
    """Nothing is written for a batch of zero, so the path is refused first."""
    rc = cr.main(["--target", "model/x.py", "--runs", "0", "--json", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "REFUSED" in err and "names an existing directory" in err, err
    assert harness.runs == 0


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_record(harness, tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = cr.main([*BATCH_ARGV, "--json", str(parent / "r.json")])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert "crash rate 0/2" in out, out
    assert written == []
