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

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import main, mutation_control                    # noqa: E402

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

def run_one(name: str) -> tuple[bool, list, str]:
    """Apply one mutation and run the suite. Returns (fired, failed tests, why)."""
    mutation, must_fail, _why = CONTROLS[name]
    return mutation_control(mutation, must_fail)


if __name__ == "__main__":
    sys.exit(main(
        __doc__, CONTROLS, run_one, show=str,
        ok_message=f"OK -- all {len(CONTROLS)} capability-verdict controls "
                   f"fired, each on the test it was written for."))

