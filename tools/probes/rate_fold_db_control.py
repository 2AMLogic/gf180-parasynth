#!/usr/bin/env python3
"""Is a dB `must` row constructible for the rate control's injected-bug test? #935

    tools/probes/rate_fold_db_control.py -o docs/sensitivity/rate-fold-db-control.txt
    tools/probes/rate_fold_db_control.py          # while iterating

THE QUESTION. `test_the_integer_fold_this_replaced_is_still_red_on_this_panel`
is the injected-bug control for `test_extra_features_are_rate_independent`, and
at the shipped licence every one of its twelve `must` rows is a `ratio` column
-- the dB family, 147 of the 191 columns, has no row in it. #935 asks whether
one is available: a dB pair that the integer fold turns red at
RATE_TOL_REL_DB = 0.1 would close that gap by being added to `must`.

WHAT IS MEASURED, AND AGAINST WHAT. The shipped eleven-entry RATE_PANEL, on
BOTH folds, through `rate_tolerance` itself -- the comparison that ships, not
a copy of it. The arithmetic is `rate_tol_sweep.measure_panel` and
`rate_tol_sweep.red`, the instrument the #782 sweep shipped and its tests tie
to `_rate_gaps`; this probe adds the tie as a RUNTIME PRECONDITION rather than
only a test: if its integer-fold red set stops matching
`_rate_gaps(taps=-1)` pair for pair, it REFUSES rather than reports.

Three readings, all on the committed grid of
docs/sensitivity/rate-tol-rel-by-unit-plan.json (reused, not restated):

    figures      the tolerance numbers #935 quotes, re-measured on this tree
                 -- what a cqt bin at the floor_db clamp (peak 80 dB) and a
                 bin 10 dB down carry at the shipped dial and at dial 0.
    grid         dB pairs red on each fold at every committed grid value, and
                 how many `must` rows that constructible: red on the INTEGER
                 fold AND green on the SHIPPED one. A pair red on both folds
                 is not a control row, it is a quarantine candidate -- the
                 suite would be red, not the injected defect.
    verdict      at the SHIPPED dial: the constructible dB pairs by name, or
                 the measured absence of any, plus the smallest committed
                 grid value at which one appears.

THE CLEAN-PANEL PRECONDITION. `RATE_KNOWN_RED` must be `{}` for the
"green on the shipped fold" half of a verdict to mean anything: an entry in
the quarantine would absorb a pair and turn a cost into a pass. A non-empty
quarantine is a REFUSAL, not a reading.

Exit 0 pass, 2 REFUSED -- this repository's convention, where 2 means "could
not answer", which is not the same as "no problem".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from model import discrimination_features as D  # noqa: E402
import rate_tol_sweep as S  # noqa: E402

SHIPPED_TOL = None  # `rate_tolerance`'s own shipped dial, whatever it is


class Refused(S.Refused):
    """This probe could not answer. Never reported as a pass."""


# ---------------------------------------------------------------------------
# the measurement


def _by_unit(rows, tol_rel) -> dict:
    """Red-pair counts per unit, on one fold."""
    out = {}
    for r in S.red(rows, tol_rel):
        out[r["unit"]] = out.get(r["unit"], 0) + 1
    return out


def _must_rows(injected, shipped, tol_rel) -> list[dict]:
    """dB pairs red on the integer fold AND green on the shipped one: the
    pairs a `must` row in the injected-bug control can assert. Red on both
    folds is the suite failing, not the defect being visible, and is not
    here."""
    red_ship = {(r["label"], r["column"]) for r in S.red(shipped, tol_rel)}
    return [r for r in S.red(injected, tol_rel)
            if r["unit"] == "db"
            and (r["label"], r["column"]) not in red_ship]


def _tie_to_rate_gaps(injected, shipped) -> None:
    """THE PRECONDITION THAT MAKES THIS A MEASUREMENT: this probe's red set
    must be `_rate_gaps`'s red set, pair for pair, on both folds. The reuse
    of measure_panel/red is what should guarantee it; asserting it is what
    makes a drift REFUSE instead of reporting numbers against a comparison
    the shipped test does not apply."""
    for rows, taps in ((injected, -1), (shipped, None)):
        theirs = {(lbl, nm) for lbl, nm, _, _, _ in D._rate_gaps(taps=taps)}
        mine = {(r["label"], r["column"]) for r in S.red(rows, SHIPPED_TOL)}
        if mine != theirs:
            raise Refused(
                f"this probe's red set on taps={taps} drifted from "
                f"_rate_gaps(taps={taps}): only the probe says "
                f"{sorted(mine - theirs)[:4]}, only _rate_gaps says "
                f"{sorted(theirs - mine)[:4]} -- the instrument is not "
                "measuring the comparison that ships, so it refuses")


def figures() -> list[dict]:
    """The tolerance numbers #935 quotes, re-measured through
    `rate_tolerance` at this tree's shipped dial and at dial 0 (the floor
    alone), so the artefact holds the licence shape rather than a citation
    of it."""
    col = "cqt3225Hz.seg0"
    rows = []
    for peak in (80.0, 10.0):
        rows.append({
            "peak_db": peak,
            "tol_shipped": D.rate_tolerance(col, peak, 0.0, SHIPPED_TOL),
            "tol_floor_only": D.rate_tolerance(col, peak, 0.0, 0.0),
        })
    return rows


def measure(grid) -> dict:
    if D.RATE_KNOWN_RED:
        raise Refused(
            f"RATE_KNOWN_RED is non-empty ({len(D.RATE_KNOWN_RED)} entries); "
            "the clean-panel reading this verdict needs -- green on the "
            "shipped fold means the pair is not a cost -- only holds with "
            "the quarantine empty")
    injected = S.measure_panel(D.RATE_PANEL, -1)
    shipped = S.measure_panel(D.RATE_PANEL, None)
    _tie_to_rate_gaps(injected, shipped)

    at_shipped = {
        "injected_by_unit": _by_unit(injected, SHIPPED_TOL),
        "shipped_by_unit": _by_unit(shipped, SHIPPED_TOL),
        "must": _must_rows(injected, shipped, SHIPPED_TOL),
    }
    grid_rows = []
    for v in grid:
        must = _must_rows(injected, shipped, {"db": v})
        grid_rows.append({
            "rel_db": v,
            "db_red_injected": float(len(S.red(injected, {"db": v}, "db"))),
            "db_red_shipped": float(len(S.red(shipped, {"db": v}, "db"))),
            "must_rows": float(len(must)),
        })
    first = next((r["rel_db"] for r in grid_rows if r["must_rows"] > 0), None)
    return {"shipped": at_shipped, "grid": grid_rows,
            "first_grid_value_with_a_must_row": first,
            "rel_db_shipped": D.RATE_TOL_REL["db"],
            "figures": figures(),
            "n_pairs": len(injected), "n_entries": len(D.RATE_PANEL)}


# ---------------------------------------------------------------------------
# the report


def report(res: dict, plan: dict, out) -> None:
    import platform

    import scipy
    w = lambda s="": print(s, file=out)  # noqa: E731
    w("Is a dB `must` row constructible for the rate control's injected-bug")
    w("test -- `test_the_integer_fold_this_replaced_is_still_red_on_this_panel`")
    w("-- at the shipped licence? The shipped eleven-entry RATE_PANEL, on both")
    w("folds, through rate_tolerance itself, with the red set tied to")
    w("_rate_gaps pair for pair as a runtime precondition. #935.")
    w(f"python {platform.python_version()}, numpy {np.__version__}, "
      f"scipy {scipy.__version__}.")
    w(f"Shipped: RATE_TOL_REL['db'] = {res['rel_db_shipped']}, "
      f"RATE_TOL_ABS = {D.RATE_TOL_ABS}, FOLD_SINC_TAPS = {D.FOLD_SINC_TAPS},")
    w(f"RATE_KNOWN_RED = {D.RATE_KNOWN_RED} (asserted empty: a quarantine")
    w("entry would absorb a pair and turn the cost reading into a pass).")
    w(f"Grid committed at {plan['_commit']} in "
      f"docs/sensitivity/rate-tol-rel-by-unit-plan.json (reused, not restated).")
    if plan["_dirty"]:
        w("NOT EVIDENCE: the plan was dirty or uncommitted when this ran, so the")
        w("grid is not known to predate the results. Do not cite this run.")
    w(f"Panel: {res['n_entries']} entries, {res['n_pairs']} (entry, column) "
      f"pairs per fold.")
    w()

    w("cqt_clamp_tolerance -- the tolerance numbers #935 quotes, re-measured on")
    w("  this tree through rate_tolerance itself. peak_db is the bin's magnitude")
    w("  (80 is the floor_db clamp); tol_shipped is what the shipped dial allows,")
    w("  tol_floor_only what dial 0 (the 0.5 dB floor alone) would. The licence")
    w("  grows with the bin's distance from 0 dB -- the shape #782's record")
    w("  states as its fifth limit and #935's body measures.")
    w()
    for line in S._table(res["figures"],
                         ["peak_db", "tol_shipped", "tol_floor_only"],
                         [9, 13, 17]):
        w(line)
    w()

    w("rate_fold_db_grid -- RATE_PANEL dB pairs red on each fold at every")
    w("  committed grid value, through the per-unit override; must_rows is the")
    w("  count CONSTRUCTIBLE as a control row: red on the integer fold AND green")
    w("  on the shipped one. A pair red on both folds is the suite failing, not")
    w("  the injected defect being visible, and is not a `must` row.")
    w()
    for line in S._table(res["grid"],
                         ["rel_db", "db_red_injected", "db_red_shipped",
                          "must_rows"],
                         [9, 17, 17, 11]):
        w(line)
    w()

    s = res["shipped"]
    w(f"rate_fold_injected_by_unit -- at the SHIPPED dial "
      f"({res['rel_db_shipped']}), red pairs on")
    w("  RATE_PANEL by unit, on each fold. fold 1 is the INTEGER fold the")
    w("  injected-bug control asserts against; fold 0 is the shipped one. The")
    w("  control's `must` set can only assert pairs the fold-1 row shows red.")
    w()
    units = sorted(D.RATE_TOL_ABS)
    unit_rows = [{"fold": 0.0,
                  **{f"{u}": float(s["shipped_by_unit"].get(u, 0)) for u in units}},
                 {"fold": 1.0,
                  **{f"{u}": float(s["injected_by_unit"].get(u, 0)) for u in units}}]
    for line in S._table(unit_rows, ["fold", *units],
                         [8, *[9] * len(units)]):
        w(line)
    w()

    w("verdict -- measured, not argued.")
    w()
    if s["must"]:
        w(f"  {len(s['must'])} RATE_PANEL dB pair(s) are red on the integer fold")
        w("  and green on the shipped one at the shipped licence; each is a")
        w("  `must` row the injected-bug control can assert:")
        w()
        w(f"  {'entry':<20} {'column':<28} {'gap':>10} {'peak':>10}")
        for r in s["must"]:
            w(f"  {r['label']:<20} {r['column']:<28} "
              f"{S._num(r['gap']):>10} {S._num(r['peak']):>10}")
    else:
        db_red = s["injected_by_unit"].get("db", 0)
        if db_red == 0:
            w("  No RATE_PANEL dB pair is red on the integer fold at the shipped")
            w(f"  licence (RATE_TOL_REL_DB = {res['rel_db_shipped']}): the "
              f"{sum(s['injected_by_unit'].values())} pairs the integer fold")
            w("  turns red are all in other units, so a dB `must` row is NOT")
            w("  constructible at the shipped licence and the control's all-ratio")
            w("  `must` set is a measured property of the dial, not an oversight.")
        else:
            other = sum(s["injected_by_unit"].values()) - db_red
            w(f"  {db_red} RATE_PANEL dB pair(s) are red on the integer fold at")
            w(f"  the shipped licence (RATE_TOL_REL_DB = {res['rel_db_shipped']})")
            w("  and red on the shipped fold too -- `must` is empty, so none of")
            w("  them is red on the integer fold alone: they are fold-invariant")
            w("  residue, the suite failing rather than the injected defect")
            w("  being visible, and none is constructible as a `must` row.")
            if other:
                w(f"  The other {other} pairs the integer fold turns red are in")
                w("  other units.")
            w("  A dB `must` row is NOT constructible at the shipped licence and")
            w("  the control's all-ratio `must` set is a measured property of")
            w("  the dial, not an oversight.")
        first = res["first_grid_value_with_a_must_row"]
        if first is None:
            w("  No committed grid value constructs one either.")
        else:
            w(f"  The smallest committed grid value that constructs one is "
              f"{S._num(first)};")
            w("  at that licence the shipped fold carries the quarantine cost")
            w("  the #782 sweep recorded, which is the trade that picked 0.1.")
    w()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="write the report here instead of stdout")
    ap.add_argument("--allow-dirty-plan", action="store_true",
                    help="measure anyway, and stamp the artefact as not evidence")
    args = ap.parse_args(argv)
    try:
        plan = S.load_plan(args.allow_dirty_plan, path=S.PLAN_BY_UNIT)
        res = measure(plan["_grid"])
    except S.Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.out:
        with args.out.open("w") as fh:
            report(res, plan, fh)
        print(f"wrote {args.out}")
    else:
        report(res, plan, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
