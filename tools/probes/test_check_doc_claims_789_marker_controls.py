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
(`git archive HEAD`), never the live checkout. Mid-merge the fixture REFUSES
instead (#964): `git merge --no-commit` leaves HEAD at the pre-merge parent,
so the archive would silently lack the merge -- a merged-tree measurement
needs a merge commit, so commit the merge and then run this suite. The first
draft staged the
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
import io
import pathlib
import re
import subprocess
import sys
import tarfile

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
    # (claim's doc, unique content anchor locating the claim line, in= old,
    #  in= new, evidence file copied, the copy's path, line appended to the
    #  copy, needle naming the pre-satisfied state, needle naming the control)
    # The anchor is the claim's note= prose: `in=manifests/verdict.txt` alone
    # matches three claims, and the count's grep= is what the claim tracks
    # (2/11 goes red by design when a third item lands). Content, not a line
    # number: a hard-coded index broke this suite on every doc edit that
    # inserted a marker above it (#904, #920/#927, #957, #960, #787).
    ("README.md", 'note="the count this sentence quotes',
     "in=manifests/verdict.txt", "in=manifests/verdict_copy.txt",
     "manifests/verdict.txt", "manifests/verdict_copy.txt",
     "T1: 3/11 items met\n",
     "T1: 3/11 items met", "the README T1 count control"),
]

