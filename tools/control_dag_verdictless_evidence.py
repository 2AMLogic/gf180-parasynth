#!/usr/bin/env python3
"""Injected-defect controls for the verdict-less evidence_file guard (#971).

    python tools/control_dag_verdictless_evidence.py          every control must fire
    python tools/control_dag_verdictless_evidence.py --list   what each one injects

WHY THIS FILE EXISTS
--------------------
`tools/test_compile_dag_deps.py::test_no_committed_node_declares_a_verdict_less_evidence_file`
reads the committed `docs/dag.json` and passes. Like every data-shaped guard it
is green on arrival by construction -- `assert not []` is green whatever the
predicate underneath it does -- so its own greenness is not evidence that it is
load-bearing. Each entry below is a way the guard gets undone, or a shape it
must PERMIT, and this tool asserts the suite turns RED **and names the guard
among the failures** -- a control satisfied by some unrelated assertion is a
false green wearing a red shirt, and would survive the deletion of the only
assertion it was written for.

The red injection points at a file that REALLY EXISTS and really carries no
verdict -- `docs/reference-compare-results.json`, M3's own evidence -- so
`unjudged()` fires for exactly the reason the guard exists, not because a path
was misspelled. Nothing on disk is touched: the mutated DAG is written to a
temporary file and `compile_dag.DAG` is repointed at it inside the subprocess,
before pytest collects. Safe to run beside anything else, and it leaves no
state behind.

THE ESCAPE HATCHES ARE PINNED GREEN, ONE PER ESCAPE
---------------------------------------------------
Without them, a guard rewritten to reject every node carrying an `evidence_file`
would pass all the red controls while being unsatisfiable the moment a node
legitimately declares a verdict-less file -- M3 today, deliberately, with the
reason in its note. That is the "unsatisfiable gate" failure CLAUDE.md names;
`control_dag_issue_only.py`'s docstring records this repository shipping it
three times.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import main, node_control                             # noqa: E402

GUARD = "test_no_committed_node_declares_a_verdict_less_evidence_file"

#: An ordinary node, so a control fires on the guard rather than on a renderer
#: that crashes first (see control_dag_issue_only.py's wrong-then-right record).
BASE = {"name": "injected control", "group": "minimoog",
        "class": "implementation"}

#: name -> (the node injected, expected colour, the test that must be among the
#: failures when red, why this is a plausible way for the fix to be undone)
CONTROLS = {
    "SILENT_VERDICTLESS_EVIDENCE_FILE_RETURNS": (
        {**BASE, "evidence_file": "docs/reference-compare-results.json"},
        "red", GUARD,
        "the exact shape the guard exists for: an `evidence_file` with no "
        "verdict key and no `evidence_requires`, and no note. Pointed at M3's "
        "real, committed, verdict-less file so unjudged() fires for the true "
        "reason -- what re-filing M3's declaration without its note produces"),
    "DECLARED_REFUSED_VERDICTLESS_STAYS_GREEN": (
        {**BASE, "evidence_file": "docs/reference-compare-results.json",
         "note": "REFUSED on purpose: this injected node exercises M3's shape "
                 "-- a verdict-less file whose note states the refusal is "
                 "deliberate"},
        "green", None,
        "the M3 escape, which the guard must PERMIT: the note is a substring "
        "check -- acknowledgement, not truth, the same trust model as the "
        "`blocked` reason the #788 guard accepts. A guard that rejected this "
        "would be unsatisfiable for M3 the moment it landed"),
    "EVIDENCE_REQUIRES_ESCAPE_STAYS_GREEN": (
        {**BASE, "evidence_file": "docs/reference-compare-results.json",
         "evidence_requires": "bigdrive-miniv3"},
        "green", None,
        "the S3 escape: the node declares the substring that makes its "
        "evidence a pass, so unjudged() returns None and the guard must not "
        "fire. Pinned green so that removing this escape is a deliberate "
        "decision with a failing control in front of it, not a silent one"),
}

def run_one(name: str) -> tuple[bool, list, str]:
    """Inject one node and run the suite. Returns (held, failed tests, why)."""
    node, expect, must_fail, _why = CONTROLS[name]
    return node_control(node, expect, must_fail)


if __name__ == "__main__":
    sys.exit(main(
        __doc__, CONTROLS, run_one,
        show=lambda node: json.dumps(node, indent=2),
        ok_message=f"OK -- all {len(CONTROLS)} verdict-less evidence controls "
                   f"held: the guard fires on the silent declaration and "
                   f"permits both escapes."))
