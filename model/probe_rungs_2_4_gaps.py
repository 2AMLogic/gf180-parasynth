#!/usr/bin/env python3
"""The four things `model/test_ladder_candidates.py` asserted and the harness
did not do. Kept because three of the four were the TEST being wrong about the
apparatus, and one was the apparatus being right in a way nobody had predicted.

    python3 model/probe_rungs_2_4_gaps.py            # all four
    python3 model/probe_rungs_2_4_gaps.py --only 3

Run in the state the first rungs-2-4 commits left the branch in, every one of
these was a red test with no number attached to it. The point of this file is
that each answer below is a MEASUREMENT that decided how the test was changed,
not a rationalisation written after changing it.

  1  peak-locator ground truth. The test built its probe grid around the
     CUTOFF; `peak_probe` builds it around the ANALYTIC PEAK, and says so in
     `analytic_peak`'s docstring ("at res = 0.5 it is at 0.80 x, which fell
     outside a window centred on the cutoff"). The apparatus had already been
     fixed and the test had not. Prints where the peak actually is.

  2  declared cost. `NewtonCore.MULS` is set in `__init__` from `iters`, and
     the test instantiated the core WITHOUT the `iters` kwarg `CORES` carries,
     so it compared the 3-iteration core's instrumented count against the
     2-iteration default's declaration. Prints both.

  3  the delay-free ladder's oscillation limit. The closed form says it cannot
     sing above 5295.6 Hz AT ANY FEEDBACK. The test then asserted it DOES sing
     below that at res = 1.45 -- which is a different claim, and the one the
     closed form does not make: below the limit the phase is available but the
     loop still needs enough gain to use it. Sweeps resonance at each cutoff
     and prints the resonance at which each first sings, so the test can assert
     the prediction instead of a stronger thing that happens to be near it.

  4  the dropped-pole control. Three poles instead of four moves the peak out
     of the four-pole search window entirely, so the linear stage REFUSES
     rather than returning a moved number. That is red -- louder than red --
     but the test asserted a dB move and got `nan`. Prints what the control
     does at every point on the linear grid, and what the always-measurable
     passband number does, so the control can assert both routes explicitly.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ladder_candidates as lc                                      # noqa: E402
import voice_fx as vf                                               # noqa: E402
import compare_ladder_candidates as cc                              # noqa: E402


def probe_1_peak_locator() -> dict:
    """Where the closed form's peak sits relative to the cutoff, and what the
    locator returns when the grid is centred there instead of on the cutoff."""
    rows = {}
    for cut, res in ((400.0, 0.9), (1600.0, 0.5), (6400.0, 0.9), (1600.0, 0.9)):
        f_a, g_a = lc.analytic_peak(cut, res)
        grid = np.unique(lc.snap_freqs(
            f_a * np.geomspace(*lc.PEAK_SPAN, lc.PEAK_POINTS)))
        f_hat, g_hat = lc.peak_of(grid, lc.analytic_response_db(cut, res, grid))
        on_cut = np.unique(lc.snap_freqs(
            cut * np.geomspace(*lc.PEAK_SPAN, lc.PEAK_POINTS)))
        try:
            lc.peak_of(on_cut, lc.analytic_response_db(cut, res, on_cut))
            cut_centred = "returned a number"
        except lc.Refused as exc:
            cut_centred = f"REFUSED: {exc}"
        rows[f"{cut:.0f}/{res}"] = dict(
            peak_over_cutoff=round(f_a / cut, 4),
            cents_err=round(1200.0 * math.log2(f_hat / f_a), 3),
            height_err_db=round(g_hat - g_a, 4),
            grid_centred_on_cutoff=cut_centred)
    return rows


def probe_2_declared_cost() -> dict:
    """Instrumented counts against each core's own declaration, instantiated
    the way `CORES` says to instantiate it."""
    x = np.zeros(200)
    x[0] = 20000.0
    rows = {}
    for name, (cls, kw) in lc.CORES.items():
        if name == "null":
            continue
        cand = lc.Candidate(name)
        cand.render(x, 1600.0, 1.2)
        c = cand.last_cost
        rows[name] = dict(
            tanh=c["tanh"], divide=c["divide"], multiply=c["multiply"],
            declared_with_kw=cls(**vf.LADDER_CFG, **kw).MULS,
            declared_without_kw=cls(**vf.LADDER_CFG).MULS)
    return rows


def probe_3_delay_free_limit(resonances=(1.05, 1.45, 2.00, 3.00, 4.00),
                             cuts=(800.0, 1600.0, 3200.0, 3600.0, 4000.0,
                                   4800.0, 6400.0),
                             names=("zdf-explicit", "shipped")) -> dict:
    """The lowest resonance at which each ladder sings, at each cutoff, against
    the closed form's `delay_free_oscillation_limit_hz`.

    The point of sweeping resonance rather than asserting at one value: the
    closed form bounds the PHASE, and a phase that exists is not a phase the
    loop can reach at a gain the instrument can command. The two ceilings are
    different numbers and only one of them is the one a player meets."""
    limit = lc.delay_free_oscillation_limit_hz("expo")
    rows = {"limit_hz": round(limit, 1)}
    for name in names:
        for cut in cuts:
            first, points = None, {}
            for res in resonances:
                r = cc.stage_resonance([name], (cut,), (res,))[name]
                p = r["points"][f"{cut:.0f}/{res}"]
                points[res] = dict(sings=p.get("sings"),
                                   sustain_db=p.get("sustain_db"),
                                   cents=p.get("cents"))
                if first is None and p.get("sings"):
                    first = res
            rows[f"{name} {cut:.0f}"] = dict(
                below_limit=cut < limit, first_singing_res=first,
                stage_max_lag_deg=round(lc.stage_max_lag_deg("expo", cut), 2),
                points=points)
    return rows


def probe_4_dropped_pole(cuts=(400.0, 1600.0, 6400.0), res=(0.5, 0.9)) -> dict:
    """What three poles instead of four does to every number the linear stage
    reports, including the passband gain, which needs no peak search and so
    cannot refuse."""
    ok = cc.stage_linear(["shipped"], cuts, res)["shipped"]
    bad = cc.stage_linear(["shipped"], cuts, res, stages=3)["shipped"]
    return dict(four_poles=ok, three_poles=bad,
                refused_points=bad["refused"], total_points=len(bad["points"]))


PROBES = {1: probe_1_peak_locator, 2: probe_2_declared_cost,
          3: probe_3_delay_free_limit, 4: probe_4_dropped_pole}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", type=int, choices=sorted(PROBES), action="append")
    a = ap.parse_args(argv)
    import json
    for i in (a.only or sorted(PROBES)):
        print(f"\n===== probe {i}: {PROBES[i].__name__} " + "=" * 30)
        print(json.dumps(PROBES[i](), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
