"""tools/probes/holdout_injected_controls.py: `main()` must keep REFUSED
(exit 2) distinguishable from CONTROL DID NOT FIRE (exit 1) in the process
exit status, because that is the channel `tools/run_all.py` classifies on
(rc in {2, 3, 126} -> NO-VERDICT, anything else non-zero -> FAIL).

Issue #1113. Before the fix, `main()` folded a refusal from `run()` into the
same `bad` counter as a genuine non-fire and the process could only ever exit
0 or 1, so `make controls` recorded both outcomes as FAIL and the Makefile's
"Exit 2 (REFUSED)" comment named a code the probe never emitted.

Issue #1118 moved the same boundary one layer down: `run()` no longer returns
a subprocess exit status at all -- it returns a `Result` whose outcome is one
of FIRED / NO_FIRE / REFUSED -- so pytest's own statuses (a deleted
`tools/test_holdout.py` exits 4, a collection error exits 2) can never land in
the "fired" bucket. Those apparatus controls live in
`tools/probes/test_holdout_control_apparatus.py`; the cases below pin the
precedence `main()` applies to Results: a control that genuinely did not fire
(a defect in the seal) outranks a refusal (the apparatus cannot run), so a mix
exits 1, not 2. The mixed case is the input that defeats a naive "any refusal
wins" implementation, per docs/verification-rules.md rule 8 (a guard ships
with the input that defeats it). Started red: on the unfixed `main()` the
refusal-only and refusal+red cases returned 1, and the summary line said
"did not fire" for refusals.

Issue #1124 added the gate in front of the loop: `main()` runs `baseline()`
(the UNMUTATED tree) first and a baseline that is not green is exit 2 with no
control run at all. So `_drive` stands in for a green baseline as well as for
`run()`; the first case pins the red one.

Issue #1400: the decision itself, `verdict()`, is no longer this probe's alone
-- `model/probe_injection_discrimination.py` imports it for its own mutants,
which is what let a pytest collection error there print as three kills. The
suite that ran became an argument, and a required one; the last two cases pin
that. They call `verdict()` directly because the point is the message, and the
staged runs in `tools/probes/test_holdout_control_apparatus.py` only assert its
status; the one that reaches this branch covers the call site, not the
argument.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "probes"))
import holdout_injected_controls as hic  # noqa: E402

GREEN = hic.Result(hic.NO_FIRE, [], "pytest stayed green on the UNMUTATED tree")


def _drive(monkeypatch, results, base: hic.Result | None = None):
    """Run main() with `baseline()` patched to `base` (a GREEN baseline unless
    given) and `run()` patched to return a Result per control, in INJECTS
    order (GATE_OFF, CLEAN_OFF, STALE_OFF)."""
    order = iter(results)
    monkeypatch.setattr(hic, "baseline", lambda: GREEN if base is None else base)
    monkeypatch.setattr(hic, "run", lambda _name: next(order))
    return hic.main()


def test_a_baseline_that_is_not_green_refuses_before_any_control_runs(
        monkeypatch, capsys):
    # The #1124 false green: a suite red before any injection made all three
    # controls "fire". Nothing may run behind it, and the exit is the
    # NO-VERDICT channel, not the seal-defect one.
    def _no_control_may_run(_name):
        raise AssertionError("a control ran although the unmutated tree was red")
    monkeypatch.setattr(hic, "run", _no_control_may_run)
    monkeypatch.setattr(hic, "baseline", lambda: hic.Result(
        hic.REFUSED, ["test_a"],
        "pytest exited 1 on the UNMUTATED tree, 1 test(s) red before any "
        "injection (test_a) -- no control's red can mean anything",
        "FAILED tools/test_holdout.py::test_a - assert 0\n1 failed in 0.01s"))
    rc = hic.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "no control's red can mean anything" in out
    assert "test_a" in out
    assert "1 failed in 0.01s" in out      # pytest's tail, shown not summarised
    assert "every control fired" not in out
    assert "did not fire" not in out


def test_every_control_fired_exits_zero(monkeypatch):
    assert _drive(monkeypatch, [hic.Result(hic.FIRED, ["test_a_seal_gate"], ""),
                                hic.Result(hic.FIRED, ["test_b_seal_clean"], ""),
                                hic.Result(hic.FIRED, ["test_c_seal_stale"], "")]) == 0


def test_a_refusal_alone_exits_two_and_says_so(monkeypatch, capsys):
    rc = _drive(monkeypatch, [hic.Result(hic.REFUSED, [], "pytest exited 4; nothing was measured"),
                              hic.Result(hic.REFUSED, [], "pytest exited 4; nothing was measured"),
                              hic.Result(hic.REFUSED, [], "pytest exited 4; nothing was measured")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refused" in out
    assert "did not fire" not in out


def test_a_control_that_did_not_fire_alone_exits_one(monkeypatch):
    assert _drive(monkeypatch, [hic.Result(hic.NO_FIRE, [], "pytest stayed green"),
                                hic.Result(hic.FIRED, ["test_a"], ""),
                                hic.Result(hic.FIRED, ["test_b"], "")]) == 1


def test_a_genuine_non_fire_outranks_a_refusal_exits_one(monkeypatch, capsys):
    # The mixed case: exit 1, not 2 -- a seal defect is worse than the
    # apparatus being unable to run, and both are named in the summary.
    rc = _drive(monkeypatch, [hic.Result(hic.NO_FIRE, [], "pytest stayed green"),
                              hic.Result(hic.REFUSED, [], "pytest exited 2; nothing was measured"),
                              hic.Result(hic.FIRED, ["test_a"], "")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "did not fire" in out and "refused" in out


def test_a_refusal_alongside_only_red_controls_exits_two(monkeypatch):
    # The other boundary of the mix: nothing failed to fire, one refused.
    assert _drive(monkeypatch, [hic.Result(hic.REFUSED, [], "pytest exited 5; nothing was measured"),
                                hic.Result(hic.FIRED, ["test_a"], ""),
                                hic.Result(hic.FIRED, ["test_b"], "")]) == 2


def test_a_refusal_reason_names_the_status_that_produced_it(monkeypatch, capsys):
    # The refusal line is the only place the reason is spoken: it must carry
    # the status, because "refused" alone cannot be told apart from the
    # injection-point-gone refusal it used to be folded into.
    _drive(monkeypatch, [hic.Result(hic.REFUSED, [], "pytest exited 4; nothing was measured"),
                         hic.Result(hic.FIRED, ["test_a"], ""),
                         hic.Result(hic.FIRED, ["test_b"], "")])
    out = capsys.readouterr().out
    assert "pytest exited 4" in out


def test_verdict_names_the_suite_that_ran_when_no_failed_line_parses():
    # `suite` names the file THIS probe ran. It was a literal here before the
    # sound probe began sharing the decision, and the literal was this
    # probe's own suite: reused unchanged, the sound probe would have refused
    # naming a file it never ran.
    res = hic.verdict(1, hic.SuiteReport({}), {"test_a": hic.Site("assert ok")}, suite="model/other.py")
    assert res.outcome == hic.REFUSED
    assert "no call failure was recorded for model/other.py" in res.reason
    assert "tools/test_holdout.py" not in res.reason


def test_verdict_refuses_to_guess_the_suite():
    # No default on purpose: a caller that omits `suite` is the input that
    # defeats the case above (docs/verification-rules.md rule 8), and it must
    # fail here rather than refuse against the wrong file's name.
    with pytest.raises(TypeError):
        hic.verdict(1, hic.SuiteReport({}), {"test_a": hic.Site("assert ok")})


def test_an_exception_while_evaluating_the_expected_assertion_refuses(tmp_path):
    suite = "test_example.py"
    (tmp_path / suite).write_text(
        "def broken():\n"
        "    __tracebackhide__ = True\n"
        "    assert False, 'helper failed'\n"
        "def test_helper():\n"
        "    assert broken()\n"
        "def test_key():\n"
        "    assert {}['unrelated']\n")
    expected = {"test_helper": hic.Site("assert broken()"),
                "test_key": hic.Site("assert {}['unrelated']", "KeyError: 'holdout'")}
    rc, report, _ = hic.run_suite(tmp_path, suite)
    assert rc == 1 and report is not None and set(report.failures) == set(expected)
    res = hic.verdict(rc, report, expected, suite=suite)
    assert res.outcome == hic.REFUSED
    assert "helper failed" in res.reason and "KeyError: 'unrelated'" in res.reason


def test_zero_exit_without_a_completed_pytest_report_refuses(tmp_path):
    suite = "test_example.py"
    (tmp_path / suite).write_text("import os\ndef test_exit():\n    os._exit(0)\n")
    rc, report, _ = hic.run_suite(tmp_path, suite)
    assert rc == 0
    res = hic.verdict(rc, report, {}, suite=suite)
    assert res.outcome == hic.REFUSED
    assert "without a failure report" in res.reason


@pytest.mark.parametrize("kind,src,expected_substr", [
    ("setup_error_exit_zero",
     "import pytest\n"
     "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup boom')\n"
     "def test_a(broken):\n    assert 1\n"
     "def test_b():\n    pytest.exit('stop', returncode=0)\n",
     "setup: test_a"),
    ("teardown_error_exit_zero",
     "import pytest\n"
     "@pytest.fixture\ndef broken():\n    yield\n    raise RuntimeError('teardown boom')\n"
     "def test_a(broken):\n    assert 1\n"
     "def test_b():\n    pytest.exit('stop', returncode=0)\n",
     "teardown: test_a"),
    ("early_exit_zero_incomplete",
     "import pytest\n"
     "def test_a():\n    assert 1\n"
     "def test_b():\n    pytest.exit('early', returncode=0)\n"
     "def test_c():\n    assert 0\n",
     "1/3 collected test(s) completed"),
    ("all_skipped_in_setup",
     "import pytest\n"
     "@pytest.mark.skip('no apparatus')\ndef test_a():\n    assert 1\n",
     "executed no tests"),
    ("all_skipped_in_call",
     "import pytest\n"
     "def test_a():\n    pytest.skip('no apparatus')\n",
     "no test passed"),
    ("strict_xpass_exit_zero",
     "import pytest\n"
     "@pytest.mark.xfail(strict=True)\ndef test_a():\n    assert 1\n"
     "def test_b():\n    pytest.exit('stop', returncode=0)\n",
     "test_a"),
])
def test_zero_exit_with_incomplete_or_errored_run_refuses(tmp_path, kind, src, expected_substr):
    suite = f"test_{kind}.py"
    (tmp_path / suite).write_text(src)
    rc, report, _ = hic.run_suite(tmp_path, suite)
    assert rc == 0
    res = hic.verdict(rc, report, {}, suite=suite)
    assert res.outcome == hic.REFUSED, res
    assert expected_substr in res.reason, res
    base = hic.run_baseline(tmp_path, suite)
    assert base.outcome == hic.REFUSED, base
    assert expected_substr in base.reason, base


def test_collect_only_refuses_while_executed_passing_suite_is_no_fire(tmp_path, monkeypatch):
    suite = "test_example.py"
    (tmp_path / suite).write_text("def test_pass():\n    assert 1\n")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    rc, report, _ = hic.run_suite(tmp_path, suite)
    assert rc == 0 and report is not None and report.collected == 1 and report.executed == 0
    res = hic.verdict(rc, report, {}, suite=suite)
    assert res.outcome == hic.REFUSED and "executed no tests" in res.reason
    base = hic.run_baseline(tmp_path, suite)
    assert base.outcome == hic.REFUSED and "executed no tests" in base.reason

    monkeypatch.delenv("PYTEST_ADDOPTS")
    rc2, report2, _ = hic.run_suite(tmp_path, suite)
    assert rc2 == 0 and report2 is not None and report2.executed == 1 and report2.passed == 1
    assert hic.verdict(rc2, report2, {}, suite=suite).outcome == hic.NO_FIRE
    assert hic.run_baseline(tmp_path, suite).outcome == hic.NO_FIRE
