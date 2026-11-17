#!/usr/bin/env python3
"""Injected-defect controls for the #967 record-freshness rule.

    python tools/control_dag_churn.py          every control must fire
    python tools/control_dag_churn.py --list   what each one injects

WHY THIS FILE EXISTS
--------------------
The #967 rule has three legs, and each leg is load-bearing in a way that the
green suite cannot show on its own: a suite that only tests the assembled
rule passes whether or not a leg is wired to anything. Each entry below is a
way the fix gets undone -- an injection into tools/compile_dag.py IN MEMORY,
inside the pytest subprocess, before collection -- and the tool asserts the
churn suite turns RED **and names the one test that must be among the
failures**. A control satisfied by some unrelated assertion would survive the
deletion of the only assertion it was written for.

  * FRESHNESS_LEG_DROPPED replaces record_unchanged() with the verdict +
    normalised-detail test alone -- exactly the "verdict-equality test" the
    Curator rejected: without the freshness condition, a node whose covered
    file changed keeps reading GREEN off evidence taken before the change,
    forever, because nothing ever re-anchors its sha.
  * STALE_AGAINST_ALWAYS_FRESH defeats the condition one door down, by
    making the staleness primitive answer "fresh" unconditionally -- the
    input that defeats a guard through a new door (#824, rule 8).
  * TIMING_STRIP_REMOVED removes the normalisation, so wall-clock jitter
    alone rewrites a record and the suppression stops working -- the rule
    collapses back to per-push churn for the most common case measured.

Nothing on disk is mutated: the patched module lives only in the control's
subprocess. Safe to run beside anything else, and it leaves no state behind.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = "test_compile_dag_churn.py"
REANCHORS = "test_a_covered_change_reanchors_the_record_even_when_the_verdict_matches"
KEEPS_BYTES = "test_run_twice_here_then_once_at_a_covers_untouched_head_keeps_bytes"

#: name -> (the in-memory patch, expected colour, the test that must be among
#: the failures when red, why this is a plausible way for the fix to be undone)
CONTROLS = {
    "FRESHNESS_LEG_DROPPED": (
        """
def _verdict_only(n, old, ok, detail):
    return (old.get("passed") == ok and
            cd.normalise_detail(old.get("detail", "")) ==
            cd.normalise_detail(detail))
cd.record_unchanged = _verdict_only
""",
        "red", REANCHORS,
        "the freshness condition deleted from the writer -- a covered change "
        "then leaves the record at the old sha and the node reads GREEN off "
        "pre-change evidence forever. AC2's named injected bug"),
    "STALE_AGAINST_ALWAYS_FRESH": (
        """
cd.stale_against = lambda n, at_sha: ("fresh", "")
""",
        "red", REANCHORS,
        "the same defeat one door down: the staleness primitive is made to "
        "answer 'fresh' unconditionally, so the condition is present in form "
        "and absent in effect. A guard ships with the input that defeats it"),
    "TIMING_STRIP_REMOVED": (
        """
cd.normalise_detail = lambda detail: detail
""",
        "red", KEEPS_BYTES,
        "the normalisation deleted, so wall-clock jitter alone rewrites a "
        "record -- the most common churn measured in #967 comes back and the "
        "rule suppresses nothing on the suite nodes"),
}

RUNNER = """
import pathlib, sys
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "tools"))
import compile_dag as cd
{patch}
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "tools" / {suite!r})]))
"""


def run_one(name: str) -> tuple[bool, list, str]:
    """Inject one defect and run the churn suite. Returns (fired, failed
    tests, why-not)."""
    patch, expect, must_fail, _why = CONTROLS[name]
    code = RUNNER.format(root=str(ROOT), patch=patch.strip(), suite=SUITE)
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True)
    failed = sorted({ln.split("::")[-1].split(" ")[0]
                     for ln in p.stdout.splitlines() if ln.startswith("FAILED")})
    if p.returncode == 0:
        return False, failed, "the suite stayed GREEN with the defect injected"
    if must_fail not in failed:
        return False, failed, (f"the suite went red but {must_fail} was not "
                               f"among the failures, so this control is "
                               f"satisfied by some OTHER assertion and would "
                               f"survive that one's deletion")
    return True, failed, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="what each control injects, and what must happen")
    a = ap.parse_args(argv)

    if a.list:
        for name, (patch, expect, must_fail, why) in CONTROLS.items():
            print(f"{name}\n  expects    suite {expect.upper()}"
                  f"{'' if not must_fail else f', failing {must_fail}'}")
            print(f"  why        {why}")
            print(textwrap.indent(patch.strip(), "  | "))
            print()
        return 0

    bad = []
    for name in CONTROLS:
        fired, failed, why = run_one(name)
        expect = CONTROLS[name][1]
        label = ("RED " if expect == "red" else "GREEN") if fired \
            else "DID NOT HOLD"
        print(f"{label:<14}{name}{'' if fired else '  -- ' + why}")
        for f in failed[:8]:
            print(f"               {f}")
        if not fired:
            bad.append(name)
    print()
    if bad:
        print(f"FAIL -- {len(bad)} control(s) did not hold: {', '.join(bad)}. "
              f"A run where these do not hold is a broken run, not a quiet one.")
        return 1
    print(f"OK -- all {len(CONTROLS)} churn controls held: each leg of the "
          f"#967 rule is load-bearing, and removing any one of them turns "
          f"the churn suite red on the test written for it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
