#!/usr/bin/env python3
"""Red-first control for the claim checker's DOCUMENT SET (#435).

WHAT IS BEING CONTROLLED. Not "does the checker detect a stale claim" -- the
suite in `tools/test_check_doc_claims.py` has covered that since #223. This
controls the layer underneath: *which files the no-argument run reaches at all*.
That layer had no control, and it was wrong. The default set was `docs/*.md`,
one directory and not even recursive, so eleven real markers -- in
`docs/scorecard/README.md`, `docs/scorecard/ensemble-e1a/rtl/README.md` and
decision record 0018 -- were parsed by nothing while the summary read
`51 claim(s) in 48 document(s): 51 ok, 0 stale, 0 refused`.

A marker the scanner never reaches is WEAKER than a skipped test. A skip at
least produces a REFUSED; an unreached marker produces a line of green.

HOW IT CONTROLS IT. Inject a deliberately STALE marker into a file that the old
default set did not reach and the new one does (`pnr/orfs/README.md` by
default), then run the checker BOTH WAYS over the whole tree:

    before   tools/check_doc_claims.py as of the named git revision
    after    tools/check_doc_claims.py as it stands in the working tree

The control PASSES only if `before` is green and silent about the injected file
while `after` is red and names it. A `before` that is already red would mean the
injection proved nothing -- the run was red for some other reason -- and this
script says REFUSED rather than claiming a result, per `CLAUDE.md`.

The injected marker is a `grep=` for a string that is not in the file it names,
so the control costs no pytest subprocess of its own; the run's cost is the
checker's own ~2 minutes, twice.

    python3 tools/probes/check_doc_claims_scope_control.py
    python3 tools/probes/check_doc_claims_scope_control.py --before origin/main
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = "tools/check_doc_claims.py"

# A claim that is false by construction: the string is not in the named file,
# so `grep=` reports STALE without running anything.
STALE_MARKER = (
    "\n"
    "<!-- #435 red-first control: injected by "
    "tools/probes/check_doc_claims_scope_control.py, removed again below -->\n"
    "This paragraph asserts something the tree flatly contradicts.\n"
    '<!-- claim: grep="zzz_injected_control_string_that_is_not_in_the_makefile"'
    " in=Makefile -->\n"
)

PASS, FAIL, REFUSED = "PASS", "FAIL", "REFUSED"


def run_checker(script: pathlib.Path) -> tuple[int, str]:
    """One whole-tree, no-argument run of a given copy of the checker."""
    r = subprocess.run([sys.executable, str(script), "--quiet"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def checker_at(rev: str, into: pathlib.Path) -> pathlib.Path | None:
    """The checker as of `rev`, written where its own ROOT still resolves.

    It must land directly in `tools/`, because the module computes
    `ROOT = Path(__file__).resolve().parent.parent`: one directory deeper and it
    would take `tools/` for the repository and scan nothing, which would look
    exactly like the silence this control is trying to demonstrate.
    """
    r = subprocess.run(["git", "show", f"{rev}:{CHECKER}"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    into.write_text(r.stdout)
    return into


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", default="origin/main",
                    help="revision whose checker is the 'before' arm")
    ap.add_argument("--doc", default="pnr/orfs/README.md",
                    help="file to inject into: newly in scope, not in docs/*.md")
    a = ap.parse_args()

    doc = ROOT / a.doc
    if not doc.is_file():
        print(f"{REFUSED}: {a.doc} does not exist", file=sys.stderr)
        return 2
    if doc.parent == ROOT / "docs":
        print(f"{REFUSED}: {a.doc} is in docs/, which the old set already "
              "reached -- the injection would prove nothing", file=sys.stderr)
        return 2

    scratch = ROOT / "tools" / f"_check_doc_claims_before_{os.getpid()}.py"
    before_script = checker_at(a.before, scratch)
    if before_script is None:
        print(f"{REFUSED}: cannot read {CHECKER} at {a.before}", file=sys.stderr)
        scratch.unlink(missing_ok=True)
        return 2

    original = doc.read_text(encoding="utf-8")
    results: dict[str, tuple[int, str]] = {}
    try:
        doc.write_text(original + STALE_MARKER, encoding="utf-8")
        for arm, script in (("before", before_script), ("after", ROOT / CHECKER)):
            t0 = time.time()
            code, out = run_checker(script)
            results[arm] = (code, out)
            print(f"--- {arm} ({a.before if arm == 'before' else 'working tree'}, "
                  f"{time.time() - t0:.1f}s, exit {code}) ---")
            print("\n".join(out.strip().splitlines()[-6:]))
            print()
    finally:
        doc.write_text(original, encoding="utf-8")
        scratch.unlink(missing_ok=True)

    b_code, b_out = results["before"]
    a_code, a_out = results["after"]

    if b_code != 0:
        print(f"{REFUSED}: the 'before' arm was already red (exit {b_code}) with "
              "the defect injected, so its silence about the injection is not "
              "evidence of anything. Fix the pre-existing failure first.")
        return 2
    if a.doc in b_out:
        print(f"{REFUSED}: the 'before' arm mentioned {a.doc}; it is not the "
              "old document set after all.")
        return 2
    if a_code != 1:
        print(f"{FAIL}: the 'after' arm exited {a_code}, not 1 -- an injected "
              "STALE claim in a newly-scanned file did not turn the run red.")
        return 1
    if a.doc not in a_out:
        print(f"{FAIL}: the 'after' arm went red but never named {a.doc}. A red "
              "run that does not say which file is not actionable.")
        return 1

    print(f"{PASS}: {a.doc} carrying a STALE claim was invisible to {a.before} "
          f"(exit 0, not mentioned) and is now reported by name (exit 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
