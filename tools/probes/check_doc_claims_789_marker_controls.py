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
renamed would silently no-op and report a FAIL of its own. Every refusal --
baseline, pre-existing injection file, missing injection target -- is checked
BEFORE the first injection lands, so exit 2 never shares output with a
control verdict: no evidence, not partial evidence.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCS = ["docs/milestones.md", "README.md"]
# The checker needs an interpreter that can collect pytest. The host python
# cannot, and reports 102 REFUSED claims that read exactly like failures, so
# the interpreter is asserted here rather than assumed.
CANDIDATES = [ROOT / ".venv/bin/python", pathlib.Path(sys.executable)]


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
    ms = ROOT / "docs/milestones.md"
    orig_ms = ms.read_text(encoding="utf-8")
    staged_ms = inject_or_refuse(
        orig_ms,
        'grep="^module drum_kit" in=rtl-sketch/drum_kit.v',
        'grep="^module drum_kit_NOT_IN_TREE" '
        'in=rtl-sketch/drum_kit.v',
        "the milestone-3 control")
    if staged_ms is None:
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
