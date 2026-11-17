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

# #936: the probe's already_satisfied guard used to read a SECOND, hand-written
# copy of the injected claim's in= path, so repointing the claim marker's own
# in= left the guard reading the old file while the claim read the new one.
# With the copy pre-satisfying the injected state the baseline stays green (it
# still carries the line the claim quotes) and the injection target still
# exists, so neither #913 nor #920 fires -- and the probe reported a
# misattributed `FAIL  README T1 count marker goes red on a wrong count:
# exit=0`, rc=1, for a guard that had silently stopped reading the claim's
# file. Measured before the fix on a git archive scratch tree.
IN_DIVERGENCE_PRESATISFIED = [
    # (claim's doc, 1-based line of the claim, in= old, in= new,
    #  evidence file copied, the copy's path, line appended to the copy,
    #  needle naming the pre-satisfied state, needle naming the control)
    ("README.md", 28,
     "in=manifests/verdict.txt", "in=manifests/verdict_copy.txt",
     "manifests/verdict.txt", "manifests/verdict_copy.txt",
     "T1: 3/11 items met\n",
     "T1: 3/11 items met", "the README T1 count control"),
]

# #936, sibling shape: repointing docs/milestones.md:62's in= defeats control 3's
# injection by a different door -- the find/replace string carries BOTH halves
# of the claim, so the target stops matching and inject_or_refuse (#913)
# refuses. Covered on main by mechanism; pinned here so it stays covered.
IN_DIVERGENCE_TARGET_MISSES = [
    ("docs/milestones.md", 62,
     "in=rtl-sketch/drum_kit.v", "in=rtl-sketch/drum_kit_copy.v",
     "rtl-sketch/drum_kit.v", "rtl-sketch/drum_kit_copy.v", "",
     "injection target", "the milestone-3 control"),
]

