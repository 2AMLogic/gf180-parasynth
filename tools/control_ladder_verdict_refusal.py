#!/usr/bin/env python3
"""Injected-defect controls for the withheld verdict in the ladder comparison (#2036).

    python tools/control_ladder_verdict_refusal.py          every control must fire
    python tools/control_ladder_verdict_refusal.py --list   what each one injects

    exit 0   every control fired, from a suite that was green with no mutation
    exit 1   a control did not fire
    exit 2   REFUSED: the suite is not green before any mutation (#1125)

WHY THIS FILE EXISTS
--------------------
`_verdict` in `tools/compare_ladder_candidates.py` reached RETAIN by default.
With every ladder dead it read three `nan` summaries, every comparison against
them is False, `wins` stayed empty, and `main` printed "RETAIN THE CURRENT
FILTER" at exit 0 (measured on the unfixed tree, origin/main @ 616b8d63, in
`tools/test_ladder_candidates_verdict_refusal.py`). A dead candidate beside a
healthy baseline was scored "no better" the same way. And `--only` without
`shipped` left as `KeyError: 'shipped'`, a traceback where a refusal belongs.

The tests in that file were red against all of it, which is evidence once. From
then on they are green, and a green suite does not say which of its assertions
a later edit could delete without noticing. The fix lives in four places -- the
gap check, the withheld verdict's rows, the exit mapping in `main`, and the two
`--only` guards inside the gap check -- and each can be undone alone. PR #1979
widens the handler those guards sit beside, and #1851 edits the same
`_verdict`, so the neighbourhood is about to be edited twice.

So each entry below re-breaks one of them in memory, and this tool asserts the
suite turns RED **and names the test that must be among the failures**. The
mutation is a text substitution applied to the source of the function each
entry names, re-executed into the imported module inside the subprocess before
pytest collects. A substitution whose site has moved fails the control rather
than injecting nothing. Nothing on disk is touched.

WHAT THIS CANNOT REACH
----------------------
The three `test_end_to_end_*` tests run the tool from disk through its real
`__main__` (`runpy.run_path`), so they never see an in-memory mutation. The
suite here is the in-process layer only (`-k "not end_to_end"`), and the exit
status of `python tools/compare_ladder_candidates.py` with the fix reverted is
a one-time record in the #2036 pull request, not something this tool re-checks.

WRONG-THEN-RIGHT, in the check of this file's own REFUSE path. A first run
planted a failing test in a symlink tree, repointed `control_dag_lib.ROOT`
(the subprocess cwd) and got exit 0 with all ten controls "RED": this harness
formats its RUNNER with ITS OWN `ROOT` (the suite path), so the plant was
never run and every red was the real suite's. Repoint both, as
`tools/test_control_harness_baselines.py` does; with both repointed the
planted suite REFUSES at exit 2 before any control runs. A harness that
reads green under a plant is the false green #1125 closed.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import ROOT, main as lib_main, run_control, run_raw  # noqa: E402

SUITE = "test_ladder_candidates_verdict_refusal.py"

RUNNER = """
import pathlib, sys
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "tools"))
import compare_ladder_candidates as cc
{mutation}
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      "-k", "not end_to_end",
                      str(ROOT / "tools" / {suite!r})]))
