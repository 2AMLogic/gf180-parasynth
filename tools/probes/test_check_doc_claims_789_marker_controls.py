"""#905: the #789 marker probe must REFUSE a dirty tree, not misattribute its
controls.

`tools/probes/check_doc_claims_789_marker_controls.py` injects defects into the
live checkout and expects `tools/check_doc_claims.py` to be green between
injections. On a tree with uncommitted WIP that already makes one of the 17
claims stale, the injected-defect controls cannot be interpreted -- the checker
is red for the tree's own reasons before anything is injected -- and the probe
used to print `FAIL` for two of its four controls, indistinguishable from a
guard that stopped guarding. Per docs/verification-rules.md and CLAUDE.md, an
instrument in a wrong state REFUSES (exit 2, no evidence) instead of reporting.

These tests stage the dirty tree for real (an uncommitted edit, restored in a
finally), run the probe as a subprocess, and require exit 2 with no control
output at all.
"""
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "check_doc_claims_789_marker_controls.py"
ROOT = PROBE.resolve().parent.parent.parent
INJECT = ROOT / "fpga/rtl/_ctl789_inject.v"

# (file, find, replace, needle naming the stale claim in the refusal output).
# The README case edits the claim marker's own pattern (the issue's measured
# repro); the verdict.txt case edits the evidence a covered claim greps -- the
# issue's wider trigger class (ordinary work in a covered file).
DIRTY_CASES = [
    ("README.md",
     'grep="^T1: 2/11 items met$"', 'grep="^T1: 4/11 items met$"',
     "T1: 4/11 items met"),
    ("manifests/verdict.txt",
     "T1: 2/11 items met", "T1: 4/11 items met",
     "T1: 2/11 items met"),
]


def _porcelain() -> set[str]:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return set(out.stdout.splitlines())


def _run_probe() -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PROBE)], cwd=ROOT,
                          capture_output=True, text=True)


@pytest.mark.parametrize("rel,old,new,needle", DIRTY_CASES,
                         ids=[c[0] for c in DIRTY_CASES])
def test_dirty_tree_refuses_instead_of_reporting_control_failures(
        rel, old, new, needle):
    f = ROOT / rel
    orig = f.read_text(encoding="utf-8")
    assert old in orig, f"staging anchor not found in {rel}"
    before = _porcelain()
    try:
        f.write_text(orig.replace(old, new), encoding="utf-8")
        p = _run_probe()
        blob = p.stdout + p.stderr
        assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
        assert "REFUSED: baseline not green before injection" in blob
        assert needle in blob, f"refusal does not name the stale claim:\n{blob}"
        assert "FAIL " not in blob, f"controls ran on a refused tree:\n{blob}"
        assert "PASS " not in blob, f"controls ran on a refused tree:\n{blob}"
        assert not INJECT.exists(), "refusal path wrote the injection file"
        assert _porcelain() - before == {f" M {rel}"}, \
            "refusal path changed the tree beyond the staged edit"
    finally:
        f.write_text(orig, encoding="utf-8")


def test_refused_baseline_claims_also_refuse():
    """A baseline that is REFUSED (rc!=0, no stale) must refuse too: the
    precondition is 'baseline green', not 'baseline merely not stale'."""
    readme = ROOT / "README.md"
    orig = readme.read_text(encoding="utf-8")
    bad_claim = ("\n<!-- claim: grep=\"_905_refused_baseline_probe\" "
                 "in=tools/does_not_exist_905/*.py -->\n")
    before = _porcelain()
    try:
        readme.write_text(orig + bad_claim, encoding="utf-8")
        p = _run_probe()
        blob = p.stdout + p.stderr
        assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
        assert "REFUSED: baseline not green before injection" in blob
        assert "FAIL " not in blob and "PASS " not in blob
        assert not INJECT.exists()
        assert _porcelain() - before == {" M README.md"}
    finally:
        readme.write_text(orig, encoding="utf-8")


def test_clean_tree_still_runs_the_four_controls():
    """AC 1: on a clean tree the probe is unchanged -- rc=0, four PASS.
    Skips on a dirty tree (the refusal is the correct answer there, and this
    test would be measuring the refusal, not the clean path)."""
    if _porcelain():
        pytest.skip("tree is dirty; the dirty-tree refusal tests cover this state")
    p = _run_probe()
    blob = p.stdout + p.stderr
    assert p.returncode == 0, blob
    assert blob.count("PASS ") == 4, blob
    assert "FAIL " not in blob, blob


def test_missing_interpreter_still_refuses_before_anything_else():
    """The pre-existing interpreter() refusal must still win: without pytest+yaml
    the checker prints hundreds of REFUSED claims that read like failures."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import check_doc_claims_789_marker_controls as probe_mod
    monkey = pytest.MonkeyPatch()
    monkey.setattr(probe_mod, "CANDIDATES",
                   [ROOT / ".venv/does_not_exist_905/bin/python"])
    with pytest.raises(SystemExit) as exc:
        probe_mod.interpreter()
    assert exc.value.code == 2
