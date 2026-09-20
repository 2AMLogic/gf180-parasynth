"""The runner is verification infrastructure, so it gets verified.

This file exists because `tools/run_all.py` was written to reduce waste and its
first version -- in bash -- contained the exact defect it was meant to prevent:
a job that exited 1 printed `FAIL(??)`, an unknown rendered where a result
belongs. Shipping an untested runner is the same mistake as the untested audio
estimators, which produced six wrong numbers before anyone checked them.

The cases below are the ones that could produce a FALSE GREEN, which is the
only failure mode of a runner that actually matters.
"""
from __future__ import annotations
import json, os, subprocess, sys, textwrap, time, threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_all as ra                                             # noqa: E402

PY = sys.executable


# --- the false-green cases ---------------------------------------------------

def test_prints_fail_but_exits_zero_is_reported_pass():
    """Status comes from the PROCESS, never from the text.

    Deliberate. A verifier that prints a verdict and exits 0 on failure has a
    defect, and the runner must surface the process truth rather than scrape
    output -- otherwise a job printing the word "FAIL" in a filename or a diff
    would be misreported, and the runner would be guessing.
    """
    r = ra.run_one(f'{PY} -c "print(\'FAIL: everything is broken\')"')
    assert r["state"] == ra.PASS
    assert r["rc"] == 0


def test_prints_pass_but_exits_three_is_failure_with_its_own_code():
    r = ra.run_one(f'{PY} -c "print(\'PASS\'); raise SystemExit(3)"')
    assert r["state"] == ra.FAIL
    assert r["rc"] == 3, "the child's own status must survive; 1 and 2 mean different things here"


def test_exit_one_inside_a_shell_command_is_captured():
    """The bash version got this wrong and printed FAIL(??)."""
    r = ra.run_one("echo working; exit 1")
    assert r["state"] == ra.FAIL and r["rc"] == 1


def test_prints_then_hangs_is_no_verdict_not_pass_and_not_fail():
    r = ra.run_one(f'{PY} -c "print(\'PASS\', flush=True); import time; time.sleep(30)"',
                   timeout=1.5)
    assert r["state"] == ra.NO_VERDICT
    assert r["rc"] is None
    assert "PASS" in r["out"], "output up to the hang should still be kept"


def test_a_job_that_cannot_launch_is_an_explicit_error():
    r = ra.run_one("this-command-does-not-exist-anywhere")
    assert r["state"] == ra.FAIL and r["rc"] not in (0, None)


def test_timeout_kills_the_grandchild_not_just_the_shell(tmp_path):
    """shell=True makes a shell the direct child; killing it orphans the work."""
    marker = tmp_path / "still_alive.txt"
    script = textwrap.dedent(f"""
        import time, pathlib
        for _ in range(60):
            pathlib.Path({str(marker)!r}).write_text(str(time.time()))
            time.sleep(0.2)
    """)
    sf = tmp_path / "child.py"
    sf.write_text(script)
    r = ra.run_one(f'{PY} {sf}', timeout=1.0)
    assert r["state"] == ra.NO_VERDICT
    first = marker.read_text() if marker.exists() else ""
    time.sleep(1.5)
    second = marker.read_text() if marker.exists() else ""
    assert first == second, "the grandchild outlived the timeout -- process group not killed"


