#!/usr/bin/env python3
"""This repository's process exit contract, declared once so every reader
imports it instead of retyping an integer.

Outcomes, named rather than numbered -- the numbering is secondary:

    PASS                  0  the comparison ran and agreed
    FAIL                  1  the comparison ran and disagreed -- a RESULT
    REFUSED               2  a precondition was not met; nothing was measured
    REFUSED_ALTERNATE     3  the same outcome, declared by tools that already
                             used 3 for it (see "WHY 2 AND 3 BOTH")
    RUN_WITH_RSS_REFUSED  126  tools/run_with_rss.py's declared refusal; that
                             tool's exit status IS the wrapped command's, and
                             pytest exits 2 for "interrupted"

NO_EVIDENCE is the set of statuses a batch runner reports as NO-VERDICT:
statuses that mean no evidence exists, as distinct from a measurement that
disagreed. tools/run_all.py imports it; tools/refusal_exit_census.py reads it
back out of this file by AST and turns red if a refusal family lands on a
status outside it.

WHY 2 AND 3 BOTH.  The tree refuses with 2 (the dominant family --
tools/refusal_exit_census.py counts it) and, deliberately, with 3:

  * pnr/shuttle/{check_route,finish_halfslot,report_halfslot,run_librelane}.py
    declare REFUSED = 3 at module level and their handlers return it;
  * tools/corpus_lineage.py: "3, not 2: 'the apparatus could not run' must be
    distinguishable from 'the corpus has gaps' by a caller that only sees the
    exit code";
  * tools/r0_capture.py exits 3 for ERROR, "(execution error, NO VERDICT)" --
    a crash means no verdict; its documented contract is 0/1/2 with 3 carrying
    that third no-verdict outcome (docs/capture-r0.md);
  * tools/probe_orphan_reap.py refuse() exits 3: "REFUSED is not a pass";
  * tools/measure_conga_body_spread.py exits 3 when a descent row recorded a
    reader defect, printing "this run is not a result" -- also a claim that no
    evidence exists.

Every `return 3` / exit-3 site in the tracked tree was read before 3 was
declared here; none is a verdict (tools/refusal_exit_census.py lists them).

WHY NOT ONE NUMBER EVERYWHERE.  tools/run_with_rss.py's REFUSED is 126 on
purpose and must stay there -- see the comment at its declaration. This module
registers that choice rather than overwriting it.

THE ONE THING THIS CANNOT FIX.  An exit status is eight bits with no outcome
channel, so a tool that refuses with an undeclared status is still rendered
FAIL by the runner. Declare new statuses HERE (not at the tool), then run
`python3 tools/refusal_exit_census.py --check`: a family outside NO_EVIDENCE
turns it red.
"""
from __future__ import annotations

PASS = 0
FAIL = 1
REFUSED = 2
REFUSED_ALTERNATE = 3
RUN_WITH_RSS_REFUSED = 126

NO_EVIDENCE = frozenset({REFUSED, REFUSED_ALTERNATE, RUN_WITH_RSS_REFUSED})
