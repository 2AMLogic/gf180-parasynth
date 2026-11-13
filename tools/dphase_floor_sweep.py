#!/usr/bin/env python3
"""Sweep the floor under `jit.dphase_ar1.seg0` before fixing it (#763).

    tools/dphase_floor_sweep.py                 the report, to stdout
    tools/dphase_floor_sweep.py -o docs/sensitivity/dphase-ar1-floor-sweep.txt

WHAT IS BEING SWEPT. `model/discrimination_features.JIT_DPHASE_FLOOR_PPM` is
the phase scatter, in ppm of a period, below which that column REFUSES
instead of reporting. Set it too low and the column reports the lag-1
autocorrelation of float rounding at 44.1 kHz and of `resample_poly` residue
at 48 kHz -- two different noises, so the same clip reads two different
numbers and the column is partly a sample-rate detector. Set it too high and
it refuses a real mechanism, which is the thing it exists to see.

WHAT IT IS SWEPT AGAINST. Two panels, because the dial is bounded from both
sides and one number cannot say so:

  rate_gap   the largest 44.1 kHz -> 48 kHz gap in `jit.dphase_ar1.seg0` over
             RATE_PANEL, each entry measured at both rates through the
             module's own band-limited resampler. The objective the
             sensitivity record carries (lower is better). It bounds the
             floor FROM BELOW.
  sees_real  how many of MECHANISM_PANEL the column still reports, of
             `n_mech`. A CONSTRAINT, not an objective: rate_gap only improves
             as the column is silenced, so on rate_gap alone an infinite
             floor wins. MECHANISM_PANEL is deliberately the stimulus set
             `test_static_detuning_is_not_reported_as_drift` depends on, so
             the row where sees_real drops is the row where that control goes
             SILENT rather than red -- the worst way for a control to fail.

THE RESULT THAT WAS WRONG BEFORE IT WAS RIGHT, which is the more useful half
of this file. The first version swept a floor on `np.diff(lin).std()` -- the
per-cycle period error, the quantity `ar1` is literally computed from, and so
the obvious one to gate on. It is the wrong one. Static detuning of +0.5 Hz
over 0.24 s is only 12 % of a beat cycle, so its per-cycle period error is
4.56 ppm while its phase scatter is 54.53 ppm and its `ar1` is 0.948: a floor
on the differenced residual has to fit between 0.80 and 4.56 ppm, a factor of
5.7, and a 20 ppm floor there silences the beating control outright (measured:
`beating 0.000 vs jitter 0.677: the gap has closed`). On the phase scatter the
same two edges are 0.78 and 54.53 ppm, a factor of 70. The dial moved onto the
phase scatter for that measured reason, not because it was handier.

HOW IT IS MEASURED. Through `jitter_features` itself, with its
`dphase_floor_ppm` override and its `trace` hook -- not through a
reimplementation of the gate. A sweep says what the shipped code does at each
value of the dial, and a copy of the gate could not. Each jittered entry
asserts that the generator's whole-sample period rounding did not round its
jitter away; an entry that fails that precondition REFUSES rather than being
reported, because a positive control that is secretly jitterless proves the
opposite of what it claims.

Exit 0 pass, 2 REFUSED -- this repository's convention, where 2 means "could
not answer", which is not the same as "no problem".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model.discrimination_features import (  # noqa: E402
    JIT_DPHASE_FLOOR_PPM, _rate_stimulus, _resample, _square, _square_periods,
    jitter_features,
)

SR_REF, SR_RENDER = 44100, 48000

# The grid, stated before the measurement: five decades bracketing the two
# quantities this module already has numbers for -- the 44.1k -> 48k phase
# artefact at ~0.8 ppm (which is what RATE_TOL_ABS["ppm"] = 20 is set
# against) and 0.1 % per-period jitter at ~5.7e3 ppm of phase scatter. 0 is
# the behaviour that shipped before #763: the guard was `d.std() > _EPS` with
# _EPS = 1e-20, which nothing real is ever below, so 0 and that epsilon are
# the same point on this dial.
GRID = (0.0, 0.2, 2.0, 20.0, 200.0, 2000.0)

# (label, f0_hz, jitter, seed) -- measured at BOTH rates. Jitterless squares
# are the defect: a phase accumulator has no period instability at all, which
# is exactly what our renders are. The jittered entries are the positive
# control, so that a floor which silenced the column everywhere could not
# score a perfect rate_gap unnoticed.
RATE_PANEL = (
    ("330Hz", 330.0, 0.0, 0),
    ("440Hz", 440.0, 0.0, 0),
    ("660Hz", 660.0, 0.0, 0),
    ("330Hz+0.1%jit", 330.0, 0.001, 3),
    ("410Hz+0.5%jit", 410.0, 0.005, 3),
)

# What the column must still answer on, at 44.1 kHz. These are exactly the
# stimuli test_static_detuning_is_not_reported_as_drift reads: three static
# detunings (which BEAT, and must land above the jitter), three jittered
# squares, and a pitch glide. +0.5 Hz is the smallest, and it is the edge
# that actually binds this dial.
MECHANISM_PANEL = (
    ("detune+0.5Hz", "beat", 0.5),
    ("detune+2Hz", "beat", 2.0),
    ("detune+8Hz", "beat", 8.0),
    ("jitter0.1%", "jitter", 0.001),
    ("jitter0.5%", "jitter", 0.005),
    ("jitter2%", "jitter", 0.02),
    ("glide", "glide", 0.0),
)


class Refused(Exception):
    """The sweep could not answer. Never reported as a pass."""


def read(x: np.ndarray, sr: int, floor: float) -> tuple[float, float, float, bool]:
    """(dphase_ar1, phase scatter ppm, per-cycle period error ppm, refused?)."""
    trace: dict = {}
    vals, names = jitter_features(x, sr, dphase_floor_ppm=floor, trace=trace)
    ar1 = float(dict(zip(names, vals))["jit.dphase_ar1.seg0"])
    if not trace:
        # an early return: the window did not hold JIT_MIN_CYCLES, so the
        # whole row is refused and this dial had nothing to do with it
        return ar1, float("nan"), float("nan"), True
    return (ar1, float(trace["phasejit_ppm"]), float(trace["dphase_scatter_ppm"]),
            bool(trace["refused"]))


def _check_jitter_survived(f0: float, jitter: float, seed: int, sr: int) -> None:
    ks = _square_periods(f0, sr, jitter=jitter, seed=seed)
    if len(set(ks)) <= 1:
        raise Refused(
            f"{f0:g} Hz at {jitter:.3%}: the generator rounded the jitter away "
            f"(every period is {ks[0]} samples), so this entry is a jitterless "
            "clip wearing a positive control's name")


def rate_pairs() -> dict:
    out = {}
    for label, f0, j, seed in RATE_PANEL:
        if j:
            _check_jitter_survived(f0, j, seed, SR_REF)
        a = _rate_stimulus(f0, j, seed, sr=SR_REF)
        out[label] = (a, _resample(a, SR_REF, SR_RENDER))
    return out


def mechanisms() -> dict:
    sr = SR_REF
    t = np.arange(int(0.24 * sr)) / sr
    out = {}
    for label, kind, arg in MECHANISM_PANEL:
        if kind == "beat":
            out[label] = np.sin(2 * np.pi * 410.0 * t) + 0.7 * np.sin(
                2 * np.pi * (410.0 + arg) * t)
        elif kind == "jitter":
            _check_jitter_survived(410.0, arg, 3, sr)
            out[label] = _square(410.0, sr, jitter=arg, seed=3)
        else:
            out[label] = np.sin(2 * np.pi * (410.0 * t + 0.5 * 410.0 * 0.01 / 0.24 * t ** 2))
    return out


def measure(grid=GRID) -> dict:
    pairs, mech = rate_pairs(), mechanisms()
    rows, detail, mdetail = [], [], []
    for floor in grid:
        gaps = []
        for label, _, j, _ in RATE_PANEL:
            a, b = pairs[label]
            ar1_a, pj_a, ds_a, ref_a = read(a, SR_REF, floor)
            ar1_b, pj_b, ds_b, ref_b = read(b, SR_RENDER, floor)
            detail.append({"floor": floor, "label": label, "pj_ref": pj_a, "pj_render": pj_b,
                           "ds_ref": ds_a, "ar1_ref": ar1_a, "ar1_render": ar1_b,
                           "refused": ref_a and ref_b})
            gaps.append(abs(ar1_a - ar1_b))
        seen = 0
        for label, _, _ in MECHANISM_PANEL:
            ar1, pj, ds, ref = read(mech[label], SR_REF, floor)
            mdetail.append({"floor": floor, "label": label, "pj": pj, "ds": ds,
                            "ar1": ar1, "refused": ref})
            seen += int(not ref)
        rows.append({"floor_ppm": floor, "rate_gap": max(gaps),
                     "sees_real": float(seen), "n_mech": float(len(MECHANISM_PANEL))})
    return {"rows": rows, "detail": detail, "mdetail": mdetail}


def report(res: dict, out) -> None:
    """A report for a human that `tools/sensitivity.py` can also parse: the
    `dphase_floor` section is a fixed-width table in the shape
    `parse_fixed_width_tables` reads, and every row of the swept grid is in
    it -- a point dropped from the artefact is a point dropped from the grid,
    which is how a plateau gets manufactured."""
    import platform

    import scipy
    print("The floor under jit.dphase_ar1.seg0, swept through jitter_features' own", file=out)
    print("dphase_floor_ppm override -- i.e. against the code that ships. #763.", file=out)
    print(f"python {platform.python_version()}, numpy {np.__version__}, "
          f"scipy {scipy.__version__}.", file=out)
    print(f"Shipped value: JIT_DPHASE_FLOOR_PPM = {JIT_DPHASE_FLOOR_PPM:g} (ppm of a "
          "period, read on the phase scatter).", file=out)
    print(file=out)
    print("dphase_floor -- largest 44.1 kHz -> 48 kHz gap in the column over the rate", file=out)
    print("  panel, in ar1 units, against the floor in ppm. sees_real is the", file=out)
    print("  CONSTRAINT from the other side: mechanism-panel stimuli still reported,", file=out)
    print("  of n_mech. Lower rate_gap is better; sees_real must stay at n_mech.", file=out)
    print("floor_ppm  rate_gap  sees_real  n_mech", file=out)
    for r in res["rows"]:
        print(f"{r['floor_ppm']:<10g} {r['rate_gap']:<9.4f} {r['sees_real']:<10g} "
              f"{r['n_mech']:g}", file=out)
    print(file=out)
    print("dphase_floor_rate_detail -- the rate panel per stimulus. pj is the phase", file=out)
    print("  scatter in ppm, the quantity the floor is compared against; ds is the", file=out)
    print("  per-cycle period error, the quantity the FIRST attempt gated on and the", file=out)
    print("  reason it failed (see the module docstring).", file=out)
    print(f"{'floor_ppm':>9} {'stimulus':<16} {'pj_ref':>11} {'pj_render':>11} "
          f"{'ds_ref':>11} {'ar1_ref':>9} {'ar1_render':>11}  refused", file=out)
    for d in res["detail"]:
        print(f"{d['floor']:>9g} {d['label']:<16} {d['pj_ref']:11.4f} "
              f"{d['pj_render']:11.4f} {d['ds_ref']:11.4f} {d['ar1_ref']:9.4f} "
              f"{d['ar1_render']:11.4f}  {'yes' if d['refused'] else 'no'}", file=out)
    print(file=out)
    print("dphase_floor_mechanism_detail -- what the column must still answer on, at", file=out)
    print("  44.1 kHz. A 'yes' here is the beating control going silent, not red.", file=out)
    print(f"{'floor_ppm':>9} {'stimulus':<16} {'pj':>11} {'ds':>11} {'ar1':>9}"
          "  refused", file=out)
    for d in res["mdetail"]:
        print(f"{d['floor']:>9g} {d['label']:<16} {d['pj']:11.2f} {d['ds']:11.2f} "
              f"{d['ar1']:9.4f}  {'yes' if d['refused'] else 'no'}", file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="write the report here instead of stdout")
    args = ap.parse_args(argv)
    try:
        res = measure()
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.out:
        with args.out.open("w") as fh:
            report(res, fh)
        print(f"wrote {args.out}")
    else:
        report(res, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
