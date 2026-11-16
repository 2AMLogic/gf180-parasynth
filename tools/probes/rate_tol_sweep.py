#!/usr/bin/env python3
"""What does narrowing `RATE_TOL_REL` buy, and what does it cost? #775

    tools/probes/rate_tol_sweep.py -o docs/sensitivity/rate-tol-rel-sweep.txt
    tools/probes/rate_tol_sweep.py --f0 220 440 660 --no-detail   # while iterating

THE DEFECT. `RATE_TOL_REL = 0.12` is the relative half of what
`test_extra_features_are_rate_independent` is allowed to call "the same
number" at 44.1 kHz and 48 kHz. On `jit.cyclecorr.seg0` that is 0.1183 --
**8.10x the 0.0146 the column reports for 0.5 % per-period jitter**, which is
the only mechanism the column exists to detect. A control that cannot see a
rate dependence eight times the size of its own signal is not a control.

WHAT IS SWEPT, AND AGAINST WHAT. One dial, `RATE_TOL_REL`, through
`rate_tolerance`'s `tol_rel` override -- the shipped comparison, not a copy of
it, which is the whole reason that override exists. Two sides, because a
tolerance is bounded from both and one number cannot say so:

    caught   (entry, column) pairs over tolerance on the INTEGER FOLD that
             shipped before #767 (`taps=-1`), over the wide panel. THE
             OBJECTIVE, higher is better: it is how much real 44.1k<->48k
             dependence the control can see, measured on the honest injected
             defect `test_the_integer_fold_this_replaced_is_still_red_on_this_panel`
             already uses rather than on a synthetic perturbation.
    cost     the same count on the SHIPPED fold, SPLIT BY UNIT. A clean panel
             turning red is the price. It is split because the two families'
             floors stand in completely different relations to their columns'
             magnitudes -- 0.02 against a correlation of ~1, 0.5 dB against a
             cqt column clamped at -80 -- so one undifferentiated count would
             hide the distinction the decision turns on.
    panel    the same cost on the ELEVEN-ENTRY shipped `RATE_PANEL`, through
             `_rate_gaps(tol_rel=...)` itself. This is the number that decides
             whether the suite is green: the wide panel is the evidence, but
             `RATE_PANEL` is what ships.

THE PANEL IS WIDE ON PURPOSE. 44 values of f0 in both resampling directions
(`fold_rate_sweep.F0_GRID`), plus the two control shapes `RATE_PANEL` carries
at both build rates: the 0.1 %-jitter entry, which is the only thing that
makes the ppm family live at all, and the band-limited square, which is the
input that defeats the fold repair. The eleven-entry panel understates the
cost by construction, and #775's curation already published the eleven-entry
trade -- so a sweep that measured only that would be restating a known answer.

AN APPARATUS PRECONDITION, ASSERTED RATHER THAN ASSUMED. On a few clips
`dominant_period` picks a different octave at the two rates (880 Hz built at
48 kHz returns 101 samples, two periods -- #774). The two sides are then
measuring different partials, so every `jit.*` column on such an entry is
REFUSED for both counts rather than reported, and the refused tally is in the
summary table. Counting them would price this dial with #774's defect folded
in. The `mpd*`, `cqt*` and `ms*` families do not use `dominant_period` and are
unaffected.

THE GRID IS COMMITTED BEFORE THE MEASUREMENT, and this file refuses to measure
while `docs/sensitivity/rate-tol-rel-plan.json` is uncommitted or dirty --
`tools/sweep_m1a_attack.py`'s refusal on a dirty `attack-sweep/grid.json` is
the precedent, and the reason is the same: a grid stated after seeing results
is not a grid. Pass `--allow-dirty-plan` only to iterate; it stamps the
artefact as not-evidence and the sensitivity record must not cite it.

Its validation cases -- including the one that ties this instrument's
re-evaluated tolerance arithmetic to what `_rate_gaps` itself returns, so the
re-evaluation cannot drift from the shipped comparison -- are in
tools/probes/test_rate_tol_sweep.py.

Exit 0 pass, 2 REFUSED -- this repository's convention, where 2 means "could
not answer", which is not the same as "no problem".
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model import discrimination_features as D  # noqa: E402
from tools.probes import fold_rate_sweep as F  # noqa: E402

PLAN = ROOT / "docs" / "sensitivity" / "rate-tol-rel-plan.json"

SR_A, SR_B = 44100, 48000

# The two control shapes RATE_PANEL carries, at both build rates. Without the
# jittered pair the ppm family is identically zero on every entry and the
# relative term there is never live; without the band-limited pair every entry
# has one exactly integer-periodic side and a fold could be right by accident.
CONTROL_ENTRIES = (
    # label,                   f0,    jitter, seed, kind,       build rate
    ("330Hz+0.1%jit@44k",      330.0, 0.001,  3,    "square",   SR_A),
    ("330Hz+0.1%jit@48k",      330.0, 0.001,  3,    "square",   SR_B),
    ("523Hz bandlimited@44k",  523.0, 0.0,    0,    "blsquare", SR_A),
    ("523Hz bandlimited@48k",  523.0, 0.0,    0,    "blsquare", SR_B),
)

JIT_PREFIX = "jit."
# How far dominant_period may disagree between the two rates before every
# jit.* column on the entry is refused. The same 5 % fold_rate_sweep.pair()
# uses, for the same reason and for the same defect (#774).
OCTAVE_TOL = 0.05


class Refused(Exception):
    """The sweep could not answer. Never reported as a pass."""


# ---------------------------------------------------------------------------
# the committed grid


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _rel(path: Path) -> str:
    """The plan's path as the repository sees it, or as given when it is not
    under the repository at all -- a monkeypatched plan in a test directory is
    still a plan that has to be reported, not a traceback."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_plan(allow_dirty: bool = False) -> dict:
    """The grid, the rule and the prediction, as committed.

    Refuses on a plan that is absent, untracked or dirty: a grid stated after
    the results is not a grid, and nothing in the artefact could tell a reader
    which it was.
    """
    if not PLAN.exists():
        raise Refused(f"no plan at {_rel(PLAN)}; state the grid first")
    plan = json.loads(PLAN.read_text())
    if plan.get("schema") != "sensitivity-plan-v1":
        raise Refused(f"{PLAN.name}: schema is {plan.get('schema')!r}")
    grid = [float(v) for v in plan["grid"]]
    if sorted(grid) != grid or len(set(grid)) != len(grid):
        raise Refused(f"{PLAN.name}: grid {grid} is not strictly increasing")
    if not allow_dirty:
        rel = _rel(PLAN)
        if not git("ls-files", rel):
            raise Refused(f"{rel} is not committed; state the grid first")
        if git("status", "--porcelain", "--", rel):
            raise Refused(f"{rel} is dirty; commit the grid before measuring it")
    plan["_grid"] = grid
    plan["_dirty"] = allow_dirty
    plan["_commit"] = ("UNCOMMITTED" if allow_dirty
                       else git("log", "-1", "--format=%h", "--", _rel(PLAN)))
    return plan


