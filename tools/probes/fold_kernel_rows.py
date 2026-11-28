#!/usr/bin/env python3
"""The fold kernel's table against its formula, per (rows, taps) (#2112).

`model/discrimination_features.py` reads `_read_at`'s Hann-windowed sinc off
a cubic table over the fractional part of a position, FOLD_KERNEL_ROWS rows
to the unit. This is the sweep behind that number of rows: for every count of
rows on a grid and every half-width on the FOLD_SINC_TAPS grid, the largest
error of one tap and the largest error of their sum over a dense scan of
[0, 1], against the formula. It is arithmetic and nothing else -- the same
numbers on every host -- so it needs no bare twin and no load gate.

The shipped count of rows is always on the swept grid, whatever it is set to.
It FAILS (exit 1) if that row is over KERNEL_ERROR_BOUND at any width, or if
the control -- 64 rows -- is under it at every width, since a bound that the
coarsest table meets holds nothing.

    python tools/probes/fold_kernel_rows.py -o docs/fold-read-cost-2026-11-28/kernel-rows.txt
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import exit_contract                                          # noqa: E402

from model import discrimination_features as D                 # noqa: E402

ROWS = (64, 256, 1024, 4096, 8192, 16384)
WIDTHS = (1, 2, 4, 8, 16, 32)
CONTROL = 64
SCAN = 200_001
BLOCK = 20_000


def formula(frac: np.ndarray, taps: int) -> np.ndarray:
    """The kernel the table stands for, evaluated where it is read: the same
    expression `_fold_kernel` puts at the nodes, at every point of `frac`."""
    t = frac[:, None] - np.arange(-taps + 1, taps + 1, dtype=float)[None, :]
    return np.where(np.abs(t) < taps,
                    np.sinc(t) * (0.5 + 0.5 * np.cos(np.pi * t / taps)), 0.0)


def errors(frac: np.ndarray, taps: int, rows: int) -> tuple[float, float]:
    """(largest error of one tap, largest error of their sum) over `frac`."""
    table = D._fold_kernel(taps, rows)
    worst_one = worst_sum = 0.0
    for s in range(0, frac.size, BLOCK):
        f = frac[s:s + BLOCK]
        err = np.abs(D._kernel_weights(f, taps, table) - formula(f, taps))
        worst_one = max(worst_one, float(err.max()))
        worst_sum = max(worst_sum, float(err.sum(axis=1).max()))
    return worst_one, worst_sum


def sweep(scan: int, grid: tuple) -> list[tuple]:
    """(rows, taps, largest error of one tap, largest error of their sum)."""
    frac = np.linspace(0.0, 1.0, scan)
    return [(rows, taps, *errors(frac, taps, rows)) for rows in grid for taps in WIDTHS]


def judge(rows: list[tuple], bound: float, shipped: int) -> tuple[str, str]:
    """(verdict, why): the shipped count under the bound at every width, and
    the control over it at every width."""
    mine = [(taps, one, total) for row, taps, one, total in rows if row == shipped]
    ctrl = [(taps, one, total) for row, taps, one, total in rows if row == CONTROL]
    over = [taps for taps, one, total in mine if max(one, total) > bound]
    if over:
        return "FAIL", f"at {shipped} rows the table is over {bound:g} at widths {over}"
    under = [taps for taps, one, total in ctrl if max(one, total) <= bound]
    if under:
        return "FAIL", (f"the {CONTROL}-row control is under {bound:g} at widths {under}: "
                        "the bound holds nothing")
    return "OK", (f"FOLD_KERNEL_ROWS = {shipped} is under {bound:g} at every width; "
                  f"the {CONTROL}-row control is over it at every width")


def report(rows: list[tuple], scan: int, bound: float, shipped: int, verdict: str,
           why: str) -> str:
    L = [f"fold_kernel_rows: {verdict} -- #2112. The table against the formula over "
         f"{scan} points of [0, 1].",
         "   rows   taps   max|error| of one tap   max|error| of their sum   shipped",
         "   " + "-" * 66]
    for row, taps, one, total in rows:
        L.append(f"  {row:>5} {taps:>6}   {one:>21.3e}   {total:>24.3e}   "
                 f"{'FOLD_KERNEL_ROWS' if row == shipped else '-'}")
    return "\n".join(L + [f"bound KERNEL_ERROR_BOUND = {bound:g}", why]) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=pathlib.Path, help="write the table here too")
    ap.add_argument("--scan", type=int, default=SCAN, help="points of [0, 1] to scan")
    ap.add_argument("--bound", type=float, default=None,
                    help="the bound to judge by (default: the module's KERNEL_ERROR_BOUND)")
    a = ap.parse_args(argv)
    bound = float(D.KERNEL_ERROR_BOUND if a.bound is None else a.bound)
    shipped, scan = int(D.FOLD_KERNEL_ROWS), max(2, a.scan)
    rows = sweep(scan, ROWS if shipped in ROWS else (*ROWS, shipped))
    verdict, why = judge(rows, bound, shipped)
    text = report(rows, scan, bound, shipped, verdict, why)
    sys.stdout.write(text)
    if a.out:
        a.out.write_text(text)
    return exit_contract.PASS if verdict == "OK" else exit_contract.FAIL


if __name__ == "__main__":
    sys.exit(main())
