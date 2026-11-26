"""Controls for the apparatus of tools/probes/holdout_injected_controls.py
(issues #1118 and #1124, and the shared verdict's suite, #1400).

The probe measures whether injected defects turn `tools/test_holdout.py` red,
so the probe itself is the apparatus. Each case builds a throwaway tree (the
real `tools/run_case.py` and `tools/holdout.py`, so the injections genuinely
apply, plus a caller-controlled `tools/test_holdout.py`) and drives the real
`run()`/`main()` against it by repointing the probe's ROOT.

Issue #1118: `run()` used to return pytest's own process exit status straight
into `main()`'s outcome space. With `tools/test_holdout.py` deleted, pytest
exits 4 in every injected copy, every control landed in the "fired" branch,
and the probe printed "every control fired" and exited 0 -- a false green over
exactly the drift the control exists to surface. Measured pytest statuses
pinned here (9.2.0): 4 (path does not exist), 2 (a collection/import error --
which `main()` used to misreport as "the injection point is gone"), 5 (no
tests collected), and 1 with no parseable `FAILED tools/test_holdout.py::`
line (an ERROR-only run), which used to report "red, 0 test(s) caught it" and
pass.

Issue #1124 is the same false green one layer out. `run()` decides FIRED from
`rc == 1 and failed`, and nothing ran the UNMUTATED tree, so a suite that was
red for its own reasons read as three healthy controls: this file's own
healthy-firing pin used `def test_a(): assert 0` as its suite, and passed.
`main()` now runs the unmutated copy once, first, and REFUSES (exit 2) unless
it is green, before any control runs. That always-failing fixture is the
rule-8 input for the gate (`test_an_unconditionally_red_suite_...` below);
the healthy-firing pin needs a suite that is GREEN on the unmutated targets
and RED under every injection, which is what `_green_then_red()` builds. A
probe that refused everything would satisfy every refusal assertion here, so
the healthy firing is pinned too.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import holdout_injected_controls as hic  # noqa: E402

#: The real tree, captured before any test repoints `hic.ROOT`.
REAL_ROOT = hic.ROOT

#: Red for its own reasons, whatever is injected: the shape #1124 measured.
ALWAYS_RED = "def test_a():\n    assert 0\n"

#: An autouse fixture that raises: pytest reports ERROR lines, exits 1, and
#: writes no `FAILED tools/test_holdout.py::` line to parse.
BOOM = ("import pytest\n\n\n"
        "@pytest.fixture(autouse=True)\n"
        "def boom():\n"
        "    raise RuntimeError('apparatus control')\n")

FAIL_THEN_EXIT = ("import pytest\n\n\ndef test_a():\n    assert 0\n\n\n"
                  "def test_b():\n    pytest.exit('stop', returncode={status})\n")

#: Every way a `tools/test_holdout.py` cannot go green, with the pytest status
#: each one produces: (file text or None to leave it out, conftest, status).
BROKEN = {
    "deleted": (None, None, 4),
    "import_error": ("import gf180_no_such_module_control\n", None, 2),
    "no_tests": ("# no tests defined\n", None, 5),
    "error_only": ("def test_a():\n    assert 1\n", BOOM, 1),
    # A failing test AND pytest.exit(returncode=2): rc 2 WITH a parsed FAILED
    # line, so the baseline's `if failed:` branch must name 2, not a literal 1
    # (#1128).
    "failed_then_exit": (FAIL_THEN_EXIT.format(status=2), None, 2),
    "failed_then_exit_zero": (FAIL_THEN_EXIT.format(status=0), None, 0),
}


def _tree(tmp_path: pathlib.Path, test_holdout: str | None,
          conftest: str | None = None) -> pathlib.Path:
    """A minimal tree the probe can inject into: the real injection targets
    (so every INJECTS pattern matches exactly once) and a controlled
    `tools/test_holdout.py`. `None` leaves that file out entirely."""
    root = tmp_path / "apparatus"
    (root / "tools").mkdir(parents=True)
    for rel in ("tools/run_case.py", "tools/holdout.py"):
        shutil.copyfile(REAL_ROOT / rel, root / rel)
    if test_holdout is not None:
        (root / "tools/test_holdout.py").write_text(test_holdout)
    if conftest is not None:
        (root / "conftest.py").write_text(conftest)
    return root


def _green_then_red(*without: str) -> str:
    """A `tools/test_holdout.py` that is GREEN on the unmutated injection
    targets and RED under every INJECTS entry: one test per control, each
    reading its own injection site. `without` names controls whose site the
    caller has already scrubbed from the tree, which the baseline would see.
    Structural on purpose -- the real suite imports the runner's whole closure
    (model/, scipy), which only the real tree has, and the thing under test
    here is the probe's verdict on a (green, red, ...) suite. The seal's own
    behaviour is `make controls`' question, asked of the real tree. Built from
    INJECTS so the two cannot drift apart."""
    lines = ["import pathlib", "import re", ""]
    for name, (rel, pattern, _replacement) in hic.INJECTS.items():
        if name in without:
            continue
        lines += [f"def test_{name.lower()}_site_is_present():",
                  f"    src = pathlib.Path({rel!r}).read_text()",
                  f"    assert re.search({pattern!r}, src), \"{name} site gone\"",
                  ""]
    return "\n".join(lines)


def _synthetic_expected(monkeypatch, **override) -> None:
    """Repoint `EXPECTED` at the names `_green_then_red()` produces (the real
    names belong to the real suite, which this tree does not carry); `override`
    replaces one control's set."""
    exp = {}
    for control, (_, pattern, _) in hic.INJECTS.items():
        site = hic.Site(f"assert re.search({pattern!r}, src)")
        names = override.get(control, {f"test_{control.lower()}_site_is_present"})
        exp[control] = {name: site for name in names}
    monkeypatch.setattr(hic, "EXPECTED", exp)


