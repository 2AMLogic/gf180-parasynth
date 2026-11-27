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
import json, os, pathlib, subprocess, sys, textwrap, time, threading
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


def test_prints_pass_but_exits_an_undeclared_code_is_failure_with_its_own_code():
    r = ra.run_one(f'{PY} -c "print(\'PASS\'); raise SystemExit(7)"')
    assert r["state"] == ra.FAIL
    assert r["rc"] == 7, "the child's own status must survive"


def test_a_declared_refusal_is_no_verdict_with_its_own_code():
    """2, 3 and 126 are the declared refusal statuses (tools/exit_contract.py):
    nothing was measured, which is no evidence -- not a failed measurement."""
    for code in (2, 3, 126):
        r = ra.run_one(f'{PY} -c "print(\'REFUSED: synthetic\'); raise SystemExit({code})"')
        assert r["state"] == ra.NO_VERDICT, code
        assert r["rc"] == code, "the original status must still be recorded"


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
    # Exit 1, the mismatch status -- a RESULT, not a refusal. (2, 3 and 126
    # are the declared refusals; this test is about a job that ran and
    # disagreed.)
    res = ra.run_all([f'{PY} -c "pass"',
                      f'{PY} -c "raise SystemExit(1)"',
                      f'{PY} -c "pass"'])
    assert [r["state"] for r in res] == [ra.PASS, ra.FAIL, ra.PASS]
    assert res[1]["rc"] == 1
    assert ra.main([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(1)"']) == 1


def test_no_verdict_still_fails_the_overall_run():
    """It is not a pass. Missing evidence must not look like success."""
    assert ra.main([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(2)"']) == 1


def test_a_refusal_from_the_alternate_family_still_fails_the_overall_run():
    assert ra.main([f'{PY} -c "pass"', f'{PY} -c "raise SystemExit(3)"']) == 1


# --- a job with no command (#1476) --------------------------------------------
# `sh -c ""` runs nothing and exits 0, so a recipe line that expands to nothing
# -- an empty `$(shell ...)` entry, a comment in a generated list -- was recorded
# PASS: a passing job that never existed. These are the inputs reproduced on
# main before the fix (each one `state="PASS", rc=0`). Each must be refused
# before any shell starts.

BLANK_OR_COMMENT_ONLY = [
    pytest.param("", id="empty"),
    pytest.param("   ", id="spaces"),
    pytest.param("\t\n  \n", id="tabs-and-newlines"),
    pytest.param("# just a comment", id="comment"),
    pytest.param("  # indented\n\t# twice", id="indented-comments"),
]


@pytest.mark.parametrize("cmd", BLANK_OR_COMMENT_ONLY)
def test_a_blank_or_comment_only_job_is_a_launch_error_before_any_shell_starts(
        cmd, monkeypatch):
    real_popen, started = ra.subprocess.Popen, []

    def spy(*args, **kwargs):
        started.append(args)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(ra.subprocess, "Popen", spy)
    r = ra.run_one(cmd, on_spawn=started.append)
    assert r["state"] == ra.LAUNCH_ERROR
    assert r["rc"] is None
    assert r["cmd"] == cmd, "the original command must be preserved verbatim"
    assert "blank or comment-only" in r["out"]
    assert started == [], "a shell was started for a job that has nothing to run"


@pytest.mark.parametrize("cmd", BLANK_OR_COMMENT_ONLY)
def test_a_lone_blank_or_comment_only_job_fails_the_cli(cmd, capsys):
    assert ra.main([cmd]) == 1
    assert "0/1 passed" in capsys.readouterr().out


def test_the_cli_refuses_a_blank_job_passed_as_an_argument(tmp_path):
    """The issue's reproduction, as the process a Makefile would invoke."""
    out = tmp_path / "r.json"
    p = subprocess.run([PY, ra.__file__, "", "--json", str(out)],
                       capture_output=True, text=True)
    assert p.returncode == 1, p.stdout
    assert "0/1 passed" in p.stdout
    got = json.loads(out.read_text())
    assert got[0]["state"] == ra.LAUNCH_ERROR and got[0]["rc"] is None


def test_a_blank_job_beside_a_real_one_fails_the_run_and_the_real_one_still_runs(
        tmp_path, capsys):
    """The control for the guard itself: a sibling that merely STARTS with a
    comment must run, or the fix has turned a false pass into a false refusal
    of real work."""
    real = "# leading comment\nprintf '%s' '# played'"
    out = tmp_path / "r.json"
    assert ra.main(["", real, "--json", str(out)]) == 1
    assert "1/2 passed" in capsys.readouterr().out
    got = json.loads(out.read_text())
    assert [g["cmd"] for g in got] == ["", real], "input order, commands verbatim"
    assert (got[0]["state"], got[0]["rc"]) == (ra.LAUNCH_ERROR, None)
    assert "blank or comment-only" in got[0]["out"]
    assert (got[1]["state"], got[1]["rc"], got[1]["out"]) == (ra.PASS, 0, "# played")


@pytest.mark.parametrize("cmd, out", [
    pytest.param("# leading comment\nprintf '%s' '# played'", "# played",
                 id="command-after-a-comment"),
    pytest.param("echo a#b", "a#b\n", id="embedded-hash"),
    pytest.param("echo '# quoted'", "# quoted\n", id="quoted-hash"),
    pytest.param("echo hi # trailing comment", "hi\n", id="trailing-comment"),
    pytest.param(":", "", id="silent-builtin"),
    pytest.param("   \techo blanks-around   ", "blanks-around\n", id="blanks-around"),
    pytest.param("\n\n  printf ran\n", "ran", id="blank-lines-then-command"),
])
def test_a_real_command_still_runs_verbatim(cmd, out):
    r = ra.run_one(cmd)
    assert r["state"] == ra.PASS and r["rc"] == 0
    assert r["out"] == out


def test_a_carriage_return_does_not_hide_a_command_from_the_guard():
    """sh ends a line only at a newline, so this is ONE comment: nothing runs,
    and it must not pass on the strength of a splitter that treats CR as a line
    end and so "sees" a command."""
    r = ra.run_one("# comment\rprintf played")
    assert r["state"] == ra.LAUNCH_ERROR and r["rc"] is None


def test_a_carriage_return_is_not_a_shell_blank_so_it_still_runs_and_fails():
    """The opposite error: a CR is an ordinary character to sh, so this is a
    command named CR. It must reach the shell and be reported with the shell's
    own 127, not refused as blank."""
    r = ra.run_one(" \r ")
    assert r["state"] == ra.FAIL and r["rc"] == 127


@pytest.mark.parametrize("cmd, nothing", [
    ("", True), (" \t", True), ("# c", True), ("  # c\n\t# d\n", True),
    ("# c\rprintf x", True),
    (" \r ", False), ("\x0b", False), ("\\\n", False),
    ("# c\nprintf x", False), ("echo '#'", False), (":", False), (";", False),
])
def test_blank_or_comment_only_follows_the_shell_not_python_whitespace(cmd, nothing):
    assert ra.is_blank_or_comment_only(cmd) is nothing


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


def test_a_declared_refusal_is_an_ordinary_failure_for_a_non_verifier():
    """The escape hatch covers every declared status, not just 2."""
    for code in (3, 126):
        r = ra.run_one(f'{PY} -c "raise SystemExit({code})"', verifier=False)
        assert r["state"] == ra.FAIL and r["rc"] == code, code


def test_mismatch_and_did_not_run_are_distinguishable_in_the_summary():
    res = [ra.run_one(f'{PY} -c "raise SystemExit(1)"'),
           ra.run_one(f'{PY} -c "raise SystemExit(2)"')]
    s = ra.summarise(res)
    assert "FAIL(1)" in s and ra.NO_VERDICT in s


# --- write_json_atomic -------------------------------------------------------
# The --json report and, since #842, the DAG's dag-results.json both persist
# through this helper, so a partial write here is a REFUSED there. The cases
# that matter: the bytes are exactly the format each caller already shipped
# (a reformat reorders a git-tracked file), and no tmp survives either path.

def test_write_json_atomic_sort_keys_matches_the_dag_records_format(tmp_path):
    value = {"b": 1, "a": {"z": 2, "y": 1}}
    p = tmp_path / "r.json"
    ra.write_json_atomic(str(p), value, sort_keys=True)
    assert p.read_bytes() == (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def test_write_json_atomic_default_keeps_insertion_order(tmp_path):
    """The --json report's existing format: default parameters must not
    silently start sorting keys and churn that file too."""
    value = {"b": 1, "a": 2}
    p = tmp_path / "r.json"
    ra.write_json_atomic(str(p), value)
    assert p.read_bytes() == (json.dumps(value, indent=2) + "\n").encode()
    assert list(json.loads(p.read_text())) == ["b", "a"]


def test_write_json_atomic_leaves_no_tmp_behind_on_success(tmp_path):
    p = tmp_path / "r.json"
    ra.write_json_atomic(str(p), {"ok": True})
    assert list(tmp_path.glob("*tmp*")) == []


def test_write_json_atomic_a_failure_before_the_replace_leaves_the_original_intact(tmp_path, monkeypatch):
    """The injected control: die after the tmp write, before os.replace.
    The pre-existing file must be complete and no tmp may survive -- the
    guarantee compile_dag's records now rest on."""
    p = tmp_path / "r.json"
    ra.write_json_atomic(str(p), {"kept": True})
    original = p.read_bytes()

    def die(src, dst):
        raise RuntimeError("injected: killed after tmp write, before replace")

    monkeypatch.setattr(os, "replace", die)
    with pytest.raises(RuntimeError):
        ra.write_json_atomic(str(p), {"lost": True})
    assert p.read_bytes() == original
    assert list(tmp_path.glob("*tmp*")) == []


def test_write_json_atomic_passes_default_through_to_json_dump(tmp_path):
    """#881: manifest's records serialise through its `_canonical` default, so
    the helper must forward one rather than raise on the first non-native
    value (or silently change that caller's bytes)."""
    p = tmp_path / "r.json"
    ra.write_json_atomic(str(p), {"v": {1, 2}}, sort_keys=True, default=sorted)
    assert p.read_bytes() == (json.dumps({"v": [1, 2]}, indent=2, sort_keys=True) + "\n").encode()
    assert list(tmp_path.glob("*tmp*")) == []
