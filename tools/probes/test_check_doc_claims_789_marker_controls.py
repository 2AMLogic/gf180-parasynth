"""#905: the #789 marker probe must REFUSE a dirty tree, not misattribute its
controls.

`tools/probes/check_doc_claims_789_marker_controls.py` injects defects into
the tree it lives in and expects `tools/check_doc_claims.py` to be green
between injections. On a tree with uncommitted WIP that already makes one of
the 17 claims stale, the injected-defect controls cannot be interpreted -- the
checker is red for the tree's own reasons before anything is injected -- and
the probe used to print `FAIL` for two of its four controls, indistinguishable
from a guard that stopped guarding. Per docs/verification-rules.md and
CLAUDE.md, an instrument in a wrong state REFUSES (exit 2, no evidence)
instead of reporting.

These tests run the probe against a SCRATCH COPY of the committed tree
(`git archive HEAD`), never the live checkout. The first draft staged the
dirty tree in the live checkout for the duration of a probe subprocess, which
collided with the concurrent `tools/check_doc_claims.py` job inside `make
verify`'s single run_all.py pool: the checker read the harness's staged edit
and reported `STALE` -- the same misattribution class this probe removes,
reintroduced one layer up (Judge finding, PR #913). Staging the dirty tree in
the copy makes the suite safe to run in parallel with anything in this
checkout, and `git archive HEAD` makes the clean-tree case clean by
construction instead of skipped on a dirty live tree.
"""
import hashlib
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PROBE_REL = pathlib.Path("tools/probes/check_doc_claims_789_marker_controls.py")
INJECT_REL = pathlib.Path("fpga/rtl/_ctl789_inject.v")

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


def _snapshot(root: pathlib.Path) -> dict[str, str]:
    """Content hash of every file in the tree, for exact after-state checks.

    The scratch copy has no .git, so git status cannot describe its state;
    hashing does, and equality here is stronger: nothing in the tree changed
    at all, not merely "nothing git would print".
    """
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


@pytest.fixture(scope="session")
def _archive_bytes() -> bytes:
    """The committed tree once, for every scratch copy."""
    return subprocess.run(["git", "archive", "HEAD"], cwd=ROOT,
                          capture_output=True, check=True).stdout


@pytest.fixture
def scratch(_archive_bytes, tmp_path) -> pathlib.Path:
    """A clean copy of the committed tree for the probe to live and inject in."""
    dst = tmp_path / "tree"
    dst.mkdir()
    subprocess.run(["tar", "-x", "-C", str(dst)], input=_archive_bytes,
                   capture_output=True, check=True)
    return dst


def _run_probe(dst: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(dst / PROBE_REL)], cwd=dst,
                          capture_output=True, text=True)


@pytest.mark.parametrize("rel,old,new,needle", DIRTY_CASES,
                         ids=[c[0] for c in DIRTY_CASES])
def test_dirty_tree_refuses_instead_of_reporting_control_failures(
        scratch, rel, old, new, needle):
    f = scratch / rel
    orig = f.read_text(encoding="utf-8")
    assert old in orig, f"staging anchor not found in {rel}"
    f.write_text(orig.replace(old, new), encoding="utf-8")
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
    assert "REFUSED: baseline not green before injection" in blob
    assert needle in blob, f"refusal does not name the stale claim:\n{blob}"
    assert "FAIL " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert "PASS " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert not (scratch / INJECT_REL).exists(), "refusal path wrote the injection file"
    assert _snapshot(scratch) == before, "refusal path changed the tree"


def test_refused_baseline_claims_also_refuse(scratch):
    """A baseline that is REFUSED (rc!=0, no stale) must refuse too: the
    precondition is 'baseline green', not 'baseline merely not stale'."""
    readme = scratch / "README.md"
    orig = readme.read_text(encoding="utf-8")
    bad_claim = ("\n<!-- claim: grep=\"_905_refused_baseline_probe\" "
                 "in=tools/does_not_exist_905/*.py -->\n")
    readme.write_text(orig + bad_claim, encoding="utf-8")
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
    assert "REFUSED: baseline not green before injection" in blob
    assert "FAIL " not in blob and "PASS " not in blob
    assert not (scratch / INJECT_REL).exists()
    assert _snapshot(scratch) == before, "refusal path changed the tree"


def test_clean_tree_still_runs_the_four_controls(scratch):
    """AC 1: on a clean tree the probe is unchanged -- rc=0, four PASS, and
    the tree is exactly as the probe found it. The archive copy is clean by
    construction, so this runs whatever the live checkout's WIP is."""
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 0, blob
    assert blob.count("PASS ") == 4, blob
    assert "FAIL " not in blob, blob
    assert _snapshot(scratch) == before, "clean run left the tree changed"


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