# ---------------------------------------------------------------------------
# the measurement


def wide_panel(f0_grid=None) -> tuple:
    """Every panel entry, as RATE_PANEL's own six-tuple shape."""
    f0s = F.F0_GRID if f0_grid is None else tuple(f0_grid)
    rows = []
    for f0 in f0s:
        for sr in (SR_A, SR_B):
            rows.append((f"{f0:g}Hz@{sr // 1000}k", f0, 0.0, 0, "square", sr))
    return tuple(rows) + CONTROL_ENTRIES


def measure_panel(panel, taps) -> list[dict]:
    """Every (entry, column) pair as its gap and its peak, so the tolerance
    arithmetic can be re-run at every grid value without re-running the
    features -- the dial changes the COMPARISON, not the measurement, which is
    what makes a seven-point grid cost the same as a one-point one.

    `_rate_pair` asserts each entry's own preconditions (that a jittered entry
    really carries jitter, that a band-limited entry really is fractional at
    both rates); an AssertionError there is a REFUSAL of the whole sweep, not
    a row to drop quietly.
    """
    rows = []
    for label, f0, jitter, seed, kind, build_sr in panel:
        try:
            a, sr_a, b, sr_b = D._rate_pair(label, f0, jitter, seed, kind, build_sr)
        except AssertionError as exc:
            raise Refused(f"panel entry {label}: {exc}") from exc
        va, names = D.extra_features(a, sr_a, taps)
        vb, _ = D.extra_features(b, sr_b, taps)
        pa = D.dominant_period(a, sr_a) / float(sr_a)
        pb = D.dominant_period(b, sr_b) / float(sr_b)
        octave = not (pa > 0 and pb > 0 and abs(pb / pa - 1.0) < OCTAVE_TOL)
        for nm, x, y in zip(names, va, vb):
            rows.append({
                "label": label, "column": nm, "unit": D._unit_of(nm),
                "gap": abs(float(x) - float(y)),
                "peak": max(abs(float(x)), abs(float(y))),
                "refused": octave and nm.startswith(JIT_PREFIX),
            })
    return rows


