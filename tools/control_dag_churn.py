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

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import main, patch_control                           # noqa: E402

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

def run_one(name: str) -> tuple[bool, list, str]:
    """Inject one defect and run the churn suite. Returns (fired, failed
    tests, why-not)."""
    patch, expect, must_fail, _why = CONTROLS[name]
    return patch_control(patch, expect, must_fail, SUITE)


if __name__ == "__main__":
    sys.exit(main(
        __doc__, CONTROLS, run_one,
        show=lambda patch: patch.strip(),
        ok_message=f"OK -- all {len(CONTROLS)} churn controls held: each leg of "
                   f"the #967 rule is load-bearing, and removing any one of "
                   f"them turns the churn suite red on the test written for "
                   f"it."))