"""

PATCH = """
import inspect
source = inspect.getsource(cc.{function})
patched = source.replace({old!r}, {new!r})
assert patched != source, "the injection site is gone: this control no longer injects anything"
exec(patched, vars(cc))
"""

DEAD_BASELINE = "test_a_dead_baseline_withholds_the_verdict"
DEAD_CANDIDATE = "test_a_dead_candidate_is_not_compared_and_is_not_scored_no_better"
DEAD_RING = "test_a_baseline_whose_every_ring_point_refused_withholds_the_verdict"
PARTIAL = "test_partial_refusal_is_still_a_measurement_and_the_verdict_stands"
NEG_INF = "test_a_summary_that_is_not_a_finite_number_is_unmeasured[neg-inf]"
MAIN = "test_main_exits_refused_and_names_the_gap_on_stderr"
NO_BASE = "test_only_without_the_baseline_withholds_instead_of_raising_key_error"
NO_RIVAL = "test_only_the_baseline_withholds_a_vacuous_verdict"

THE_CHECK = "    gaps = _evidence_gaps(rep, names)\n    if gaps:\n        return _withheld(rep, names, gaps)\n"
EVERY_POINT = "                unmeasured = bool(tally) and tally[0] == tally[1]\n"
FINITE = "                    unmeasured = unmeasured or not _measured(value)\n"

#: name -> ((function, the text replaced inside it, its replacement), the test
#: that MUST be among the failures, why it is a plausible way for the fix to be undone)
CONTROLS = {
    "GAP_CHECK_REMOVED": (
        ("_verdict", THE_CHECK, "    gaps = _evidence_gaps(rep, names)\n"),
        DEAD_CANDIDATE,
        "the exact broken behaviour of #2036: the gaps are computed and never "
        "read, so every comparison runs on `nan`, is False, and RETAIN is "
        "reached by default -- a dead candidate is scored \"no better\""),
    "EVERY_POINT_REFUSED_NOT_READ": (
        ("_evidence_gaps", EVERY_POINT, "                unmeasured = False\n"),
        DEAD_RING,
        "the point count dropped and only the finite-number test left: a stage "
        "that refused every ring point still books `sings_everywhere=False`, "
        "which the verdict reads as a measurement"),
    "PARTIAL_REFUSAL_WITHHOLDS": (
        ("_evidence_gaps", EVERY_POINT, "                unmeasured = bool(tally) and tally[0] > 0\n"),
        PARTIAL,
        "the guard made too tight, which is rule 8's other direction: any "
        "refused point withholds, and the real grids -- 2 of 8 linear points "
        "refused on the full one -- can no longer reach a verdict at all"),
    "FINITE_CHECK_IS_A_COMPARISON": (
        ("_evidence_gaps", FINITE,
         "                    unmeasured = unmeasured or not value < float(\"inf\")\n"),
        NEG_INF,
        "#134's shape: the guard written as a comparison. It still catches `nan` "
        "and `+inf` (both compare False) and lets `-inf` through, and `-inf < x` "
        "is True, so an infinite summary WINS the tuning merit"),
    "MISSING_BASELINE_NOT_CAUGHT": (
        ("_evidence_gaps", "    if base not in ran:\n", "    if False:\n"),
        NO_BASE,
        "the `--only` without `shipped` guard dropped: nothing withholds, the "
        "verdict reads a baseline that is not there, and `KeyError: 'shipped'` "
        "leaves as a traceback at exit 1 again"),
    "NO_CANDIDATE_RETAINS": (
        ("_evidence_gaps", "    if not [n for n in names if n != base]:\n", "    if False:\n"),
        NO_RIVAL,
        "`--only shipped` allowed through again: with no candidate the loop "
        "body never runs and RETAIN is printed over nothing"),
    "DEAD_CANDIDATE_BOOKED_COMPARED": (
        ("_withheld", "            candidate=n, state=\"NOT COMPARED\",\n",
         "            candidate=n, state=\"COMPARED\",\n"),
        DEAD_CANDIDATE,
        "the verdict withheld but the row relabelled: a candidate named "
        "COMPARED in a report where it was not"),
    "WITHHELD_MERIT_GUESSED_FALSE": (
        ("_withheld", "            wins_on_merit=None,\n", "            wins_on_merit=False,\n"),
        DEAD_CANDIDATE,
        "unknown rendered as a measurement: `False` prints as \"no better\", "
        "which is the sentence this issue exists to keep off an unmeasured input"),
    "WITHHELD_EXITS_0": (
        ("main", "        return 2\n    return 0\n", "        return 0\n    return 0\n"),
        MAIN,
        "the text changed and the exit status not: the refusal is printed and "
        "a runner that reads the status records PASS"),
    "WITHHELD_SAYS_NOTHING_ON_STDERR": (
        ("main",
         "        print(\"REFUSED: no verdict -- \" + \"; \".join(g[\"say\"] for g in v[\"withheld\"]),\n"
         "              file=sys.stderr)\n",
         "        pass\n"),
        MAIN,
        "the status kept and the reason dropped: exit 2 with nothing on stderr "
        "naming what was unmeasured, so a log cannot say why"),
}


def run_suite(mutation: str) -> tuple[int, list, str]:
    """Run the suite with `mutation` applied (empty = UNMUTATED) and return the
    raw (exit status, FAILED tests, last output line)."""
    return run_raw(RUNNER.format(root=str(ROOT), mutation=mutation, suite=SUITE))


def run_one(name: str) -> tuple[bool, list, str]:
    """Apply one mutation and run the suite. Returns (fired, failed tests, why)."""
    (function, old, new), must_fail, _why = CONTROLS[name]
    return run_control(RUNNER.format(
        root=str(ROOT), mutation=PATCH.format(function=function, old=old, new=new),
        suite=SUITE), "red", must_fail)


def main(argv=None) -> int:
    return lib_main(
        __doc__, CONTROLS, run_one,
        show=lambda inject: f"in {inject[0]}: {inject[1]!r}\n     -> {inject[2]!r}",
        baseline=lambda: run_suite(""),
        ok_message=f"OK -- all {len(CONTROLS)} withheld-verdict controls fired, "
                   f"each on the test it was written for.",
        argv=argv)


if __name__ == "__main__":
    sys.exit(main())
