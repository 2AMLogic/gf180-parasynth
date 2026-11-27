"""Tests for the shared control_dag harness (tools/control_dag_lib.py).

THE WRONG-THEN-RIGHT RECORD FOR THIS FILE
-----------------------------------------
#1097 asked for an injected `run_one`-ignores-`must_fail` defect to be caught
by the controls. Measured before this file was written (2026-11-23): it is
not, and cannot be. With the must_fail branch deleted from
`control_dag_lib.run_control`, all three control tools still exit 0, because
every shipped control genuinely fails its named guard -- a red suite and a
red suite with the right test among its failures look identical to a checker
that never distinguishes them. A control tool cannot bootstrap strictness
about its own harness; that is not a defect in the controls, it is what
"shared harness" implies once three tools depend on one copy of the sentence.

So the check lives one level down, here, against canned pytest output in
which the named guard is deliberately absent. These tests are the only thing
in the repository that turns red when the must_fail branch is deleted;
deleting any other branch of the decision logic has its own test below.

This is internal-consistency testing and is labelled as such: it says the
harness implements its contract, not that the guards themselves work -- that
is what the control tools (which use the harness) and the suites they inject
into are for.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import control_dag_lib as cdl                                          # noqa: E402

GUARD = "test_the_guard"


class _Proc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _fake_subprocess(monkeypatch, returncode, lines):
    """Feed run_control canned subprocess output; return what it was called with."""
    seen = {}

    def _run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return _Proc(returncode, "\n".join(lines) + "\n")

    monkeypatch.setattr(cdl.subprocess, "run", _run)
    return seen


def _failed(test):
    return f"FAILED tools/test_x.py::{test} - AssertionError: injected"


# --- the decision logic, one test per branch ------------------------------

def test_red_suite_failing_the_named_guard_holds(monkeypatch):
    _fake_subprocess(monkeypatch, 1, [_failed(GUARD), _failed("test_other")])
    held, failed, why = cdl.run_control("pass", "red", GUARD)
    assert held
    assert failed == ["test_other", GUARD]
    assert why == ""


def test_red_suite_not_failing_the_named_guard_does_not_hold(monkeypatch):
    """THE false-green guard: red for the wrong reason is not a held control.

    This is the assertion #1097's harness consolidation exists to protect,
    and the one whose deletion no control tool notices (see the module
    docstring's wrong-then-right record).
    """
    _fake_subprocess(monkeypatch, 1, [_failed("test_other")])
    held, failed, why = cdl.run_control("pass", "red", GUARD)
    assert not held
    assert "some OTHER assertion" in why
    assert "survive that" in why


def test_green_suite_holds(monkeypatch):
    _fake_subprocess(monkeypatch, 0, [])
    held, _, why = cdl.run_control("pass", "green", None)
    assert held
    assert why == ""


def test_green_shape_going_red_does_not_hold(monkeypatch):
    _fake_subprocess(monkeypatch, 1, [_failed(GUARD)])
    held, _, why = cdl.run_control("pass", "green", None)
    assert not held
    assert "PERMIT" in why


def test_defect_that_stays_green_does_not_hold(monkeypatch):
    _fake_subprocess(monkeypatch, 0, [])
    held, _, why = cdl.run_control("pass", "red", GUARD)
    assert not held
    assert "stayed GREEN" in why


def test_failed_line_parsing_dedupes_and_ignores_summary_lines(monkeypatch):
    lines = [
        _failed(f"{GUARD}[case-2]"),
        f"FAILED tools/test_x.py::{GUARD}[case-2]",   # duplicate, dropped
        "not a FAILED line",
        "1 failed, 3 passed in 0.01s",
    ]
    _fake_subprocess(monkeypatch, 1, lines)
    _, failed, _ = cdl.run_control("pass", "red", GUARD)
    assert failed == [f"{GUARD}[case-2]"]


# --- the runner templates -------------------------------------------------

def test_node_control_formats_node_and_default_suite_into_the_runner(monkeypatch):
    seen = _fake_subprocess(monkeypatch, 0, [])
    cdl.node_control({"issue": 99999}, "green", None)
    code = seen["cmd"][2]
    assert 'doc["nodes"]["ZZ"] = {\'issue\': 99999}' in code
    assert "test_compile_dag_deps.py" in code
    assert str(cdl.ROOT) in code
    assert seen["kw"]["cwd"] == str(cdl.ROOT)


def test_patch_control_strips_the_patch_and_names_the_suite(monkeypatch):
    seen = _fake_subprocess(monkeypatch, 0, [])
    cdl.patch_control("\ncd.stale_against = lambda n, at: 'fresh'\n",
                      "red", GUARD, "test_compile_dag_churn.py")
    code = seen["cmd"][2]
    assert "as cd\ncd.stale_against = lambda n, at: 'fresh'" in code
    assert "test_compile_dag_churn.py" in code


def test_mutation_control_imports_the_module_and_judges_red(monkeypatch):
    seen = _fake_subprocess(monkeypatch, 1, [_failed(GUARD)])
    held, failed, why = cdl.mutation_control("rp.X = 1", GUARD)
    code = seen["cmd"][2]
    assert "import refprofile as rp" in code
    assert "rp.X = 1" in code
    for root in ("tools", "model", "audition"):
        assert f'sys.path.insert(0, str(ROOT / "{root}"))' in code
    assert "test_refprofile.py" in code
    assert str(cdl.ROOT) in code
    assert seen["kw"]["cwd"] == str(cdl.ROOT)
    assert held
    assert failed == [GUARD]
    assert why == ""


# --- the shared main() ------------------------------------------------------

def test_main_labels_each_colour_and_exits_zero_when_all_hold(capsys):
    controls = {"A_RED": ({}, "red", "guard_a", "why a"),
                "A_GREEN": ({}, "green", None, "why g")}
    calls = []

    def run_one(name):
        calls.append(name)
        return True, ["guard_a"], ""

    rc = cdl.main("doc", controls, run_one, show=lambda inj: "injected",
                  ok_message="OK -- all held", argv=[])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == ["A_RED", "A_GREEN"]
    assert re.search(r"^RED\s+A_RED$", out, re.M)
    assert re.search(r"^GREEN\s+A_GREEN$", out, re.M)
    assert "OK -- all held" in out


def test_main_exits_one_and_names_the_control_that_did_not_hold(capsys):
    controls = {"A_RED": ({}, "red", "guard_a", "why a")}
    rc = cdl.main("doc", controls, lambda name: (False, [], "stayed GREEN"),
                  show=lambda inj: "injected", ok_message="OK", argv=[])
    out = capsys.readouterr().out
    assert rc == 1
    assert re.search(r"^DID NOT HOLD\s+A_RED", out, re.M)
    assert "broken run, not a quiet one" in out


def test_main_list_renders_expectation_why_and_injection(capsys):
    controls = {"A_RED": ({"issue": 1}, "red", "guard_a", "why a")}
    rc = cdl.main("doc", controls, lambda name: (True, [], ""),
                  show=lambda inj: f"injected {inj}", ok_message="OK",
                  argv=["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "expects    suite RED, failing guard_a" in out
    assert "why        why a" in out
    assert "  | injected {'issue': 1}" in out


# --- the mutation-only 3-tuple shape (#1099) --------------------------------

def test_main_list_three_tuple_prints_caught_by_and_no_expects_line(capsys):
    """A mutation-only table has no colour leg, so --list must not invent one."""
    controls = {"M": ("rp.X = 1", "guard_m", "why m")}
    rc = cdl.main("doc", controls, lambda name: (True, [], ""),
                  show=lambda m: f"injected {m}", ok_message="OK",
                  argv=["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "caught by  guard_m" in out
    assert "expects" not in out
    assert "why        why m" in out
    assert "  | injected rp.X = 1" in out


def test_main_three_tuple_labels_red_when_held(capsys):
    controls = {"M": ("rp.X = 1", "guard_m", "why m")}
    rc = cdl.main("doc", controls, lambda name: (True, ["guard_m"], ""),
                  show=str, ok_message="OK", argv=[])
    out = capsys.readouterr().out
    assert rc == 0
    assert re.search(r"^RED\s+M$", out, re.M)


def test_main_three_tuple_green_mutation_flips_to_not_held_and_exits_one(capsys):
    """THE red-first check for the 3-tuple path: a mutation whose defect stays
    green must be reported not-held with exit 1 through the shared main(),
    exactly as the capability tool's own scaffolding did before #1099."""
    controls = {"M": ("rp.X = 1", "guard_m", "why m")}
    rc = cdl.main("doc", controls,
                  lambda name: (False, [], "the suite stayed GREEN"
                                         " with the defect injected"),
                  show=str, ok_message="OK", argv=[])
    out = capsys.readouterr().out
    assert rc == 1
    assert re.search(r"^DID NOT HOLD\s+M", out, re.M)
    assert "stayed GREEN" in out
    assert "did not hold" in out


def test_main_with_an_empty_controls_table_refuses_instead_of_printing_ok(capsys):
    """#1469's census shape: `controls={}` ran nothing, `bad` stayed empty and
    the caller's ok_message printed with exit 0. Nothing was measured, so the
    outcome is REFUSED (2), the same code the red-baseline refusal uses."""
    calls = []
    rc = cdl.main("doc", {}, lambda name: calls.append(name), show=str,
                  ok_message="OK -- all held", argv=[])
    out = capsys.readouterr().out
    assert rc == 2
    assert calls == []
    assert "OK -- all held" not in out
    assert "REFUSED" in out and "no control" in out


def test_list_with_an_empty_controls_table_stays_exit_zero():
    # `--list` is an inventory, not a verdict: it must stay exit 0.
    assert cdl.main("doc", {}, lambda name: None, show=str,
                    ok_message="OK", argv=["--list"]) == 0