def _probe(monkeypatch, capsys, root: pathlib.Path) -> tuple[int, str]:
    monkeypatch.setattr(hic, "ROOT", root)
    rc = hic.main()
    return rc, capsys.readouterr().out


def _assert_no_control_ran(out: str) -> None:
    """Not one control reported anything, fired or refused: the baseline is
    the only line that may say REFUSED."""
    assert out.count(": REFUSED --") == 1, out
    assert "caught it" not in out
    for name in hic.INJECTS:
        assert f"{name}:" not in out


def test_an_unconditionally_red_suite_is_refused_not_three_fired_controls(
        monkeypatch, tmp_path, capsys):
    """The rule-8 input (docs/verification-rules.md): a suite that fails
    before anything is injected used to print "every control fired" and exit
    0, three times over, having measured nothing."""
    root = _tree(tmp_path, test_holdout=ALWAYS_RED)
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc == 2, out
    assert "every control fired" not in out
    assert "UNMUTATED" in out and "pytest exited 1" in out
    assert "test_a" in out             # the red is named, not just counted
    assert "1 failed" in out           # and pytest's own tail is shown
    _assert_no_control_ran(out)


@pytest.mark.parametrize("shape", sorted(BROKEN))
def test_a_suite_that_cannot_go_green_is_refused_before_any_control_runs(
        monkeypatch, tmp_path, capsys, shape):
    """Each #1118 status now stops at the baseline: one refusal naming it,
    and no control line at all -- where the probe used to print three."""
    test_holdout, conftest, status = BROKEN[shape]
    root = _tree(tmp_path, test_holdout, conftest)
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc == 2, out
    assert "every control fired" not in out
    assert f"pytest exited {status}" in out   # the status is named
    if status != 1:                           # ...and no other one is (#1128)
        assert "pytest exited 1" not in out
    assert "the injection point is gone" not in out
    _assert_no_control_ran(out)


@pytest.mark.parametrize("shape", sorted(BROKEN))
def test_run_alone_still_maps_a_broken_suite_status_to_a_refusal(
        monkeypatch, tmp_path, shape):
    """`run()` keeps its own mapping (the #1118 fix) although `main()` now
    stops these trees at the baseline: an INJECTS replacement that is itself
    what breaks the suite reaches `run()` with a green baseline behind it."""
    test_holdout, conftest, status = BROKEN[shape]
    monkeypatch.setattr(hic, "ROOT", _tree(tmp_path, test_holdout, conftest))
    res = hic.run("GATE_OFF")
    assert res.outcome == hic.REFUSED
    assert f"pytest exited {status}" in res.reason


def test_run_names_its_own_suite_when_no_failed_line_parses(monkeypatch, tmp_path):
    """The suite became an argument of `verdict()` when the sound probe began
    sharing it (#1400), so this probe hands it its own at the call site. That
    is a literal again, and a wrong one is invisible in every case above: the
    reason is the only place a refusal is spoken, and it would name a file
    this probe never ran."""
    test_holdout, conftest, _status = BROKEN["error_only"]
    monkeypatch.setattr(hic, "ROOT", _tree(tmp_path, test_holdout, conftest))
    res = hic.run("GATE_OFF")
    assert res.outcome == hic.REFUSED
    assert "no call failure was recorded for tools/test_holdout.py" in res.reason


