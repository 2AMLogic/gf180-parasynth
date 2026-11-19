#!/usr/bin/env python3
"""Can the claim markers issue #789 added actually go RED?

`docs/verification-rules.md` rule 8: a guard ships with the input that defeats
it. #789 brought `README.md` and `docs/milestones.md` under
`tools/check_doc_claims.py` for the first time, and a marker that cannot fail
is not a check -- it is a green light wired to nothing. This is the red-first
record for the three kinds of failure those markers have to be able to see.

The one that matters is control 1. `rtl-sketch/synth_top.v`'s header comment
says, in prose, "drum_section_placeholder is gone" -- so the OBVIOUS marker,
`absent="drum_section_placeholder" in=rtl-sketch/*.v`, is STALE on a tree where
the placeholder is genuinely absent, because the sentence announcing its
absence contains the string. The shipped marker is anchored
(`^\\s*(module\\s+)?drum_section_placeholder`) so that a comment cannot satisfy
it; this probe injects a real instantiation into the `in=` globs and requires
the anchored pattern to catch it, which is the only evidence that the anchoring
narrowed the regex without blinding it.

    python3 tools/probes/check_doc_claims_789_marker_controls.py

Exit 0 every control went red then green again, 1 a control did not fire,
2 the apparatus could not run (per this repository's convention: 2 is NO
EVIDENCE, not NO PROBLEM).

BASELINE PRECONDITION (#905). The controls inject into the live checkout and
expect the checker to be green between injections, so before injecting anything
the probe runs the checker once and REFUSES -- exit 2, naming what it found --
if that baseline is not already green. WIP that makes one of the 17 claims
stale otherwise reads as two FAILing controls (a guard that stopped guarding)
when the guard worked and the tree was merely dirty. The precondition refuses
exactly when the controls would be uninterpretable, not on WIP that leaves all
claims green; that green-WIP state has its own refusal at the injection site
(see "injection target" below), because a control whose replace target the WIP
renamed would silently no-op and report a FAIL of its own. The dual refusal
(#920): a control also REFUSES when the tree ALREADY satisfies the state it
injects -- a hand-appended duplicate `T1: 3/11 items met` line in the
generated verdict manifest, or any module whose name starts with
`drum_kit_NOT_IN_TREE` (#927: the refusal's pattern IS the injected claim's
own pattern, an unanchored prefix match, so a prefixed name like
`drum_kit_NOT_IN_TREE_X` refuses too) -- because then the marker has nothing
to fire at and "the control failed" is indistinguishable from "the tree was
already wrong" (see "already satisfied" below). Every refusal -- baseline,
pre-existing injection file, missing injection target, already-satisfied
injection state -- is
checked BEFORE the first injection lands, so exit 2 never shares output with
a control verdict: no evidence, not partial evidence.

#936: `already_satisfied` no longer takes a hand-written copy of the claim's
grep= pattern and in= path. Both are parsed out of the staged injection with
the checker's own grammar (`tools/check_doc_claims.py`: iter_claim_markers,
parse_attrs, expand -- #962 replaced this probe's hand-copied fence-only
scan with the shared generator), so repointing a claim marker's in= cannot
leave the guard reading a
file the claim no longer reads -- a divergence that measured, before the
fix, as a misattributed `FAIL  README T1 count marker goes red on a wrong
count: exit=0` with rc=1 (baseline green, injection target present, the
injected line pre-satisfied in the file the claim actually grepped).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCS = ["docs/milestones.md", "README.md"]
# The checker needs an interpreter that can collect pytest. The host python
# cannot, and reports 102 REFUSED claims that read exactly like failures, so
# the interpreter is asserted here rather than assumed.
CANDIDATES = [ROOT / ".venv/bin/python", pathlib.Path(sys.executable)]

# The marker grammar is the checker's, not a second copy of it: importing the
# module the probe subprocess-runs keeps parse_attrs and expand() byte-identical
# with what actually grades the claims (#936 -- a mirrored parser is the same
# two-copies-can-diverge defect this probe exists to catch, one level up).
# The import must also leave NO trace in the tree being audited: the scratch
# tests hash every file, and a stray tools/__pycache__/check_doc_claims.pyc
# reads as "the refusal path changed the tree" (measured: 10 red snapshots).
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools"))
import check_doc_claims as checker  # noqa: E402  (needs ROOT's tools/ on path)


def interpreter() -> str:
    for cand in CANDIDATES:
        if not cand.exists():
            continue
        probe = subprocess.run([str(cand), "-c", "import pytest, yaml"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return str(cand)
    print("REFUSED: no interpreter found that can import pytest and yaml; "
          "build the CI environment first (uv venv --python 3.12; "
          "numpy scipy pytest pyyaml)", file=sys.stderr)
    raise SystemExit(2)


def run(py: str) -> tuple[int, str]:
    p = subprocess.run([py, "tools/check_doc_claims.py", *DOCS],
                       cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def expect_red(py: str, name: str, needle: str) -> bool:
    rc, out = run(py)
    hits = [l.strip() for l in out.splitlines() if l.startswith("STALE") and needle in l]
    ok = rc != 0 and bool(hits)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: exit={rc}")
    for line in hits:
        print(f"        {line}")
    return ok


def baseline_refused(py: str) -> bool:
    """REFUSE unless the checkout's claims are green BEFORE any injection (#905).

    Returns True -- after printing the refusal to stderr -- when the baseline
    is NOT green, and False (printing nothing) when it is green, so the call
    site reads `if baseline_refused(py): return 2`. A control run on a
    not-green tree would report the tree's own staleness as a control
    failure, which is exactly the misattribution #905 exists for.
    """
    rc, out = run(py)
    if rc == 0 and "0 stale, 0 refused" in out:
        return False
    reasons = [l.strip() for l in out.splitlines()
               if l.startswith("STALE") or "REFUSED" in l][:10]
    print("REFUSED: baseline not green before injection (checker exit="
          f"{rc}); the four controls below would misreport this tree's own "
          "staleness as a control failure. Commit or revert the WIP first.",
          file=sys.stderr)
    for line in reasons:
        print(f"        {line}", file=sys.stderr)
    return True


def inject_or_refuse(before: str, find: str, replace: str, what: str) -> str | None:
    """Stage an injection edit, or refuse if the target text is not there.

    A replace that matches nothing would silently no-op and the control would
    then report a FAIL about the tree rather than about the marker (#905's
    green-WIP corner: WIP that renamed the claim text leaves every claim green,
    so the baseline precondition passes, and this is the only thing that can
    notice). Returns the staged text, or None after printing the refusal.
    """
    staged = before.replace(find, replace)
    if staged == before:
        print(f"REFUSED: injection target {find!r} not found; cannot stage "
              f"{what} on this tree", file=sys.stderr)
        return None
    return staged


def injected_state(staged: str, marker_of: str,
                   what: str) -> tuple[list[pathlib.Path], str] | None:
    """Derive the injected claim's (files, pattern) from the staged text (#936).

    already_satisfied used to be called with a SECOND, hand-written copy of
    the claim's grep= pattern and in= path -- #927 had made the two pattern
    copies byte-identical for control 3, but control 2's in= had no check at
    all, and a hand copy diverges silently. Repoint the claim marker's own
    in= at a file that already contains the injected line and the guard read
    the OLD file, found no pre-satisfaction, ran the control, and the marker
    had nothing to fire at in the file the claim actually greps: a
    misattributed FAIL (rc=1) for a tree that was already wrong -- the exact
    shape #905, #913 and #920 each removed through a different door. So the
    pair is parsed out of the staged injection instead of restated: the
    marker containing `marker_of` (the replace string the injection just
    introduced) is read with the checker's own skip grammar and
    MARKER/parse_attrs (#962: iter_claim_markers, so a marker the checker
    treats as an example -- fenced or inside `backticks` -- is skipped here
    too, instead of a hand-copied fence rule without the backtick rule
    refusing a fine injection), and its in= is expanded with the checker's
    own expand(), so guard and claim cannot disagree about which file
    carries which pattern. Returns None
    after printing a refusal when the staged injection does not yield
    exactly one parseable grep= claim with an expanding in= -- an apparatus
    error, exit 2, no evidence.
    """
    bodies: list[str] = []
    for _, _, m in checker.iter_claim_markers(staged.splitlines()):
        if marker_of in m.group("body"):
            bodies.append(m.group("body"))
    if len(bodies) != 1:
        print(f"REFUSED: expected exactly one injected claim containing "
              f"{marker_of!r} in the staged text, found {len(bodies)}; "
              f"cannot derive {what}'s guard from the injection",
              file=sys.stderr)
        return None
    attrs, err = checker.parse_attrs(bodies[0])
    if err or "grep" not in attrs or "in" not in attrs:
        print(f"REFUSED: the staged injection is not a parseable grep= claim "
              f"with an in= ({err or 'missing grep=/in='}); cannot derive "
              f"{what}'s guard", file=sys.stderr)
        return None
    files, err = checker.expand(attrs["in"])
    if err or not files:
        print(f"REFUSED: the staged injection's in={attrs['in']!r} does not "
              f"expand to files ({err or 'matched no files'}); cannot derive "
              f"{what}'s guard", file=sys.stderr)
        return None
    return files, attrs["grep"]


def already_satisfied(paths: list[pathlib.Path], pattern: str, what: str) -> bool:
    """REFUSE when the tree already satisfies the state a control injects (#920).

    The dual of inject_or_refuse's missing-target refusal. A control injects
    a defect and requires its marker to go red; if the injected state is
    ALREADY true on this tree -- a hand-appended duplicate summary line in
    the generated verdict manifest, a module name the injected claim's own
    pattern matches (#927: pattern equality is the contract; a narrower
    guard here stayed silent on `drum_kit_NOT_IN_TREE_X`) -- the
    marker has nothing to fire at and stays green no matter what the marker
    does, so "the control failed" and "the tree was already wrong" are
    indistinguishable and the control reports the tree as its finding.
    #936: the paths and the pattern are no longer passed as hand-written
    literals -- they are derived from the staged injection (injected_state),
    so the guard always checks the file(s) the injected claim actually
    greps with the pattern it actually carries.
    Checked pre-injection, beside the other refusals, so exit 2 never shares
    output with a control verdict. Returns True after printing the refusal,
    False (printing nothing) when the state is genuinely absent.
    """
    rx = re.compile(pattern)
    matched: list[str] = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        if any(rx.search(line)
               for line in path.read_text(encoding="utf-8").splitlines()):
            matched.append(rel)
    if matched:
        print(f"REFUSED: {', '.join(matched)} already contains a line matching "
              f"{pattern!r}: the state {what} injects pre-exists on this "
              f"tree, so its marker cannot fire and the control would "
              f"misreport the tree as a failure. Remove the pre-existing "
              f"state first.", file=sys.stderr)
        return True
    return False


def main() -> int:
    py = interpreter()
    if baseline_refused(py):
        return 2
    # Every refusal is checked BEFORE the first injection lands, so an exit 2
    # never shares its output with a control verdict: a refusal means no
    # evidence, not partial evidence plus one control's opinion.
    inj = ROOT / "fpga/rtl/_ctl789_inject.v"
    if inj.exists():
        print(f"REFUSED: {inj} already exists; refusing to overwrite", file=sys.stderr)
        return 2
    readme = ROOT / "README.md"
    orig_readme = readme.read_text(encoding="utf-8")
    staged_readme = inject_or_refuse(
        orig_readme, 'grep="^T1: 2/11 items met$"', 'grep="^T1: 3/11 items met$"',
        "the README T1 count control")
    if staged_readme is None:
        return 2
    derived = injected_state(staged_readme, 'grep="^T1: 3/11 items met$"',
                             "the README T1 count control")
    if derived is None:
        return 2
    if already_satisfied(derived[0], derived[1],
                         "the README T1 count control"):
        return 2
    ms = ROOT / "docs/milestones.md"
    orig_ms = ms.read_text(encoding="utf-8")
    # The find string is the claim's ANCHORED form (#959): the unanchored
    # `^module drum_kit` also matched the state this control injects, so the
    # checker refused for non-uniqueness before the already-satisfied guard
    # could fire. Do not widen it back -- the claim in docs/milestones.md and
    # this string must stay the same text (the #936 coupling).
    staged_ms = inject_or_refuse(
        orig_ms,
        'grep="^module drum_kit #\\(" in=rtl-sketch/drum_kit.v',
        'grep="^module drum_kit_NOT_IN_TREE" '
        'in=rtl-sketch/drum_kit.v',
        "the milestone-3 control")
    if staged_ms is None:
        return 2
    # The guard's pattern and paths are DERIVED from the staged injection
    # (#936), so the pattern is by construction the injected claim's own
    # pattern -- an unanchored prefix match, per line (check_grep compiles
    # with re.MULTILINE and rx.search-es each line) -- the property #927
    # first enforced by hand: a trailing \b would let
    # `module drum_kit_NOT_IN_TREE_X` satisfy the claim while this refusal
    # stayed silent (red-first at 17fbfe45: FAIL exit=0, rc=1).
    derived = injected_state(
        staged_ms,
        'grep="^module drum_kit_NOT_IN_TREE" in=rtl-sketch/drum_kit.v',
        "the milestone-3 control")
    if derived is None:
        return 2
    if already_satisfied(derived[0], derived[1],
                         "the milestone-3 control"):
        return 2

    results: list[bool] = []

    # 1. A re-introduced placeholder INSTANTIATION must defeat the anchored
    #    absent= marker -- the defect the marker exists to catch.
    inj.write_text("module _ctl789;\n"
                   "    drum_section_placeholder u_plh (.clk(clk));\n"
                   "endmodule\n", encoding="utf-8")
    try:
        results.append(expect_red(py, "absent= catches a re-introduced instantiation",
                                  "drum_section_placeholder"))
    finally:
        inj.unlink()

    # 2. The README T1 marker must go red the way a THIRD attestation would
    #    make it go red -- the drift this issue repaired, re-armed.
    try:
        readme.write_text(staged_readme, encoding="utf-8")
        results.append(expect_red(py, "README T1 count marker goes red on a wrong count", "3/11"))
    finally:
        readme.write_text(orig_readme, encoding="utf-8")

    # 3. A milestone-3 marker whose cited evidence is absent must go red, not
    #    quietly pass.
    try:
        ms.write_text(staged_ms, encoding="utf-8")
        results.append(expect_red(py, "milestone-3 marker goes red when its evidence is absent",
                                  "drum_kit_NOT_IN_TREE"))
    finally:
        ms.write_text(orig_ms, encoding="utf-8")

    # 4. And the tree is green again: a control that leaves the tree red has
    #    reported a finding about its own mess.
    rc, out = run(py)
    tail = out.strip().splitlines()[-1] if out.strip() else "(no output)"
    clean = rc == 0 and "0 stale, 0 refused" in out
    print(f"{'PASS' if clean else 'FAIL'}  reverted tree is green again: exit={rc}")
    print(f"        {tail}")
    results.append(clean)

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
