#!/usr/bin/env python3
"""Injected-defect controls for the per-(rig, host, capability) verdict (#136).

    python tools/control_capability_verdicts.py          every control must fire
    python tools/control_capability_verdicts.py --list   what each one injects

WHY THIS FILE EXISTS
--------------------
`tools/test_refprofile.py` asserts that the capability map behaves. That is not
the same as asserting the map CANNOT QUIETLY GO BACK to one boolean per rig,
which is the state #136 was filed about and the state a future simplification
pass would most plausibly restore. A suite that passes against the current tree
says nothing about which of its assertions are load-bearing; `make controls` is
where this repository answers that question, and a verdict refactor belongs
there as much as an RTL change does.

So each entry below is a way the fix gets undone, and this tool asserts the
suite turns RED **and names the test that must be among the failures**. A
control that fires for an unrelated reason is a false green wearing a red shirt:
collapsing the map also breaks the matrix renderer, and a control satisfied by
that would keep "passing" after the assertion it was written for was deleted.

The mutation is applied in a subprocess, to the imported `refprofile` module
object, before pytest collects. Nothing on disk is touched, so this is safe to
run beside anything else and leaves no state behind to explain.

THE WRONG-THEN-RIGHT RECORD FOR THIS FILE
-----------------------------------------
The first version of the `qualified_rigs` control patched the function to
consult `RIG_VERDICTS[n]["qualified"]` AFTER the capability filter -- which is
the real bug, since it is what a well-meaning "shouldn't we also check the rig
is qualified?" review comment produces. It fired. The first version of the
`None`-collapse control did not test anything new: mapping `None -> False`
already trips `test_an_overall_yes_never_hides_a_measured_no` through Surge,
before reaching the refusal wording the control was aimed at. Both named tests
are asserted per control for that reason.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: name -> (the mutation, the test that MUST be among the failures, why it is
#: a plausible way for the fix to be undone)
CONTROLS = {
    "RIG_BOOLEAN_FANOUT": (
        'for v in rp.RIG_VERDICTS.values():\n'
        '    v["capabilities"] = {c: v["qualified"] for c in rp.CAPABILITIES}',
        "test_the_capability_map_is_not_collapsible_to_the_rig_boolean",
        "the map derived from the rig-level boolean -- the shape #136 was filed "
        "about, and what a 'remove the duplicated table' cleanup produces"),
    "NONE_COLLAPSES_TO_FALSE": (
        'for v in rp.RIG_VERDICTS.values():\n'
        '    v["capabilities"] = {c: (False if q is None else q)\n'
        '                         for c, q in v["capabilities"].items()}',
        "test_an_unmeasured_capability_is_REFUSED_and_never_returned_as_False",
        "'unmeasured' written as 'rejected' -- #123's mistake one axis down, and "
        "what any `if not qualified:` branch does to a three-state value"),
    "NO_CAPABILITY_REASONS": (
        'for v in rp.RIG_VERDICTS.values():\n'
        '    v["capability_why"] = {}',
        "test_every_capability_verdict_says_which_measurement_produced_it",
        "verdicts with no measurement behind them: a table of bare booleans "
        "nobody can audit or correct"),
    "PROSE_NESTED_IN_THE_VERDICT": (
        'for v in rp.RIG_VERDICTS.values():\n'
        '    v["capabilities"] = {c: {"qualified": q, "why": "x"}\n'
        '                         for c, q in v["capabilities"].items()}',
        "test_no_capability_verdict_carries_prose_nested_beside_it",
        "prose nested where `split_profile` cannot reach it, so correcting a "
        "sentence invalidates every measurement hashed against profile.json "
        "(#129, one axis down)"),
    "REFUSAL_NAMES_ONLY_THE_RIG": (
        'def _bare(rig, host, capability):\n'
        '    v = rp.verdict_for(rig, host, capability)\n'
        '    if v["qualified"] is True:\n'
        '        return v\n'
        '    raise rp.Refused(f"{rig} is not qualified")\n'
        'rp.require_capability = _bare',
        "test_a_capability_refusal_names_the_capability_and_what_does_work",
        "the exact wording that parked 32 cases: true of the rig, useless to "
        "the reader, and indistinguishable from a missing qualification run"),
    "CAPABILITY_QUERY_RE_GATED_ON_THE_RIG": (
        '_orig = rp.qualified_rigs\n'
        'def _gated(host=None, capability=None):\n'
        '    return [n for n in _orig(host=host, capability=capability)\n'
        '            if rp.RIG_VERDICTS[n]["qualified"] is True]\n'
        'rp.qualified_rigs = _gated',
        "test_a_capability_query_finds_a_rig_its_rig_level_verdict_rejects",
        "the capability filter re-gated on the rig boolean, which is what a "
        "'shouldn't we also check the rig is qualified?' review comment asks "
        "for and which restores the original defect exactly"),
}

RUNNER = """
import pathlib, sys
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
import refprofile as rp
{mutation}
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "tools" / "test_refprofile.py")]))
"""


def run_one(name: str) -> tuple[bool, list, str]:
    """Apply one mutation and run the suite. Returns (fired, failed tests, why)."""
    mutation, must_fail, _why = CONTROLS[name]
    code = RUNNER.format(root=str(ROOT), mutation=mutation)
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True)
    failed = sorted({ln.split("::")[-1].split(" ")[0]
                     for ln in p.stdout.splitlines() if ln.startswith("FAILED")})
    if p.returncode == 0:
        return False, failed, "the suite stayed GREEN with the defect injected"
    if must_fail not in failed:
        return False, failed, (f"the suite went red but {must_fail} was not among "
                               f"the failures, so this control is satisfied by some "
                               f"OTHER assertion and would survive that one's "
                               f"deletion")
    return True, failed, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="what each control injects, and which test must catch it")
    a = ap.parse_args(argv)

    if a.list:
        for name, (mutation, must_fail, why) in CONTROLS.items():
            print(f"{name}\n  caught by  {must_fail}\n  why        {why}")
            print(textwrap.indent(mutation, "  | "))
            print()
        return 0

    bad = []
    for name in CONTROLS:
        fired, failed, why = run_one(name)
        print(f"{'RED ' if fired else 'DID NOT FIRE':<14}{name}"
              f"{'' if fired else '  -- ' + why}")
        for f in failed[:8]:
            print(f"               {f}")
        if not fired:
            bad.append(name)
    print()
    if bad:
        print(f"FAIL -- {len(bad)} control(s) did not fire: {', '.join(bad)}. "
              f"A run where these do not fire is a broken run, not a quiet one.")
        return 1
    print(f"OK -- all {len(CONTROLS)} capability-verdict controls fired, each on "
          f"the test it was written for.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