def test_a_missing_injection_point_keeps_its_own_distinct_refusal(
        monkeypatch, tmp_path, capsys):
    # The pre-#1118 refusal reason must survive as its own message: scrub the
    # CLEAN_OFF pattern out of the copied holdout.py, so that one control
    # cannot inject while the other two still fire. The suite must not watch
    # the scrubbed site: the baseline sees the tree as the caller left it and
    # would refuse before CLEAN_OFF got to say why.
    _synthetic_expected(monkeypatch)
    root = _tree(tmp_path, test_holdout=_green_then_red("CLEAN_OFF"))
    target = root / "tools/holdout.py"
    text = target.read_text()
    needle = '    if _git("diff", "--name-only", "HEAD", "--", rel).strip():'
    assert needle in text  # the pattern this control injects against
    target.write_text(text.replace(needle, "    if True:"))
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc != 0, out
    assert "the injection point is gone from tools/holdout.py" in out
    assert "every control fired" not in out
    assert out.count("red, 1 test(s) caught it") == len(hic.INJECTS) - 1


def test_a_working_apparatus_still_fires_and_names_what_caught_it(
        monkeypatch, tmp_path, capsys):
    _synthetic_expected(monkeypatch)
    root = _tree(tmp_path, test_holdout=_green_then_red())
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc == 0, out
    assert "green on the UNMUTATED tree" in out
    assert "every control fired" in out
    assert out.count("red, 1 test(s) caught it") == len(hic.INJECTS)
    for name in hic.INJECTS:
        assert f"test_{name.lower()}_site_is_present" in out


# Issue #1136: a red is not THIS injection's red unless it includes a recorded
# test. The baseline is green in every case below, so #1124's gate passes them.

def test_a_red_entirely_outside_the_expected_set_is_refused_not_fired(
        monkeypatch, tmp_path, capsys):
    """The rule-8 input: green baseline, every control goes red, but in tests
    the record does not name (a flaky test, or a defect that arose after the
    baseline copy). Before #1136 this printed "every control fired"."""
    _synthetic_expected(monkeypatch, GATE_OFF={"test_somewhere_else"})
    root = _tree(tmp_path, test_holdout=_green_then_red())
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc == 2, out
    assert "every control fired" not in out
    gate = [ln for ln in out.splitlines() if ln.startswith("GATE_OFF:")]
    assert len(gate) == 1 and "REFUSED" in gate[0]
    assert "test_somewhere_else" in gate[0]                  # expected, named
    assert "test_gate_off_site_is_present" in gate[0]        # parsed, named
    assert out.count("red, 1 test(s) caught it") == len(hic.INJECTS) - 1


def test_a_renamed_expected_test_is_refused_not_silently_passed(
        monkeypatch, tmp_path, capsys):
    _synthetic_expected(monkeypatch, STALE_OFF={"test_the_old_name"})
    rc, out = _probe(monkeypatch, capsys, _tree(tmp_path, _green_then_red()))
    assert rc == 2 and "every control fired" not in out, out


def test_wrong_assertion_in_expected_holdout_test_is_refused(
        monkeypatch, tmp_path, capsys):
    _synthetic_expected(monkeypatch)
    suite = _green_then_red().replace(
        "def test_gate_off_site_is_present():",
        "def test_gate_off_site_is_present():\n"
        "    assert 'if False and is_holdout(case):' not in "
        "pathlib.Path('tools/run_case.py').read_text(), 'apparatus failed'")
    rc, out = _probe(monkeypatch, capsys, _tree(tmp_path, test_holdout=suite))
    assert rc == 2, out
    assert "GATE_OFF: REFUSED" in out and "apparatus failed" in out
    assert out.count("red, 1 test(s) caught it") == 2


def test_an_empty_expected_set_is_refused(monkeypatch, tmp_path, capsys):
    _synthetic_expected(monkeypatch, CLEAN_OFF=set())
    rc, out = _probe(monkeypatch, capsys, _tree(tmp_path, _green_then_red()))
    assert rc == 2 and "every control fired" not in out, out
    assert "cannot be attributed" in out


def test_a_superset_of_the_expected_set_still_fires_and_names_the_extras(
        monkeypatch, tmp_path, capsys):
    """Overlap is enough: an added test, or a recorded name that no longer
    exists beside one that does, must not turn the probe noisy."""
    _synthetic_expected(monkeypatch, GATE_OFF={"test_gate_off_site_is_present", "test_gone"})
    rc, out = _probe(monkeypatch, capsys, _tree(tmp_path, _green_then_red()))
    assert rc == 0, out
    assert "every control fired" in out


def test_run_names_extra_failures_as_information_only(monkeypatch, tmp_path, capsys):
    _synthetic_expected(monkeypatch)
    suite = _green_then_red() + (
        "\ndef test_extra():\n"
        "    assert 'if False and is_holdout(case):' not in "
        "pathlib.Path('tools/run_case.py').read_text()\n")
    rc, out = _probe(monkeypatch, capsys, _tree(tmp_path, test_holdout=suite))
    assert rc == 0, out
    assert "GATE_OFF: red, 1 test(s) caught it" in out
    assert "information only: test_extra" in out
