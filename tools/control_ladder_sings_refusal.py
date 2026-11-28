#!/usr/bin/env python3
"""Injected-defect controls for the refused-ring booking in the ladder comparison (#1851).

    python tools/control_ladder_sings_refusal.py          every control must fire
    python tools/control_ladder_sings_refusal.py --list   what each one injects

    exit 0   every control fired, from a suite that was green with no mutation
    exit 1   a control did not fire
    exit 2   REFUSED: the suite is not green before any mutation (#1125)

WHY THIS FILE EXISTS
--------------------
`stage_resonance` in `tools/compare_ladder_candidates.py` used to append
`False` to `sings` for a ring the estimator REFUSED, so one refused point read
as a measured "does not sing" and, through `_verdict`, handed a rival the
"sings where the shipped ladder does not" merit. Measured on the unfixed tree
with `compare(quick=True)`: the shipped ladder sang at all three of its
evaluated points, refused one, and was booked `sings_everywhere=False`; both
Newton candidates were credited with the merit.

The tests in `tools/test_compare_ladder_candidates.py` were red against that,
which is evidence once. From then on they are green, and a green suite does
not say which of its assertions a later edit could delete without noticing.
The fix lives in three places -- the stage, the verdict and the table -- and
each can be undone alone.

So each entry below re-breaks one of them in memory, and this tool asserts the
suite turns RED **and names the test that must be among the failures**. The
mutation is a text substitution applied to the source of the named function,
re-executed into the imported module inside the subprocess before pytest
collects. A substitution whose site has moved or duplicated fails the control
rather than injecting nothing. Nothing on disk is touched, so this is safe to
run beside anything else and leaves no state behind.

WHAT THIS CANNOT REACH
----------------------
`model/test_ladder_candidates.py` runs the real candidates. That its two
"sings nowhere" readings for `zdf-explicit` at 4800 and 6400 Hz were refusals
all along is a one-time record in the #1851 pull request, not something this
tool re-checks.

THE WRONG-THEN-RIGHT RECORD FOR THIS FILE
-----------------------------------------
Both of the first run's defects were reported by the three conditions above,
not found by reading it, which is the argument for them.

  * Five controls read DID NOT HOLD with no FAILED test at all. A one-entry
    substitution table was rendered `((old, new))`, which is a bare pair, so
    `for old, new in subs` unpacked a string and the mutant died before
    pytest collected. Only the two multi-entry controls fired. The literal
    now carries a trailing comma per entry and a malformed table fails by
    name.
  * The refusal-guard control named `test_every_point_refused_leaves_nothing
    _measured` for a mutation (`if sings and not refused` -> `if sings`) that
    leaves the all-refused case `None`, because `sings` is then empty: a test
    that mutation cannot redden. It was satisfied by six OTHER assertions.
    The mutation is now the pre-fix expression, which does read six refusals
    as `False`.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import main as lib_main, mutation_control, mutation_suite  # noqa: E402

SUITE = "test_compare_ladder_candidates.py"
MODULE, ALIAS = "compare_ladder_candidates", "cc"

STAGE = "test_a_refused_point_is_counted_and_enters_no_sings_figure"
NOTHING = "test_every_point_refused_leaves_nothing_measured"
VERDICT = "test_a_refused_point_on_the_shipped_ladder_is_not_a_merit_for_the_rival"
TABLE = "test_the_table_prints_a_refusal_as_REFUSED_and_a_measurement_as_true_or_false"

#: Substitutions against the source of the function each entry names.
BOOKED = ("                    refused += 1\n",
          "                    refused += 1\n                    sings.append(False)\n")
UNGUARDED = ("if sings and not refused else None),", "),")
OVER_ALL = ("round(sum(sings) / len(sings), 3) if sings else None,",
            "round(sum(sings) / max(len(sings), 1), 3),")

#: name -> ((the function, its substitutions), the test that MUST be among the
#: failures, why it is a plausible way for the fix to be undone)
CONTROLS = {
    "THE_DEFECT_AS_IT_SHIPPED": (
        ("stage_resonance", (BOOKED, UNGUARDED, OVER_ALL)),
        VERDICT,
        "the exact broken behaviour of #1851: the refused ring booked as "
        "`False`, both figures answered over it, and the rival credited with "
        "singing where the shipped ladder does not"),
    "REFUSED_BOOKED_AS_DOES_NOT_SING": (
        ("stage_resonance", (BOOKED,)),
        STAGE,
        "the handler's `sings.append(False)` back on its own: "
        "`sings_everywhere` still says None, and `sings_fraction` is diluted "
        "by a point nobody measured"),
    "SINGS_EVERYWHERE_ANSWERS_AS_IF_EVERY_POINT_WAS_EVALUATED": (
        ("stage_resonance", (UNGUARDED,)),
        NOTHING,
        "the `None` simplified away to the pre-fix `bool(sings and all(sings))`: "
        "six refusals read `sings_everywhere=False`, and one refusal beside "
        "singing points reads True"),
    "VERDICT_READS_A_REFUSAL_AS_FALSE": (
        ("_verdict", (("sings = bool(sings_measured and c_sings and not b_sings)",
                       "sings = bool(c_sings and not b_sings)"),)),
        VERDICT,
        "the stage correct and the verdict ignoring it: `not None` is True, "
        "so an unmeasured shipped ladder loses the merit to a rival that sang"),
    "VERDICT_NOTE_DROPS_THE_REFUSAL": (
        ("_verdict", (('candidate=rep["resonance"][n]["refused"],', "candidate=0,"),
                      ('shipped=rep["resonance"][base]["refused"]),', "shipped=0),"))),
        VERDICT,
        "the merit withheld and nothing saying why: the note reports zero "
        "refused points, so the refusal can no longer be found in the report"),
    "VERDICT_TEXT_SILENT_ABOUT_THE_REFUSAL": (
        ("_verdict", (('unmeasured = [r for r in notes if not r["sings_measured"]]',
                       "unmeasured = []"),)),
        VERDICT,
        "the sentence a reader of the printed report gets, dropped in a "
        "'shorten the verdict' pass that leaves the note as the only record"),
    "TABLE_PRINTS_A_REFUSAL_AS_FALSE": (
        ("_say_sings", (('return "REFUSED" if measured is None else str(measured)',
                         "return str(False if measured is None else measured)"),)),
        TABLE,
        "the pre-fix rendering: a refusal printed in the `sings` column as "
        "`False`, indistinguishable from a ladder that was measured not singing"),
}


def _mutation(function: str, subs) -> str:
    # A trailing comma per entry: `((old, new))` is a bare pair, not a
    # one-entry table, and the mutant then dies before pytest collects.
    pairs = "".join(f"({old!r}, {new!r}), " for old, new in subs)
    return (
        "import inspect\n"
        "def _repatch(fn, subs):\n"
        "    assert all(isinstance(s, tuple) and len(s) == 2 for s in subs), (\n"
        "        'malformed substitution table', subs)\n"
        "    source = inspect.getsource(fn)\n"
        "    for old, new in subs:\n"
        "        assert source.count(old) == 1, (\n"
        "            'the injection site is gone or ambiguous (%d of it): %r'\n"
        "            % (source.count(old), old))\n"
        "        source = source.replace(old, new)\n"
        "    exec(compile(source, inspect.getsourcefile(fn), 'exec'), "
        f"vars({ALIAS}))\n"
        f"_repatch({ALIAS}.{function}, ({pairs}))\n")


def _show(inject) -> str:
    function, subs = inject
    return "\n".join([f"in {function}:", *(
        f"{old!r}\n     -> {new!r}" for old, new in subs)])


def run_suite(mutation: str) -> tuple[int, list, str]:
    """Run the suite with `mutation` applied (empty = the UNMUTATED baseline
    every control's red is read against). Returns (pytest's exit status, the
    FAILED tests, the last line of output)."""
    return mutation_suite(mutation, module=MODULE, alias=ALIAS, suite=SUITE)


def run_one(name: str) -> tuple[bool, list, str]:
    """Apply one mutation and run the suite. Returns (fired, failed tests, why)."""
    (function, subs), must_fail, _why = CONTROLS[name]
    return mutation_control(_mutation(function, subs), must_fail,
                            module=MODULE, alias=ALIAS, suite=SUITE)


def main(argv=None) -> int:
    return lib_main(
        __doc__, CONTROLS, run_one, show=_show,
        baseline=lambda: run_suite(""),
        ok_message=f"OK -- all {len(CONTROLS)} refused-ring controls "
                   f"fired, each on the test it was written for.",
        argv=argv)


if __name__ == "__main__":
    sys.exit(main())