def red(rows: list[dict], tol_rel: float, unit: str | None = None) -> list[dict]:
    """Which pairs are over tolerance at `tol_rel`. The tolerance comes from
    `rate_tolerance` itself -- the shipped expression, called with the
    override, so this cannot drift from what the test applies."""
    out = []
    for r in rows:
        if r["refused"] or (unit is not None and r["unit"] != unit):
            continue
        if r["gap"] > D.rate_tolerance(r["column"], r["peak"], 0.0, tol_rel):
            out.append(r)
    return out


def crossing(r: dict) -> float:
    """The value of `RATE_TOL_REL` at or below which this pair goes red.

    `gap > max(tol_rel * peak, floor)` is unsatisfiable when `gap <= floor`:
    no value of the dial can reach the pair, which is a fact about the floor
    and not about this dial. Those return -1 and are counted separately.
    """
    floor = D.RATE_TOL_ABS[r["unit"]]
    if r["gap"] <= floor or r["peak"] <= 0.0:
        return -1.0
    return r["gap"] / r["peak"]


CYCLECORR = "jit.cyclecorr.seg0"


def cyclecorr_margin() -> dict:
    """THE ONE NUMBER THAT DECIDES WHETHER A MARGIN TEST CAN EXIST AT ALL.

    `max()` means the tolerance on a column can never fall below its unit's
    absolute floor, so the smallest tolerance this dial can put on
    `jit.cyclecorr.seg0` is `RATE_TOL_ABS["ratio"] = 0.02` -- at RATE_TOL_REL =
    0, with the relative term removed entirely. Measured against the whole
    effect the column reports (a steady 410 Hz square against the same square
    with 0.5 % per-period jitter) and against the column's own 44.1k->48k
    reproducibility on the jittered clip, which is the noise the effect has to
    stand out from.

    `test_the_rate_tolerance_is_far_below_the_effect` asserts this shape for
    `jit.phasejit_ppm` (0.78 ppm of artefact, a 20 ppm floor, a 14 534 ppm
    mechanism). The point of measuring it here is that for the correlation
    column the middle term is on the WRONG SIDE of the third, and no value of
    RATE_TOL_REL moves it.

    That citation is about the SHAPE of a margin and nothing else, because
    `jit.phasejit_ppm` is not a quotable number on its own: it cannot tell a
    drifting oscillator from two steady partials beating, and `jit.dphase_ar1`
    -- the lag-1 autocorrelation of the per-cycle period error -- is the column
    that can, so the two are never quoted apart (`docs/discrimination.md` 5c;
    `test_phasejit_is_not_promoted` enforces it). The 20 ppm floor above is the
    same `RATE_TOL_ABS["ppm"]` that `jit.dphase_ar1` refuses below as
    `JIT_DPHASE_FLOOR_PPM` (#763), which is why a reader checking this margin
    should check that one too rather than read phase jitter alone.
    """
    sr = SR_A
    read = lambda x, s=sr: dict(zip(*reversed(D.jitter_features(x, s))))  # noqa: E731
    steady = read(D._square(410.0, sr, jitter=0.0))[CYCLECORR]
    jit = D._square(410.0, sr, jitter=0.005, seed=3)
    a = read(jit)[CYCLECORR]
    b = read(D._resample(jit, SR_A, SR_B), SR_B)[CYCLECORR]
    effect = float(steady - a)
    gap = abs(float(a - b))
    # The two tolerances are named by their VALUE of the dial, not by "the
    # shipped one": the artefact has to say the same thing before and after
    # #775 moves what ships, or the record stops transcribing it.
    at_012 = D.rate_tolerance(CYCLECORR, a, b, 0.12)
    floor = D.rate_tolerance(CYCLECORR, a, b, 0.0)
    return {
        "effect": effect, "rate_gap": gap,
        "tol_at_012": at_012, "tol_floor_only": floor,
        "at_012_over_effect": at_012 / effect,
        "floor_over_effect": floor / effect,
        "effect_over_gap": effect / gap,
    }


