#!/usr/bin/env python3
"""Injected-defect controls for the DAG's issue-only guard (#788).

    python tools/control_dag_issue_only.py          every control must fire
    python tools/control_dag_issue_only.py --list   what each one injects

WHY THIS FILE EXISTS
--------------------
`tools/test_compile_dag_deps.py::test_no_committed_node_cites_an_issue_as_its_only_source`
reads the committed `docs/dag.json` and passes. On a tree where M5 and D4 have
already been retired, *a guard that asserted nothing at all would also pass* --
`assert not []` is green whatever the predicate underneath it does. That is the
whole difficulty with a data-shaped guard: it is green on arrival by
construction, so its own greenness is not evidence that it is load-bearing.

So each entry below is a way the #788 fix gets undone, and this tool asserts
the suite turns RED **and names the test that must be among the failures** -- a
control satisfied by some unrelated assertion is a false green wearing a red
shirt, and would survive the deletion of the only assertion it was written for.

One entry is inverted: `BLOCKED_ESCAPE_STAYS_GREEN` injects the shape the guard
deliberately PERMITS and requires the suite to stay green. Without it, a guard
rewritten to reject every node carrying an `issue` would pass all the red
controls while being unsatisfiable the moment a node legitimately declares both
a blocker and the issue tracking it -- the "unsatisfiable gate" failure
CLAUDE.md names, which this project has shipped three times.

Nothing on disk is touched: the mutated DAG is written to a temporary file and
`compile_dag.DAG` is repointed at it inside the subprocess, before pytest
collects. Safe to run beside anything else, and it leaves no state behind.

THE WRONG-THEN-RIGHT RECORD FOR THIS FILE
-----------------------------------------
`BASE` was introduced on a hypothesis that measurement then REFUTED, and the
refutation is worth more than the file would be without it.

The hypothesis: a node carrying only `{"issue": 99999}` -- no `name`, no
`group` -- would fire this control for the wrong reason, because `table()` and
`mermaid()` index `n["name"]` unguarded and would `KeyError` before the guard
was ever reached, so pytest would go red on a crash rather than on the
assertion. Giving every injected node a full set of ordinary keys was supposed
to prevent that.

What running it showed: **the bare node fires the guard cleanly.** `rc=1`,
`test_no_committed_node_cites_an_issue_as_its_only_source` the only failure, no
`KeyError` anywhere in the output. The premise was half right and the
conclusion wrong -- `cd.table()` on a nameless node *does* raise
`KeyError: 'name'`, but `tools/test_compile_dag_deps.py` never calls the
renderer, so that path is not on this control's route at all. A valid
derivation from an unverified premise, which is the failure mode
`docs/failure-modes.md` names and the one that is faster to produce than the
check.

`BASE` is kept, for the smaller reason that survives: a control should inject
a node shaped like the thing it is standing in for, and a future control
pointed at a suite that *does* render would hit the `KeyError`. But the claim
that it was load-bearing here is withdrawn, and
`BARE_ISSUE_ONLY_NODE_STILL_FIRES` pins the measured behaviour so the
hypothesis cannot be quietly reinstated.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import main, node_control                             # noqa: E402

GUARD = "test_no_committed_node_cites_an_issue_as_its_only_source"

#: An ordinary node, so a control fires on the guard rather than on a renderer
#: that crashes first. See the wrong-then-right record above.
BASE = {"name": "injected control", "group": "minimoog",
        "class": "implementation", "deps": ["M2"]}

#: name -> (the node injected, expected colour, the test that must be among the
#: failures when red, why this is a plausible way for the fix to be undone)
CONTROLS = {
    "ISSUE_ONLY_NODE_RETURNS": (
        {**BASE, "issue": 99999},
        "red", GUARD,
        "exactly the M5/D4 shape re-added -- what filing a roadmap entry as a "
        "capability produces, and what #788 was filed about"),
    "ISSUE_ONLY_NODE_WITH_EMPTY_COVERS": (
        {**BASE, "issue": 99999, "covers": [], "slow": True},
        "red", GUARD,
        "the same node dressed in the keys a real node carries. `covers` and "
        "`slow` are not evidence, and a guard checking 'does this look like a "
        "node' rather than 'does it name evidence' would pass this"),
    "BLOCKED_ESCAPE_STAYS_GREEN": (
        {**BASE, "issue": 99999,
         "blocked": "waiting on a documented self-oscillation clip"},
        "green", None,
        "the shape the guard must PERMIT. A node that says what it is waiting "
        "for renders BLOCKED with that reason, which is derived from a stated "
        "fact rather than a stale pointer. A guard that rejected this would be "
        "unsatisfiable for M4 and S2, which both declare blockers"),
    "BARE_ISSUE_ONLY_NODE_STILL_FIRES": (
        {"issue": 99999},
        "red", GUARD,
        "the measured refutation of this file's own first hypothesis, pinned: a "
        "node with no `name` and no `group` fires the guard cleanly and does "
        "NOT crash the renderer, because this suite never calls it. See the "
        "wrong-then-right record above"),
    "NODE_WITH_NO_EVIDENCE_AND_NO_ISSUE_IS_PERMITTED": (
        {**BASE},
        "green", None,
        "the documented boundary, asserted rather than only described: "
        "deleting the `issue` key gets a node past this guard. It renders "
        "`TODO  no evidence declared`, which is at least honest about being "
        "empty. Pinned green so that widening the guard here is a deliberate "
        "decision with a failing control in front of it, not a silent one"),
}

def run_one(name: str) -> tuple[bool, list, str]:
    """Inject one node and run the suite. Returns (fired, failed tests, why)."""
    node, expect, must_fail, _why = CONTROLS[name]
    return node_control(node, expect, must_fail)


if __name__ == "__main__":
    sys.exit(main(
        __doc__, CONTROLS, run_one,
        show=lambda node: json.dumps(node, indent=2),
        ok_message=f"OK -- all {len(CONTROLS)} issue-only controls held: the "
                   f"guard fires on the shape #788 reported and permits the "
                   f"two shapes it documents."))
