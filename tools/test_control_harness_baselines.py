"""The in-tree injected-defect harnesses REFUSE (exit 2) when their suite is
red before any mutation: `tools/control_capability_verdicts.py` and
`tools/control_dag_churn.py`, the two same-shape harnesses of issue #1125, and
`tools/control_ladder_verdict_refusal.py` (#2036), which joined them.

Before the fix each `run_one()` fired on `pytest exited non-zero` plus the
declared `must_fail` name among the FAILED lines, and neither `main()` ever
ran its suite UNMUTATED -- so the name pin attributed the red to a test, not
to the injection. A declared test red for a reason of its own (the rule-8
input planted below: `def test_x(): assert 0`) turned red every control that
named it, and the harness printed `OK -- all 6 ... controls fired`, exit 0.
Started red: on the unfixed harnesses both cases returned 0.

Neither harness mutates anything on disk, so the planted suite cannot be
staged the way `tools/inject_manifest_defects.py` stages a copy. Instead the
harness's `ROOT` is repointed at a tree that is the real one for every purpose
except its suite file: a real copy of it under a `tools/` of symlinks, under a
root of symlinks. The harness's own RUNNER, the real module it patches and its
real `main()` all run unchanged; only pytest's exit status differs.

The second half of each case is what keeps the first from passing for the
wrong reason: with the plant removed, the same tree through the same RUNNER
is GREEN. Without that, a symlink tree that broke the suite on its own would
make `main()` refuse and the refusal would prove nothing about the gate.

(`tools/test_manifest.py` carries the equivalent case for the third harness,
next to that harness's other self-tests. It lives there and not in a suite
these harnesses run whole, because a self-test inside the file under
injection would call `main()` from inside `main()`.)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import control_capability_verdicts as cv  # noqa: E402
import control_dag_churn as cd            # noqa: E402
import control_dag_lib as lib             # noqa: E402
import control_ladder_verdict_refusal as lv  # noqa: E402

REAL_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Top-level entries that are not the tree a harness reads: the worktree's git
#: pointer, this host's venv, the orchestration runtime.
NOT_THE_TREE = {".git", ".venv", ".loom", ".loom-managed", "tools"}

#: (harness, the suite file it runs, the test its first control declares as
#: `must_fail`). Tuple indices are each harness's own CONTROLS shape.
CASES = [
    pytest.param(cv, "test_refprofile.py", cv.CONTROLS["RIG_BOOLEAN_FANOUT"][1],
                 id="capability_verdicts"),
    pytest.param(cd, "test_compile_dag_churn.py",
                 cd.CONTROLS["FRESHNESS_LEG_DROPPED"][2], id="dag_churn"),
    pytest.param(lv, lv.SUITE, lv.CONTROLS["GAP_CHECK_REMOVED"][1],
                 id="ladder_verdict_refusal"),
]


def _tree_with(tmp_path, monkeypatch, harness, suite, plant):
    """Repoint `harness.ROOT` at a tree that is the real one except for
    `tools/<suite>`: a real copy of it, with `def <plant>(): assert 0`
    appended when `plant` is given. Returns that copy's path."""
    root = tmp_path / "tree"
    (root / "tools").mkdir(parents=True)
    for entry in REAL_ROOT.iterdir():
        if entry.name not in NOT_THE_TREE:
            (root / entry.name).symlink_to(entry)
    for entry in (REAL_ROOT / "tools").iterdir():
        if entry.name != suite:
            (root / "tools" / entry.name).symlink_to(entry)
    copy = root / "tools" / suite
    copy.write_text((REAL_ROOT / "tools" / suite).read_text())
    if plant:
        # A later definition shadows the earlier one in the module namespace,
        # so this is the declared test pytest collects.
        copy.write_text(copy.read_text() + f"\n\ndef {plant}():\n    assert 0\n")
    monkeypatch.setattr(harness, "ROOT", root, raising=False)
    # dag_churn runs through the shared harness, whose ROOT is the one its
    # runner and subprocess cwd read.
    monkeypatch.setattr(lib, "ROOT", root)
    return copy


@pytest.mark.parametrize("harness, suite, declared", CASES)
def test_main_refuses_when_the_suite_is_red_before_any_injection(
        tmp_path, monkeypatch, capsys, harness, suite, declared):
    copy = _tree_with(tmp_path, monkeypatch, harness, suite, plant=declared)
    assert harness.main([]) == 2, (
        "a declared test red before any mutation is not evidence of a mutation")
    out = capsys.readouterr().out
    assert "REFUSED" in out and declared in out
    assert not [name for name in harness.CONTROLS if name in out], (
        "the loop ran, and reported controls on a suite whose red the "
        "mutations did not cause")

    copy.write_text((REAL_ROOT / "tools" / suite).read_text())
    rc, failed, tail = harness.run_suite("")
    assert rc == 0, f"the symlink tree alone is red: {failed} {tail}"
