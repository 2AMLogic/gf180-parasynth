#!/usr/bin/env python3
"""The corpus-gate honesty scan: classify every file that resolves the Fischer
TR-808 corpus by what its ABSENCE handling does, and by which gates run it.

    .venv/bin/python tools/check_corpus_gate_honesty.py

Why this exists: `docs/scorecard/cymbal-369/tone-knob/README.md` row 7 recorded
that two controls documented as enforced were enforced nowhere -- a gate slot
occupied by a tool that cannot run where it matters -- and the class search the
repository requires at close was not done for the class. This scan is that
class search, kept as the instrument that produced the numbers (#814).

Classification, per file that mentions `configured_refs|REFS_DEFAULT|load_reference`:

- REFUSE   -- an absence branch that raises (or names Refused): the tool
             answers "did not run" loudly.
- SKIP     -- an absence branch that returns/prints past its checks: the tool
             goes quiet. HONEST only where no gate runs it (a reader reads the
             report, no verdict is at stake) or where the skipped checks are
             named as NOT RUN in the verdict.
- ?        -- the regex found no absence branch it recognises. NOT
             hand-classified; every count below is a lower bound and the
             "only gated skipper" result holds only for what the regex
             classified.

Gate membership is text membership of the file (or its basename) in the
Makefile and in CI's workflow files.

Counts on the tree this landed on (base origin/main 881c4631, before #814's
repair): 26 REFUSE, 8 SKIP, 29 ?. After #814's repair of
`tools/probes/verify_109_claims.py` -- the one gated non-test SKIP, repaired
to refuse under GF180_REQUIRE_TR808_REFS=1 and to report NOT RUN per check
otherwise -- the SKIP bucket is 7, of which exactly ONE remains gated:
`tools/test_run_case.py` (Makefile, CI:rungs.yml). That is the correct
pattern, not a defect: its `fischer_refs` fixture is the test OF the
required-corpus gate and honours GF180_REQUIRE_TR808_REFS, so its skips are
named rather than silent. No gated NON-TEST skipper remains.

Spot-check of the ? bucket's four gate members, read from the source by
#814's Builder (the regex's own stated limit, recorded rather than left
implicit):

- tools/probes/estimator_domains.py   (make verify): corpus absence is
  section 7, "NOT MEASURED HERE, and why" -- a first-class report that emits
  no check(), so its verdict covers only checks that ran. Not a silent
  skipper.
- tools/measure_promoted_bands.py     (`make verify-fast`'s validate gate and
  its own recipe): absence prints REFUSED to stderr and returns 2. Not a
  skipper.
- tools/measure_m1a_volume_mapping.py matched the scan only via its own
  `bass.load_reference` (the M1A manifest, not the Fischer corpus); absence
  raises Refused. Its gate slot is its pytest test file. Not a skipper.
- tools/mono_m1a_score.py             same shape: M1A-manifest reader, every
  absence path raises Refused; gate slot is its pytest test file. Not a
  skipper.

So the ? bucket contributed no second instance. Known limits, stated rather
than implied: the classification is a regex over absence branches, not data
flow -- a tool that skips without an `if not ...exists()` shape is invisible
to it, which is exactly what the ? bucket is. Rewriting on `ast` is the
durable fix and is deliberately NOT this file (#814 left it as a follow-on).

Two counting differences from the proposal's own run of this scan (26/8/29),
both stated: the REFUSE fallback here recognises the `except ... Refused`
spelling, not only a constructor, which moves two files from ? to REFUSE
(30/7/27 on this tree); and the instrument now finds itself, since its source
names the corpus-resolution idioms it scans for. The proposal-time counts are
recorded for comparison, not as this script's expected output.
This instrument is a REPORT: it exits 0 and prints; it is not wired into a
gate, because a gate over a classifier with a 29-file unclassified bucket
would certify more than the classifier knows.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN_DIRS = ("tools", "model", "fpga", "audition", "spec")
RESOLVES_CORPUS = re.compile(r"configured_refs|REFS_DEFAULT|load_reference")
ABSENCE_BRANCH = re.compile(
    r"\n([ \t]*)if[^\n]*\b(?:not .*\.exists\(\)|not refs|refs.*absent)[^\n]*:\n"
    r"((?:[ \t]*(?:#[^\n]*)?\n|\1[ \t]+[^\n]*\n)+)")


def classify(src: str) -> str:
    kind = None
    for indent, body in ABSENCE_BRANCH.findall(src):
        if "raise" in body or "Refused" in body:
            kind = "REFUSE"
            break
        if "return" in body or "skip" in body.lower():
            kind = "SKIP"
    if kind is None:
        # No recognisable absence branch. A file that handles `Refused` (the
        # repository's did-not-run outcome) and resolves the corpus is routed
        # through refusals even if the branch shape was not matched above;
        # this catches the except-clause spelling as well as a constructor.
        kind = "REFUSE" if (re.search(r"\bRefused\b", src) and "refs" in src) else "?"
    return kind


def gate_membership(rel: str) -> list[str]:
    """Text membership of the file -- or its basename, since the Makefile
    invokes basenames -- in the Makefile and CI workflows, of THIS tree."""
    gates: list[str] = []
    base = pathlib.PurePosixPath(rel).name
    makefile = ROOT / "Makefile"
    if makefile.exists() and base in makefile.read_text(errors="replace"):
        gates.append("Makefile")
    ci_dir = ROOT / ".github" / "workflows"
    if ci_dir.is_dir():
        for wf in sorted(ci_dir.glob("*.yml")):
            if base in wf.read_text(errors="replace") or rel in wf.read_text(errors="replace"):
                gates.append(f"CI:{wf.name}")
                break
    return gates


def main() -> int:
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            src = path.read_text(errors="replace")
            if not RESOLVES_CORPUS.search(src):
                continue
            rows.append((classify(src), rel, ",".join(gate_membership(rel)) or "-"))
    for kind in ("SKIP", "?", "REFUSE"):
        sel = [r for r in rows if r[0] == kind]
        print(f"\n########## {kind}  ({len(sel)})")
        for _k, f, g in sel:
            print(f"  {f:62s} gated:{g}")
    gated_skippers = [f for k, f, g in rows if k == "SKIP" and g != "-"]
    print("\n########## gated SKIP members")
    for f in gated_skippers:
        print(f"  {f}")
    if not gated_skippers:
        print("  (none)")
    print(f"\ntotal corpus-resolving files: {len(rows)} "
          f"(REFUSE {sum(1 for r in rows if r[0] == 'REFUSE')}, "
          f"SKIP {sum(1 for r in rows if r[0] == 'SKIP')}, "
          f"? {sum(1 for r in rows if r[0] == '?')})")
    print("read the module docstring for what the ? bucket does and does not "
          "tell you before quoting any count from this report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
