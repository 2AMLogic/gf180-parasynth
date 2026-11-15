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

import argparse
import json
import pathlib
import subprocess
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
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

RUNNER = """
import json, pathlib, sys, tempfile
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "tools"))
import compile_dag as cd
doc = json.loads(cd.DAG.read_text())
doc["nodes"]["ZZ"] = {node!r}
tmp = pathlib.Path(tempfile.mkdtemp()) / "dag.json"
tmp.write_text(json.dumps(doc, indent=2))
cd.DAG = tmp
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "tools" / "test_compile_dag_deps.py")]))
"""


def run_one(name: str) -> tuple[bool, list, str]:
    """Inject one node and run the suite. Returns (fired, failed tests, why)."""
    node, expect, must_fail, _why = CONTROLS[name]
    code = RUNNER.format(root=str(ROOT), node=node)
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True)
    failed = sorted({ln.split("::")[-1].split(" ")[0]
                     for ln in p.stdout.splitlines() if ln.startswith("FAILED")})
    if expect == "green":
        if p.returncode != 0:
            return False, failed, ("the suite went RED on a shape the guard is "
                                   "meant to PERMIT -- the guard is stricter "
                                   "than its own contract")
        return True, failed, ""
    if p.returncode == 0:
        return False, failed, "the suite stayed GREEN with the defect injected"
    if must_fail not in failed:
        return False, failed, (f"the suite went red but {must_fail} was not among "
                               f"the failures, so this control is satisfied by "
                               f"some OTHER assertion and would survive that "
                               f"one's deletion")
    return True, failed, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="what each control injects, and what must happen")
    a = ap.parse_args(argv)

    if a.list:
        for name, (node, expect, must_fail, why) in CONTROLS.items():
            print(f"{name}\n  expects    suite {expect.upper()}"
                  f"{'' if not must_fail else f', failing {must_fail}'}")
            print(f"  why        {why}")
            print(textwrap.indent(json.dumps(node, indent=2), "  | "))
            print()
        return 0

    bad = []
    for name in CONTROLS:
        fired, failed, why = run_one(name)
        expect = CONTROLS[name][1]
        label = ("RED " if expect == "red" else "GREEN") if fired else "DID NOT HOLD"
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
    print(f"OK -- all {len(CONTROLS)} issue-only controls held: the guard fires on "
          f"the shape #788 reported and permits the two shapes it documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
