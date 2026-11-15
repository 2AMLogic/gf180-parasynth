#!/usr/bin/env python3
"""Does `measure_promoted_bands.period_error_on_known_cases()` NOTICE when the
estimator it is grounding REFUSES? (#801)

    python3 tools/probes/period_error_refusal_policy.py           # the table
    python3 tools/probes/period_error_refusal_policy.py --check   # exit 1 on a gap

WHY THIS FILE EXISTS, AND WHY IT IS COMMITTED RATHER THAN DELETED

The figure this function returns is quoted in two places as the DISCRIMINATION
FLOOR for drum pitch -- `model/promoted_measures.py` ("worst error 0.068 %")
and `docs/discrimination.md` ("Those differences are 6-30 times that"). The
multiplier in the second is the observed tuning spread DIVIDED BY this figure,
so a figure computed over a silently shrunken population is too SMALL, the
multiplier is too LARGE, and a pitch difference can be called significant when
it sits inside the reader's real error. A wrong sound conclusion reached
through a correct-looking derivation, which is what `docs/failure-modes.md` is
about.

Before #801 the reduction was `worst = max(worst, abs(e.value / want - 1.0))`
over an UNGUARDED `.value`, in a second copy of the twelve-case grid that
`known_cases()` already walks with a refusal policy. `max()` drops `nan`
silently -- `max(0.068, float("nan")) -> 0.068`, because Python returns the
first argument when the comparison is False -- so the "worst error over the
known cases" was the worst over however many cases happened to answer.

WRONG-THEN-RIGHT (1 of 1 for this finding; CLAUDE.md asks for the rate)

The prediction from reading the code was that the WHOLLY-refusing stub would
report 0.0 and pass green as a "perfect" known-answer error. It reports 0.0 and
FAILS, because `tools/test_promoted_bands.py`'s bound is `0.0 < err < 0.1` and
the lower bound was already a real guard. The defect is the PARTIAL refusal,
condition C below, which only a run found. A record of a result that looked
good and was wrong is the point of keeping this file.

WHAT EACH CONDITION IS FOR

    A  real estimator        the published figure must be reproduced, finite
    B  always refuses (nan)  the whole population gone
    B2 always refuses (None) `Estimate.value` is `float | None`: before #801
                             this was a TypeError -- a CRASH where REFUSED
                             belongs ("startup errors are NO VERDICT")
    C  refuses 9 of 12       the defect: a SHRUNKEN population, not an empty
                             one, which `tools/vacuous_guard_audit.py` (#620)
                             explicitly does not model
    D  answers 0.0           the guard-defeating input for a refusal COUNT
                             (verification-rules.md rule 8): counting refusals
                             has no power against a stub that answers all
                             twelve with a plausible wrong number, so the
                             check cannot be a population count alone

PASS CONDITION (`--check`): A is finite and reproduces 0.068 %; B, B2, C and D
each fail to produce a number that satisfies the pinned bound `0.0 < err < 0.1`.
Run against `main` before #801 this exits 1 on C (0.034 %, green) and on B2
(TypeError). That is the start-red evidence for the repair.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "model")]
import measure_promoted_bands as mpb  # noqa: E402

pm = mpb.pm
#: The pinned bound in `tools/test_promoted_bands.py` that a reported figure
#: has to satisfy. A condition that satisfies it under a broken estimator is a
#: false green in the test suite, not merely an odd number in a table.
PINNED_LO, PINNED_HI = 0.0, 0.1
#: The published figure, from `model/promoted_measures.py` / `docs/discrimination.md`.
PUBLISHED_PCT = 0.068


def _is_published(worst) -> bool:
    """Is this the figure `model/promoted_measures.py` and
    `docs/discrimination.md` quote? Checked as well as finiteness, because a
    function that refused everything and a function that answered a DIFFERENT
    number would both satisfy "not green on the stubs"."""
    return math.isclose(float(worst), PUBLISHED_PCT, abs_tol=0.002)


def _satisfies(worst) -> bool:
    """Does this reading satisfy the pinned bound -- i.e. would it have passed
    green? Coerced to a Python `bool` deliberately: the estimators return
    numpy scalars, `0.0 < np.float64(x) < 0.1` is a `np.bool_`, and
    `np.bool_(True) is not True` -- which made this probe's own first run
    report a GAP on condition A. WRONG-THEN-RIGHT, in the instrument."""
    return bool(PINNED_LO < worst < PINNED_HI)


def _nan_refusal(*_a, **_k):
    return pm.Estimate(float("nan"), False, "stub refuses (probe)", {})


def _none_refusal(*_a, **_k):
    return pm.Estimate(None, False, "stub refuses (probe)", {})


def _zero_answer(*_a, **_k):
    return pm.Estimate(0.0, True, "", {})


def _partial_refusal(every: int = 4):
    """Refuses all but every `every`-th call, deferring those to the real
    estimator -- so the answers it does give are EXACTLY right. That is what
    makes it the dangerous case: three correct answers out of twelve, and the
    worst of three is smaller than the worst of twelve."""
    real = pm.dominant_period_ms
    calls = {"n": 0}

    def stub(x, sr, band, *a, **k):
        calls["n"] += 1
        if calls["n"] % every != 0:
            return _nan_refusal()
        return real(x, sr, band, *a, **k)

    return stub, calls


def _read() -> tuple[str, float, int, int]:
    """(rendering, value, answered, total) of the function under test.

    Reads `period_error_report()` when the repaired module offers it, so the
    population size is reported rather than inferred; falls back to the bare
    float on a pre-#801 tree, which is the whole point -- the probe has to run
    on both sides of the repair to be evidence about it."""
    report = getattr(mpb, "period_error_report", None)
    try:
        if report is not None:
            worst, answered, total = report()
        else:
            worst, answered, total = mpb.period_error_on_known_cases(), -1, -1
    except Exception as exc:  # a crash is a result, and it is NO VERDICT
        return f"{type(exc).__name__}", float("nan"), -1, -1
    return f"{worst:.6f}", worst, answered, total


def conditions() -> list:
    """(label, rendering, satisfies the pinned bound, answered, total, note).

    `satisfies` is the dangerous property: True means a broken estimator would
    have passed `tools/test_promoted_bands.py`'s bound green."""
    rows = []

    rendering, worst, answered, total = _read()
    rows.append(("A real estimator", rendering, _satisfies(worst),
                 answered, total,
                 "must be finite and reproduce "
                 f"{PUBLISHED_PCT} %: "
                 f"{'yes' if _is_published(worst) else 'NO'}"))

    saved = pm.dominant_period_ms
    try:
        for label, stub, note in (
                ("B always refuses (nan,ok=False)", _nan_refusal,
                 "whole population gone"),
                ("B2 always refuses (None,ok=False)", _none_refusal,
                 "pre-#801: TypeError, a crash where REFUSED belongs"),
                ("D answers 0.0 (ok=True)", _zero_answer,
                 "rule 8: a refusal COUNT has no power here; the magnitude does"),
        ):
            pm.dominant_period_ms = stub
            rendering, worst, answered, total = _read()
            rows.append((label, rendering, _satisfies(worst),
                         answered, total, note))
            pm.dominant_period_ms = saved

        stub, calls = _partial_refusal()
        pm.dominant_period_ms = stub
        rendering, worst, answered, total = _read()
        rows.append(("C refuses 9/12, 3 answer exactly", rendering,
                     _satisfies(worst), answered, total,
                     f"{calls['n']} grid calls; pre-#801: 0.034 %, BETTER than "
                     "the true figure, and green"))
    finally:
        pm.dominant_period_ms = saved
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 unless A reproduces the published figure and "
                         "B/B2/C/D all fail to produce one")
    args = ap.parse_args(argv)

    rows = conditions()
    print(f"{'condition':<36} {'returned':>12} {'answered':>9}   "
          f"{'bound ' + str(PINNED_LO) + '<err<' + str(PINNED_HI):<16} note")
    print("-" * 118)
    gaps = []
    worst_a = None
    for label, rendering, satisfies, answered, total, note in rows:
        pop = f"{answered}/{total}" if total >= 0 else "n/a"
        verdict = "PASS" if satisfies else "refused/red"
        print(f"{label:<36} {rendering:>12} {pop:>9}   {verdict:<16} {note}")
        wanted = label.startswith("A")
        if wanted:
            worst_a = rendering
        if satisfies is not wanted:
            gaps.append(f"{label}: bound satisfied={satisfies}, wanted={wanted}")
    # A must also be the PUBLISHED figure, not merely finite: a function that
    # refuses everything and a function that answers a different number both
    # satisfy "not green on the stubs". Read from the single run above -- a
    # second `conditions()` call would walk the twelve-case grid twice.
    try:
        if not _is_published(worst_a):
            gaps.append(f"A does not reproduce the published "
                        f"{PUBLISHED_PCT} %: {worst_a}")
    except ValueError:
        gaps.append(f"A did not return a number: {worst_a}")

    if args.check:
        for g in gaps:
            print(f"GAP  {g}")
        print("period-error refusal policy:", "PASS" if not gaps else f"FAIL ({len(gaps)})")
        return 1 if gaps else 0
    for g in gaps:
        print(f"GAP  {g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
