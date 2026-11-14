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


def main() -> int:
    py = interpreter()
    results: list[bool] = []

    # 1. A re-introduced placeholder INSTANTIATION must defeat the anchored
    #    absent= marker -- the defect the marker exists to catch.
    inj = ROOT / "fpga/rtl/_ctl789_inject.v"
    if inj.exists():
        print(f"REFUSED: {inj} already exists; refusing to overwrite", file=sys.stderr)
        return 2
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
    readme = ROOT / "README.md"
    orig = readme.read_text(encoding="utf-8")
    try:
        readme.write_text(orig.replace('grep="^T1: 2/11 items met$"',
                                       'grep="^T1: 3/11 items met$"'), encoding="utf-8")
        results.append(expect_red(py, "README T1 count marker goes red on a wrong count", "3/11"))
    finally:
        readme.write_text(orig, encoding="utf-8")

    # 3. A milestone-3 marker whose cited evidence is absent must go red, not
    #    quietly pass.
    ms = ROOT / "docs/milestones.md"
    orig_ms = ms.read_text(encoding="utf-8")
    try:
        ms.write_text(orig_ms.replace('grep="^module drum_kit" in=rtl-sketch/drum_kit.v',
                                      'grep="^module drum_kit_NOT_IN_TREE" '
                                      'in=rtl-sketch/drum_kit.v'), encoding="utf-8")
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