# #936, sibling shape: repointing the milestones drum_kit claim's (the
# `in=rtl-sketch/drum_kit.v` marker) in= defeats control 3's injection by a
# different door -- the find/replace string carries BOTH halves of the claim,
# so the target stops matching and inject_or_refuse (#913) refuses. Covered on
# main by mechanism; pinned here so it stays covered. The claim is located by
# its unique in= content, not a line number: a hard-coded index broke this
# suite on every doc edit that inserted a marker above it (#904, #920/#927,
# #957, #960, #787's nightly-scheduling claim).
IN_DIVERGENCE_TARGET_MISSES = [
    ("docs/milestones.md", "in=rtl-sketch/drum_kit.v",
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
# History of the drum_kit.v staging choice. The append staging this table
# originally carried passed at #920/#927's own tips but broke on their merge
# with #904 (4c0c93bb) -- a second `^module drum_kit` line makes claim 62
# (grep="^module drum_kit") multi-match, the checker REFUSES the tree, and
# the probe honestly refused at the BASELINE (exit 2) instead of reaching
# already_satisfied, leaving main's suite red in this file since the two
# landed. Swap (renaming the declaration in place) fixed that for the
# unanchored claim -- and then broke the same way against #960 (the #959
# repair's anchored form): each PR green alone, red together on the merge
# (measured at the #957 review: 2 failed, both drum_kit.v ids), because a
# renamed line unmatches `^module drum_kit #\(` and no fixed choice opens
# both doors. how="derive" ends the sequence: the staging is read off the
# claim's ACTUAL committed grep= at run time (_derive_how) -- the pattern
# matching the candidate line means append would add a second claim hit
# (swap); not matching means swap would unmatch the only one (append). The
# verdict.txt row keeps its hard-coded append on purpose: three claims read
# that file, so no single claim's form decides its staging, and no colliding
# PR touches them.
ALREADY_SATISFIED_CASES = [
    ("manifests/verdict.txt", "append", "",
     "T1: 3/11 items met\n",
     "T1: 3/11 items met",
     "the README T1 count control"),
    ("rtl-sketch/drum_kit.v", "derive", "module drum_kit #(",
     "module drum_kit_NOT_IN_TREE #(",
     "drum_kit_NOT_IN_TREE",
     "the milestone-3 control"),
    # #927: the injected claim is an UNANCHORED PREFIX match, so a module
    # whose name merely STARTS WITH drum_kit_NOT_IN_TREE pre-satisfies it
    # too. The first draft's guard pattern carried a trailing \b and stayed
    # silent on exactly this state (red-first at 17fbfe45: the control
    # reported `FAIL ...: exit=0`, rc=1, baseline green).
    ("rtl-sketch/drum_kit.v", "derive", "module drum_kit #(",
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


def _archive_root_bytes(root: pathlib.Path) -> bytes:
    """`git archive HEAD` of `root`, refusing when a merge is mid-flight.

    #964: mid-merge (`git merge --no-commit`, MERGE_HEAD present) HEAD is
    the PRE-merge parent -- the archive silently snapshots a tree without
    the merge and every control run on it is a green that is not evidence.
    Per docs/verification-rules.md an instrument in a wrong state refuses
    rather than reports: a merged-tree measurement needs a merge COMMIT, so
    commit the merge and re-run the suite. The check goes through
    `git rev-parse --git-path MERGE_HEAD`, which resolves the per-worktree
    path (also where `.git` is a file) instead of a hard-coded `.git`.
    Deliberately NOT refused: a rebase or cherry-pick in progress -- there
    HEAD names a real commit whose archive is what it says, unlike the
    mid-merge split between the worktree content and HEAD's tree.
    """
    probe = subprocess.run(["git", "rev-parse", "--git-path", "MERGE_HEAD"],
                           cwd=root, capture_output=True, text=True)
    if probe.returncode == 0:
        merge_head = pathlib.Path(probe.stdout.strip())
        if not merge_head.is_absolute():
            merge_head = root / merge_head
        if merge_head.exists():
            pytest.fail("REFUSED: merge in progress; HEAD is pre-merge, "
                        "commit the merge first")
    return subprocess.run(["git", "archive", "HEAD"], cwd=root,
                          capture_output=True, check=True).stdout


@pytest.fixture(scope="session")
def _archive_bytes() -> bytes:
    """The committed tree once, for every scratch copy."""
    return _archive_root_bytes(ROOT)


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


def _derive_how(scratch: pathlib.Path, rel: str, candidate: str) -> str:
    """append-vs-swap from the injected claim's ACTUAL committed grep= (#959).

    The table used to hard-code swap -- a hand-written assumption about claim
    62's form, the same two-copies defect #936 removed from the guard, one
    level further out. Measured at the #957 review (and reproduced here on
    the merged tree): #957 and #960 were each green alone, red together --
    claim 62 unanchored needs swap (an appended `module drum_kit_NOT_IN_TREE`
    line is a SECOND `^module drum_kit` hit and the checker refuses the
    tree), #960's anchored `^module drum_kit #\\(` needs append (a swap
    unmatches the only hit and the baseline refuses STALE), and no fixed
    choice opens both doors. Read the claim the milestone-3 control's
    injection targets off the SCRATCH tree -- with the checker's own grammar,
    via the probe module's injected_state, the same derivation
    already_satisfied itself uses -- and apply its pattern to the candidate
    line: a match means append would add a second claim hit (swap); no match
    means swap would unmatch the only one (append). injected_state refuses on
    zero-or-multiple claims, so a second rtl-sketch/drum_kit.v claim fails
    here loudly instead of guessing.
    """
    sys.path.insert(0, str(ROOT / "tools" / "probes"))
    import check_doc_claims_789_marker_controls as probe_mod
    doc = (scratch / "docs/milestones.md").read_text(encoding="utf-8")
    derived = probe_mod.injected_state(doc, f"in={rel}",
                                       f"the {rel} staging decision")
    assert derived is not None, (
        f"could not derive the staging for {rel} from docs/milestones.md "
        "(the REFUSED detail above names why)")
    _, pattern = derived
    return "swap" if re.search(pattern, candidate) else "append"


def _stage_already_satisfied(scratch: pathlib.Path, rel: str, how: str,
                             find: str, staged: str) -> None:
    """Write the pre-satisfied state into the scratch copy.

    how='append' adds the line at end-of-file (a newline is supplied when the
    staged text lacks one -- the drum_kit rows' text doubles as the swap
    replacement, which must stay newline-free); how='swap' replaces `find` in
    place; how='derive' asks the claim's own grep= which of the two keeps it
    single-match (_derive_how).
    """
    f = scratch / rel
    orig = f.read_text(encoding="utf-8")
    if how == "derive":
        how = _derive_how(scratch, rel, staged)
    if how == "append":
        f.write_text(orig + (staged if staged.endswith("\n")
                             else staged + "\n"), encoding="utf-8")
    else:
        assert find and find in orig, f"swap anchor not found in {rel}"
        f.write_text(orig.replace(find, staged), encoding="utf-8")


def _anchor_drum_kit_claim_as_960_does(scratch: pathlib.Path) -> None:
    """Recreate PR #960's two edits on the SCRATCH copy: the anchored doc
    claim and the matching anchored find string in the probe (#960's diff
    touches nothing else this suite reads). The #957+#960 merged tree is
    exactly this state, so the anchored-form tests below are the collision
    in miniature, measurable on this tree whether or not #960 ever lands.
    Idempotent: on a tree where #960 already landed (the merged
    configuration) both forms are already anchored and this is a no-op --
    anything else fails the asserts loudly rather than staging a third
    claim form. The claim line is found by its unique
    `in=rtl-sketch/drum_kit.v` content, not an index: a hard-coded
    `lines[61]` broke on every doc edit inserting a marker above it
    (#904, #920/#927, #957, #960, #787's nightly-scheduling claim)."""
    doc = scratch / "docs/milestones.md"
    lines = doc.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, ln in enumerate(lines)
            if "in=rtl-sketch/drum_kit.v" in ln]
    assert len(hits) == 1, \
        f"drum_kit claim matches {len(hits)} lines, expected 1"
    i = hits[0]
    if 'grep="^module drum_kit" in=' in lines[i]:
        lines[i] = lines[i].replace('grep="^module drum_kit" in=',
                                    'grep="^module drum_kit #\\(" in=')
        doc.write_text("".join(lines), encoding="utf-8")
    else:
        assert 'grep="^module drum_kit #\\(" in=' in lines[i], \
            "drum_kit claim anchor not found (neither form)"
    probe = scratch / PROBE_REL
    text = probe.read_text(encoding="utf-8")
    old = "'grep=\"^module drum_kit\" in=rtl-sketch/drum_kit.v'"
    new = "'grep=\"^module drum_kit #\\\\(\" in=rtl-sketch/drum_kit.v'"
    if old in text:
        probe.write_text(text.replace(old, new), encoding="utf-8")
    else:
        assert new in text, "probe find-string anchor not found (neither form)"


def _assert_refusal(before: dict[str, str], p: subprocess.CompletedProcess,
                    blob: str, scratch: pathlib.Path, rel: str, needle: str,
                    what: str) -> None:
    """The already-satisfied refusal contract, shared by both claim forms:
    exit 2 naming the file, the state and the control, no control lines, no
    injection file, tree byte-identical."""
    assert p.returncode == 2, f"expected REFUSED rc=2, got {p.returncode}:\n{blob}"
    assert "REFUSED:" in blob, blob
    assert rel in blob, f"refusal does not name the file:\n{blob}"
    assert needle in blob, f"refusal does not name the already-satisfied state:\n{blob}"
    assert what in blob, f"refusal does not name the control:\n{blob}"
    assert "FAIL " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert "PASS " not in blob, f"controls ran on a refused tree:\n{blob}"
    assert not (scratch / INJECT_REL).exists(), "refusal path wrote the injection file"
    assert _snapshot(scratch) == before, "refusal path changed the tree"


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
    report. The staging keeps every claim green (for the drum_kit rows the
    choice is DERIVED from the claim's own grep= at run time -- swap while
    the claim is unanchored, append under #960's anchored form -- so the
    injected state pre-exists without unmatching or duplicating claim 62)
    and the injection target still exists, so neither #913 guard fires and
    the control's marker has nothing to fire at -- the probe used to answer
    `FAIL ...: exit=0` for that control and rc=1 overall, a guard that
    "stopped guarding" when it had nothing to guard against. The probe must
    REFUSE pre-injection: exit 2, the refusal names the file and the state,
    no control lines, tree byte-identical."""
    _stage_already_satisfied(scratch, rel, how, find, staged)
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    _assert_refusal(before, p, p.stdout + p.stderr, scratch, rel, needle, what)


@pytest.mark.parametrize("staged", [
    "module drum_kit_NOT_IN_TREE #(",
    "module drum_kit_NOT_IN_TREE_X #(",
], ids=["rtl-sketch/drum_kit.v0", "rtl-sketch/drum_kit.v1"])
def test_already_satisfied_staging_survives_an_anchored_claim_form(
        scratch, staged):
    """#959, the collision in miniature. #960 anchors claim 62 to
    `^module drum_kit #\\(`, and under that form the hard-coded swap staging
    renamed drum_kit.v's only matching line -- the baseline refused STALE and
    the already-satisfied guard was never reached (measured on the #957+#960
    merged tree: 2 failed, both drum_kit.v ids, `refusal does not name the
    already-satisfied state`). Red first in exactly that configuration. The
    derived staging picks append -- the anchored pattern does not match the
    candidate line, so a swap would unmatch the only claim hit -- the tree
    stays green, and the refusal must still name the file, the state and the
    control. Pins this tree against the anchored form whether or not #960
    ever lands."""
    _anchor_drum_kit_claim_as_960_does(scratch)
    _stage_already_satisfied(scratch, "rtl-sketch/drum_kit.v", "derive",
                             "module drum_kit #(", staged)
    before = _snapshot(scratch)
    p = _run_probe(scratch)
    blob = p.stdout + p.stderr
    assert "REFUSED: baseline not green" not in blob, blob
    _assert_refusal(before, p, blob, scratch, "rtl-sketch/drum_kit.v",
                    "drum_kit_NOT_IN_TREE", "the milestone-3 control")


def _repoint_in(scratch: pathlib.Path, doc: str, anchor: str, old: str,
                new: str) -> None:
    """Repoint ONE claim marker's in= (content-targeted: the same in= string
    appears on several claims in these documents, so the line is found by its
    unique `anchor` substring and asserted exactly one -- not a line number,
    which drifts on every doc edit inserting a marker above it)."""
    f = scratch / doc
    lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, ln in enumerate(lines) if anchor in ln]
    assert len(hits) == 1, \
        f"anchor {anchor!r} matches {len(hits)} lines in {doc}, expected 1"
    assert old in lines[hits[0]], f"in= anchor not on {doc}:{hits[0] + 1}"
    lines[hits[0]] = lines[hits[0]].replace(old, new)
    f.write_text("".join(lines), encoding="utf-8")


@pytest.mark.parametrize(
    "doc,anchor,old,new,src_rel,dst_rel,extra,needle,what",
    IN_DIVERGENCE_PRESATISFIED,
    ids=[c[0] for c in IN_DIVERGENCE_PRESATISFIED])
def test_in_repointed_presatisfied_state_refuses(
        scratch, doc, anchor, old, new, src_rel, dst_rel, extra, needle, what):
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
    _repoint_in(scratch, doc, anchor, old, new)
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
    "doc,anchor,old,new,src_rel,dst_rel,extra,needle,what",
    IN_DIVERGENCE_TARGET_MISSES,
    ids=[c[0] for c in IN_DIVERGENCE_TARGET_MISSES])
def test_in_repointed_defeats_injection_target_refuses(
        scratch, doc, anchor, old, new, src_rel, dst_rel, extra, needle, what):
    """#936, sibling shape: control 3's find string carries BOTH halves of the
    claim, so an edited in= stops the injection from matching and #913's
    missing-target refusal fires (rc=2, no control lines). Pinned so the
    mechanism stays covered."""
    (scratch / dst_rel).write_text(
        (scratch / src_rel).read_text(encoding="utf-8") + extra,
        encoding="utf-8")
    _repoint_in(scratch, doc, anchor, old, new)
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
    _repoint_in(scratch, "README.md", 'note="the count this sentence quotes',
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


# ---- #964: the archive engine must refuse a mid-merge tree ----


def _git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                   check=True)


def _scratch_repo(path: pathlib.Path) -> pathlib.Path:
    """A throwaway repo with one commit on main, git-configured locally."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "probe@example.invalid", cwd=path)
    _git("config", "user.name", "probe", cwd=path)
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", "base.txt", cwd=path)
    _git("commit", "-m", "base", cwd=path)
    return path


def _diverge(repo: pathlib.Path) -> None:
    """One commit on `side`, one on `main`, both off the base commit."""
    _git("checkout", "-b", "side", cwd=repo)
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    _git("add", "side.txt", cwd=repo)
    _git("commit", "-m", "side", cwd=repo)
    _git("checkout", "main", cwd=repo)
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _git("add", "main.txt", cwd=repo)
    _git("commit", "-m", "main", cwd=repo)


def _merge_no_commit(repo: pathlib.Path, other: str = "side") -> None:
    _git("merge", "--no-commit", "--no-ff", other, cwd=repo)


def _tar_names(archive: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
        return tf.getnames()


def test_archive_refuses_mid_merge_head_is_pre_merge(tmp_path):
    """#964, the injected defect: mid `git merge --no-commit` the worktree
    holds the merge but HEAD is the pre-merge parent, so `git archive HEAD`
    silently snapshots a tree WITHOUT the merged content and every control
    on that snapshot is a green that is not evidence. The premise is shown,
    not assumed -- the raw archive lacks side.txt -- and then the fixture
    engine must REFUSE instead of handing that snapshot to the suite."""
    repo = _scratch_repo(tmp_path / "repo")
    _diverge(repo)
    _merge_no_commit(repo)
    bare = subprocess.run(["git", "archive", "HEAD"], cwd=repo,
                          capture_output=True, check=True).stdout
    assert "side.txt" not in _tar_names(bare), (
        "premise drifted: the mid-merge archive now contains the merge")
    with pytest.raises(pytest.fail.Exception) as exc:
        _archive_root_bytes(repo)
    assert "REFUSED" in str(exc.value)


def test_archive_allows_a_committed_merge(tmp_path):
    """#964 control: the same merge, COMMITTED, does not refuse -- and the
    archive then contains the merge -- so the gate is satisfiable exactly
    when its premise is fixed."""
    repo = _scratch_repo(tmp_path / "repo")
    _diverge(repo)
    _merge_no_commit(repo)
    _git("commit", "--no-edit", cwd=repo)
    assert "side.txt" in _tar_names(_archive_root_bytes(repo))


def test_archive_allows_a_plain_clean_tree(tmp_path):
    """#964 control: an ordinary committed tree with no merge anywhere does
    not refuse. The rest of this module, running on the current checkout,
    pins the same thing end to end; this pins the gate alone."""
    repo = _scratch_repo(tmp_path / "plain")
    assert isinstance(_archive_root_bytes(repo), bytes)


def test_archive_refuses_mid_merge_in_a_linked_worktree(tmp_path):
    """#964 AC edge: `.git` is a FILE in a linked worktree, so the guard
    must reach MERGE_HEAD through `git rev-parse --git-path` (which
    resolves the per-worktree path) rather than a hard-coded
    `.git/MERGE_HEAD`. The merge is mid-flight in the worktree only --
    MERGE_HEAD is per-worktree state -- so the worktree refuses while the
    main repo of the same throwaway repo keeps answering."""
    repo = _scratch_repo(tmp_path / "repo")
    _diverge(repo)
    wt = tmp_path / "wt"
    _git("worktree", "add", str(wt), "side", cwd=repo)
    _merge_no_commit(wt, other="main")
    with pytest.raises(pytest.fail.Exception) as exc:
        _archive_root_bytes(wt)
    assert "REFUSED" in str(exc.value)
    assert isinstance(_archive_root_bytes(repo), bytes), (
        "the guard leaked across the worktree boundary")