def measure(grid, panel, panel_11=True) -> dict:
    shipped = measure_panel(panel, None)
    injected = measure_panel(panel, -1)
    n_refused = sum(1 for r in shipped if r["refused"])
    rows = []
    for t in grid:
        c = red(injected, t)
        s = red(shipped, t)
        rows.append({
            "tol_rel": t, "caught": float(len(c)), "cost": float(len(s)),
            "cost_db": float(len(red(shipped, t, "db"))),
            "cost_ratio": float(len(red(shipped, t, "ratio"))),
            "caught_ratio": float(len(red(injected, t, "ratio"))),
            "refused": float(n_refused),
        })
    units = sorted(D.RATE_TOL_ABS)
    by_unit = [{"tol_rel": t, **{u: float(len(red(shipped, t, u))) for u in units}}
               for t in grid]
    panel_rows = []
    if panel_11:
        for t in grid:
            over = D._rate_gaps(tol_rel=t)
            panel_rows.append({
                "tol_rel": t, "panel_red": float(len(over)),
                "panel_red_ratio": float(sum(1 for r in over
                                             if D._unit_of(r[1]) == "ratio")),
                "panel_entries": float(len({r[0] for r in over})),
            })
    # refused pairs are excluded here too: a #774 octave-error row has a huge
    # crossing value and would head this table while saying nothing about the
    # dial, which is the same reason it is out of both counts
    reach = sorted((r for r in shipped if not r["refused"] and crossing(r) > 0),
                   key=lambda r: -crossing(r))
    unreachable = sum(1 for r in shipped if not r["refused"] and crossing(r) < 0
                      and r["gap"] > 0)
    return {"rows": rows, "by_unit": by_unit, "units": units,
            "panel": panel_rows, "reach": reach, "margin": cyclecorr_margin(),
            "n_pairs": len(shipped), "n_entries": len(panel),
            "n_refused": n_refused, "n_unreachable": unreachable,
            "shipped": shipped, "injected": injected}


# ---------------------------------------------------------------------------
# the report


def _table(rows: list[dict], cols: list[str], widths: list[int]) -> list[str]:
    out = ["".join(f"{c:>{w}}" for c, w in zip(cols, widths))]
    for r in rows:
        cells = []
        for c, w in zip(cols, widths):
            v = r[c]
            cells.append(f"{v:>{w}}" if isinstance(v, str) else f"{_num(v):>{w}}")
        out.append("".join(cells))
    return out


def _num(v: float) -> str:
    """A plain decimal, because `tools/sensitivity.py`'s table parser reads
    `-?\\d+(?:\\.\\d+)?` and nothing else -- no exponent, no trailing point."""
    f = float(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.6f}".rstrip("0")