# (file, how to stage, find text for swap staging, staged text, needle naming
# the injected state the tree already satisfies, the control). #920: neither
# #913 guard fires here -- the baseline stays green and the injection target
# still exists -- so the control's marker has nothing to fire at and the probe
# used to report a misattributed FAIL instead of refusing.
#
# The drum_kit.v cases stage by REPLACING the module declaration in place, not
# appending a duplicate. Found during #936: the append staging this table
# originally carried passed at #920/#927's own tips but broke on their merge
# with #904 (4c0c93bb) -- a second `^module drum_kit` line makes claim 62
# (grep="^module drum_kit") multi-match, the checker REFUSES the tree, and the
# probe honestly refused at the BASELINE (exit 2) instead of reaching
# already_satisfied, leaving main's suite red in this file since the two
# landed. The renamed module keeps claim 62 single-match (its line still
# prefix-matches `^module drum_kit`) while pre-satisfying the injected claim
# -- the same state, staged through a door #904 does not close.
ALREADY_SATISFIED_CASES = [
    ("manifests/verdict.txt", "append", "",
     "T1: 3/11 items met\n",
     "T1: 3/11 items met",
     "the README T1 count control"),
    ("rtl-sketch/drum_kit.v", "swap", "module drum_kit #(",
     "module drum_kit_NOT_IN_TREE #(",
     "drum_kit_NOT_IN_TREE",
     "the milestone-3 control"),
    # #927: the injected claim is an UNANCHORED PREFIX match, so a module
    # whose name merely STARTS WITH drum_kit_NOT_IN_TREE pre-satisfies it
    # too. The first draft's guard pattern carried a trailing \b and stayed
    # silent on exactly this state (red-first at 17fbfe45: the control
    # reported `FAIL ...: exit=0`, rc=1, baseline green).
    ("rtl-sketch/drum_kit.v", "swap", "module drum_kit #(",
     "module drum_kit_NOT_IN_TREE_X #(",
     "drum_kit_NOT_IN_TREE",
     "the milestone-3 control"),
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


@pytest.mark.parametrize("rel,how,find,staged,needle,what",
                         ALREADY_SATISFIED_CASES,
                         ids=[c[0] for c in ALREADY_SATISFIED_CASES])
def test_already_satisfied_injection_refuses(scratch, rel, how, find, staged,
                                             needle, what):
    """#920: an injected state the tree ALREADY satisfies must refuse, not
    report. The staging keeps every claim green (for swap staging the renamed
    module still prefix-matches claim 62, single-hit) and the injection target
    still exists, so neither #913 guard fires and the control's marker has
    nothing to fire at -- the probe used to answer `FAIL ...: exit=0` for that
    control and rc=1 overall, a guard that "stopped guarding" when it had
    nothing to guard against. The probe must REFUSE pre-injection: exit 2, the
    refusal names the file and the state, no control lines, tree
    byte-identical."""
    f = scratch / rel
    orig = f.read_text(encoding="utf-8")
    if how == "append":
        f.write_text(orig + staged, encoding="utf-8")
    else:
        assert find and find in orig, f"swap anchor not found in {rel}"
        f.write_text(orig.replace(find, staged), encoding="utf-8")
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
    assert "REFUSED:" in blob, blob
    assert rel in blob, f"refusal does not name the file:\n{blob}"
    assert needle in blob, f"refusal does not name the already-satisfied state:\n{blob}"
    assert what in blob, f"refusal does not name the control:\n{blob}"
    assert "FAIL " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert "PASS " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert not (scratch / INJECT_REL).exists(), "refusal path wrote the injection file"
    assert _snapshot(scratch) == before, "refusal path changed the tree"


def _repoint_in(scratch: pathlib.Path, doc: str, lineno: int, old: str,
                new: str) -> None:
    """Repoint ONE claim marker's in= (line-targeted: the same in= string
    appears on several claims in these documents)."""
    f = scratch / doc
    lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
    assert old in lines[lineno - 1], f"in= anchor not on {doc}:{lineno}"
    lines[lineno - 1] = lines[lineno - 1].replace(old, new)
    f.write_text("".join(lines), encoding="utf-8")


@pytest.mark.parametrize(
    "doc,lineno,old,new,src_rel,dst_rel,extra,needle,what",
    IN_DIVERGENCE_PRESATISFIED,
    ids=[c[0] for c in IN_DIVERGENCE_PRESATISFIED])
def test_in_repointed_presatisfied_state_refuses(
        scratch, doc, lineno, old, new, src_rel, dst_rel, extra, needle, what):
    """#936, red-first: the issue's measured repro. Copy the evidence file,
    append the injected line to the copy (the claim still matches the copy, so
    the baseline is green), repoint the claim's in= at the copy. The guard used
    to read the ORIGINAL file, found no pre-satisfaction, ran the control, and
    the marker had nothing to fire at in the file the claim ACTUALLY greps:
    `FAIL ... exit=0`, rc=1 -- a guard that works misread as one that stopped
    guarding. The guard must be derived from the staged injection and REFUSE:
    exit 2, naming the file the claim now reads, the state, and the control,
    with no control lines and a byte-identical tree."""
    (scratch / dst_rel).write_text(
        (scratch / src_rel).read_text(encoding="utf-8") + extra,
        encoding="utf-8")
    _repoint_in(scratch, doc, lineno, old, new)
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
    assert "REFUSED:" in blob, blob
    assert dst_rel in blob, f"refusal does not name the file the claim reads:\n{blob}"
    assert needle in blob, f"refusal does not name the pre-satisfied state:\n{blob}"
    assert what in blob, f"refusal does not name the control:\n{blob}"
    assert "FAIL " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert "PASS " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert not (scratch / INJECT_REL).exists(), "refusal path wrote the injection file"
    assert _snapshot(scratch) == before, "refusal path changed the tree"


@pytest.mark.parametrize(
    "doc,lineno,old,new,src_rel,dst_rel,extra,needle,what",
    IN_DIVERGENCE_TARGET_MISSES,
    ids=[c[0] for c in IN_DIVERGENCE_TARGET_MISSES])
def test_in_repointed_defeats_injection_target_refuses(
        scratch, doc, lineno, old, new, src_rel, dst_rel, extra, needle, what):
    """#936, sibling shape: control 3's find string carries BOTH halves of the
    claim, so an edited in= stops the injection from matching and #913's
    missing-target refusal fires (rc=2, no control lines). Pinned so the
    mechanism stays covered."""
    (scratch / dst_rel).write_text(
        (scratch / src_rel).read_text(encoding="utf-8") + extra,
        encoding="utf-8")
    _repoint_in(scratch, doc, lineno, old, new)
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
    assert "REFUSED:" in blob, blob
    assert needle in blob, f"refusal does not name the missed target:\n{blob}"
    assert what in blob, f"refusal does not name the control:\n{blob}"
    assert "FAIL " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert "PASS " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert not (scratch / INJECT_REL).exists(), "refusal path wrote the injection file"
    assert _snapshot(scratch) == before, "refusal path changed the tree"


def test_in_repointed_without_presatisfaction_still_measures(scratch):
    """#936, the scoping half: repointing the claim's in= at a copy that does
    NOT pre-satisfy the injected state leaves the control measuring the marker
    on the file the claim actually greps -- and it genuinely goes red there.
    The derived guard must not over-refuse: rc=0, four PASS, tree restored."""
    (scratch / "manifests/verdict_copy.txt").write_text(
        (scratch / "manifests/verdict.txt").read_text(encoding="utf-8"),
        encoding="utf-8")
    _repoint_in(scratch, "README.md", 28,
                "in=manifests/verdict.txt", "in=manifests/verdict_copy.txt")
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert p.returncode == 0, blob
    assert blob.count("PASS ") == 4, blob
    assert "FAIL " not in blob, blob
    assert _snapshot(scratch) == before, "clean run left the tree changed"


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