def test_one_failure_among_successes_fails_overall_and_keeps_every_result():
    # Exit 3, not 2: under this repo's convention 2 means "did not run", which
    # is NO-VERDICT rather than FAIL. Using it here would have tested the
    # wrong thing -- and did, until the convention was added.
    res = ra.run_all([f'{PY} -c "pass"',
                      f'{PY} -c "raise SystemExit(3)"',
                      f'{PY} -c "pass"'])
    assert [r["state"] for r in res] == [ra.PASS, ra.FAIL, ra.PASS]
    assert res[1]["rc"] == 3
    assert ra.main([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(3)"']) == 1


def test_no_verdict_still_fails_the_overall_run():
    """It is not a pass. Missing evidence must not look like success."""
    assert ra.main([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(2)"']) == 1


# --- the exit-code bound -----------------------------------------------------

def test_exit_code_is_bounded_and_cannot_wrap_to_zero():
    """A raw failure count is wrong: shell status is 8 bits, so 256 fails -> 0."""
    many = [f'{PY} -c "raise SystemExit(1)"'] * 8
    assert ra.main(many) == 1
    assert ra.main([f'{PY} -c "pass"']) == 0


def test_the_counts_survive_in_the_json_where_they_cannot_wrap(tmp_path):
    out = tmp_path / "r.json"
    ra.main([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(7)"', "--json", str(out)])
    got = json.loads(out.read_text())
    assert [g["state"] for g in got] == [ra.PASS, ra.FAIL]
    assert got[1]["rc"] == 7


def test_json_report_is_updated_when_each_job_finishes(tmp_path, monkeypatch):
    report = tmp_path / "verification" / "results.json"
    slow_started = threading.Event()
    release_slow = threading.Event()
    finished = []

    def fake_run_one(cmd, timeout=None, env=None, verifier=True):
        if cmd == "slow":
            slow_started.set()
            assert release_slow.wait(2.0)
        return {"cmd": cmd, "state": ra.PASS, "rc": 0,
                "out": "", "secs": 0.01}

    monkeypatch.setattr(ra, "run_one", fake_run_one)
    worker = threading.Thread(target=lambda: finished.append(
        ra.main(["fast", "slow", "--jobs", "2", "--json", str(report)])))
    worker.start()
    assert slow_started.wait(1.0)
    deadline = time.monotonic() + 1.0
    snapshot = []
    while time.monotonic() < deadline:
        snapshot = json.loads(report.read_text())
        if snapshot[0]["state"] == ra.PASS:
            break
        time.sleep(0.01)
    assert snapshot[0]["state"] == ra.PASS
    assert snapshot[1]["state"] == "PENDING"
    release_slow.set()
    worker.join(2.0)
    assert not worker.is_alive()
    assert finished == [0]
    assert [item["state"] for item in json.loads(report.read_text())] == [ra.PASS, ra.PASS]


# --- parallelism -------------------------------------------------------------

def test_independent_jobs_actually_run_in_parallel():
    t0 = time.time()
    ra.run_all([f'{PY} -c "import time; time.sleep(1.2)"'] * 3)
    assert time.time() - t0 < 2.6, "three 1.2 s jobs should not take 3.6 s"


def test_serial_runs_in_order_for_jobs_that_contend(tmp_path):
    log = tmp_path / "order.txt"
    cmds = [f'{PY} -c "open({str(log)!r},\'a\').write(\'{i}\')"' for i in "abc"]
    ra.run_all(cmds, serial=True)
    assert log.read_text() == "abc"


def test_jobs_limit_caps_concurrency():
    t0 = time.time()
    ra.run_all([f'{PY} -c "import time; time.sleep(0.8)"'] * 4, jobs=2)
    assert time.time() - t0 >= 1.4, "--jobs 2 should serialise into two waves"


# --- the summary -------------------------------------------------------------

def test_summary_shows_the_childs_own_code_and_names_every_failure():
    res = ra.run_all([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(5)"'])
    s = ra.summarise(res)
    assert "FAIL(5)" in s
    assert "1/2 passed" in s


def test_summary_marks_a_timeout_distinctly_from_a_failure():
    res = [ra.run_one(f'{PY} -c "import time; time.sleep(9)"', timeout=0.8)]
    assert ra.NO_VERDICT in ra.summarise(res)
    assert "FAIL" not in ra.summarise(res)


# --- the exit-code convention ------------------------------------------------

def test_verifier_exit_two_is_no_evidence_not_failure():
    """This repo's verifiers use 2 for "did not run" -- simulator missing,
    compile failed. Flattening that into FAIL hides the difference between a
    negative control that worked and one that never ran."""
    r = ra.run_one(f'{PY} -c "raise SystemExit(2)"')
    assert r["state"] == ra.NO_VERDICT
    assert r["rc"] == 2, "the original status must still be recorded"


def test_exit_two_is_an_ordinary_failure_for_a_non_verifier():
    r = ra.run_one(f'{PY} -c "raise SystemExit(2)"', verifier=False)
    assert r["state"] == ra.FAIL and r["rc"] == 2


def test_mismatch_and_did_not_run_are_distinguishable_in_the_summary():
    res = [ra.run_one(f'{PY} -c "raise SystemExit(1)"'),
           ra.run_one(f'{PY} -c "raise SystemExit(2)"')]
    s = ra.summarise(res)
    assert "FAIL(1)" in s and ra.NO_VERDICT in s