def report(res: dict, plan: dict, out) -> None:
    import platform

    import scipy
    w = lambda s="": print(s, file=out)  # noqa: E731
    w("The relative half of the 44.1 kHz <-> 48 kHz rate tolerance, swept through")
    w("rate_tolerance's own tol_rel override -- i.e. against the comparison that")
    w("ships. #775.")
    w(f"python {platform.python_version()}, numpy {np.__version__}, "
      f"scipy {scipy.__version__}.")
    w(f"Shipped value: RATE_TOL_REL = {D.RATE_TOL_REL:g}. Held fixed: "
      f"RATE_TOL_ABS = {D.RATE_TOL_ABS}, FOLD_SINC_TAPS = {D.FOLD_SINC_TAPS}.")
    w(f"Grid committed at {plan['_commit']} in "
      f"docs/sensitivity/rate-tol-rel-plan.json.")
    if plan["_dirty"]:
        w("NOT EVIDENCE: the plan was dirty or uncommitted when this ran, so the")
        w("grid is not known to predate the results. Do not cite this run.")
    w(f"Panel: {res['n_entries']} entries, {res['n_pairs']} (entry, column) pairs. "
      f"{res['n_refused']} jit.* pairs refused")
    w("for the octave error in dominant_period (#774); they are excluded from both")
    w(f"counts. {res['n_unreachable']} pairs have a non-zero gap that no value of this")
    w("dial can reach, because the gap is already under the unit's absolute floor.")
    w()
    w("rate_tol_rel -- the objective and the cost over the wide panel. caught is")
    w("  (entry, column) pairs the control turns red on the INTEGER FOLD #767")
    w("  replaced (taps=-1), the honest injected defect; higher is better. cost is")
    w("  the same count on the SHIPPED fold -- a clean panel going red. The _ratio")
    w("  columns are the correlation family #775 is about; note that cost_ratio and")
    w("  cost are not the same story, which is why the cost is also split by unit")
    w("  below.")
    w()
    for line in _table(res["rows"],
                       ["tol_rel", "caught", "cost", "cost_db", "cost_ratio",
                        "caught_ratio", "refused"],
                       [9, 9, 7, 9, 12, 14, 9]):
        w(line)
    w()
    w("rate_tol_rel_cost_by_unit -- the cost of the SHIPPED fold going red, split")
    w("  by the unit whose absolute floor applies. The acceptance criterion asks")
    w("  for this split because the floors stand in completely different relations")
    w("  to their columns' magnitudes: 0.02 against a correlation of ~1, 0.5 dB")
    w("  against a cqt column clamped at -80 dB.")
    w()
    for line in _table(res["by_unit"], ["tol_rel", *res["units"]],
                       [9, *[10] * len(res["units"])]):
        w(line)
    w()
    if res["panel"]:
        w("rate_tol_rel_shipped_panel -- the same cost on the ELEVEN-ENTRY RATE_PANEL,")
        w("  read through _rate_gaps(tol_rel=...) itself rather than re-evaluated")
        w("  here. This is the count that decides whether the suite is green, and")
        w("  every pair it reports needs a RATE_KNOWN_RED entry WITH A STATED REASON")
        w("  or a fix. panel_entries is how many distinct panel entries are involved.")
        w()
        for line in _table(res["panel"],
                           ["tol_rel", "panel_red", "panel_red_ratio",
                            "panel_entries"],
                           [9, 11, 17, 15]):
            w(line)
        w()
    w("rate_tol_rel_cyclecorr_margin -- the column #775 was filed on, and the")
    w("  reason this dial alone cannot give it a margin. effect is the whole")
    w("  separation jit.cyclecorr.seg0 reports between a steady 410 Hz square and")
    w("  the same square with 0.5 % per-period jitter; rate_gap is that jittered")
    w("  clip's own 44.1k->48k movement in the same column, i.e. the noise the")
    w("  effect stands out from. tol_at_012 is the tolerance the rate test applied")
    w("  at the RATE_TOL_REL = 0.12 that shipped before #775; tol_floor_only is the")
    w("  tolerance at RATE_TOL_REL = 0, which is RATE_TOL_ABS['ratio'] and is the")
    w("  SMALLEST this dial can ever make it. floor_over_effect above 1 means no")
    w("  value of RATE_TOL_REL puts the tolerance under the effect: the binding")
    w("  term is the floor, not the dial, which is why #775's margin test lands on")
    w("  its recorded-reason branch.")
    w()
    for line in _table([res["margin"]],
                       ["effect", "rate_gap", "tol_at_012", "tol_floor_only",
                        "at_012_over_effect", "floor_over_effect",
                        "effect_over_gap"],
                       [9, 10, 12, 16, 20, 19, 16]):
        w(line)
    w()
    w("The pairs this dial can reach, on the shipped fold, worst first: the value of")
    w("RATE_TOL_REL at or below which each goes red. A pair whose gap is already")
    w("under its unit's absolute floor is unreachable at any value and is not here.")
    w()
    w(f"{'crosses_below':>14} {'entry':<24} {'column':<28} {'unit':<6} "
      f"{'gap':>10} {'peak':>10}")
    for r in res["reach"][:24]:
        w(f"{crossing(r):>14.6f} {r['label']:<24} {r['column']:<28} "
          f"{r['unit']:<6} {r['gap']:>10.4f} {r['peak']:>10.4f}")
    w()


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="write the report here instead of stdout")
    ap.add_argument("--f0", type=float, nargs="+", default=None,
                    help="a narrower f0 ladder, for iterating. Not evidence.")
    ap.add_argument("--no-shipped-panel", dest="panel_11", action="store_false",
                    default=True, help="skip the eleven-entry RATE_PANEL table")
    ap.add_argument("--allow-dirty-plan", action="store_true",
                    help="measure anyway, and stamp the artefact as not evidence")
    args = ap.parse_args(argv)
    try:
        plan = load_plan(args.allow_dirty_plan)
        res = measure(plan["_grid"], wide_panel(args.f0), panel_11=args.panel_11)
    except Refused as exc:
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
