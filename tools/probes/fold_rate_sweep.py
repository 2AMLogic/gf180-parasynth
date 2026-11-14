#!/usr/bin/env python3
"""Does a fold read the oscillator, or the sample grid it was written on? #767

THE QUESTION. `model/discrimination_features.py` compares a 44.1 kHz
reference corpus against 48 kHz renders, so any column that moves between the
two rates on the SAME clip is partly a sample-rate detector --
`docs/failure-modes.md`'s shape, an answer in the place where evidence
belongs. `test_extra_features_are_rate_independent` is the assertion; this is
the instrument that measures WHY it failed, over a grid wide enough that the
answer is not a property of which f0 values the test panel happens to hold.

WHAT IT MEASURES, PER (f0, direction, taps):

    gap        |column at rate A - column at rate B|, for every jit.* and
               mpd*.rowcorr column, against the module's own tolerance
               expression (RATE_TOL_REL / RATE_TOL_ABS).
    dp_err     `dominant_period`'s fractional error to the true period, in
               percent -- the misalignment the `jit.cycledshape` story
               predicts the gap should follow.
    stride_err `round(sr*T) / (sr*T) - 1`, in percent -- the misalignment the
               `mpd*.rowcorr` story predicts ITS gap should follow.

BOTH RESAMPLING DIRECTIONS, because the stimulus is a confound. `_square`
emits whole-sample periods, so a square built at 44.1 kHz is EXACTLY
integer-periodic there and fractional only after resampling; a one-direction
sweep cannot tell "follows the sample rate" from "follows whichever side was
resampled". Building at 48 kHz and resampling down is the control for that,
and it is why `cycledshape` and `mpd*.rowcorr` turn out to be two mechanisms
rather than one.

THE DIAL. `--taps` sweeps FOLD_SINC_TAPS, the fold interpolator's kernel
half-width, THROUGH THE SHIPPED FUNCTIONS -- `mpd_features(..., taps=)` and
`jitter_features(..., taps=)` are overrides on the code that ships, not a
reimplementation of it. `taps=-1` selects the integer-lag fold that shipped
before #767, so the same instrument reproduces the defect and measures the
repair. The sweep record is docs/sensitivity/fold-interpolator.json.

    tools/probes/fold_rate_sweep.py -o docs/sensitivity/fold-interpolator-sweep.txt
    tools/probes/fold_rate_sweep.py --taps -1 --f0 440 660 --detail

Its validation cases -- including a stimulus whose period is a whole number of
samples at BOTH rates, where the answer is known from arithmetic and not from
this module -- are in tools/probes/test_fold_rate_sweep.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model import discrimination_features as D  # noqa: E402

SR_A, SR_B = 44100, 48000

# The eleven f0 values #767 was filed on, plus a 25 Hz ladder between them, so
# the answer cannot be a property of which values the panel happened to hold.
# 300 Hz is on the ladder ON PURPOSE: 44100/300 and 48000/300 are both whole
# numbers, so its fold is exactly aligned at both rates and the instrument must
# report no excess there. That is the validation case with an answer known
# independently of this module -- see test_fold_rate_sweep.py.
F0_FILED = (220.0, 262.0, 330.0, 392.0, 440.0, 523.0, 587.0, 660.0, 784.0,
            880.0, 990.0)
F0_GRID = tuple(sorted(set(F0_FILED) | set(float(f) for f in range(200, 1001, 25))))

TAPS_GRID = (-1, 0, 1, 2, 4, 8, 16, 32)

JIT_COLS = ("jit.cyclecorr.seg0", "jit.cycledshape.seg0")
OBJECTIVE_COL = "jit.cycledshape.seg0"


# ---------------------------------------------------------------------------
# the measurement


def tolerance(name: str, a: float, b: float) -> float:
    """The module's OWN tolerance expression, imported rather than restated so
    the probe cannot drift from the test it explains."""
    return max(D.RATE_TOL_REL * max(abs(a), abs(b)), D.RATE_TOL_ABS[D._unit_of(name)])


def read(x: np.ndarray, sr: int, taps: int) -> dict:
    """The two fold families, through the shipped functions."""
    out = {}
    for vals, names in (D.mpd_features(x, sr, taps), D.jitter_features(x, sr, taps=taps)):
        out.update(zip(names, vals))
    return out


def dp_fractional_error_pct(x: np.ndarray, sr: int, f0: float) -> float:
    """How far `dominant_period`'s integer lag is from the true period, in
    percent of the true period. The quantity the `cycledshape` story says the
    gap should follow; `f0` is the stimulus's own nominal, not a fit."""
    p = D.dominant_period(x, sr)
    if p < 4:
        return float("nan")
    true_p = sr / f0
    return 100.0 * (p - true_p) / true_p


def stride_error_pct(sr: int, T: float) -> float:
    """`round(sr*T)` against `sr*T`, in percent. Zero wherever the stride is a
    whole number of samples -- which 5.0 ms is at 48 kHz and is not at
    44.1 kHz, the asymmetry #767 is about."""
    return 100.0 * (round(sr * T) - sr * T) / (sr * T)


def stimulus(f0: float, sr: int, jitter: float = 0.0, seed: int = 0,
             kind: str = "square") -> np.ndarray:
    """One clip at `sr`, by the module's own generators and decay."""
    return D._rate_stimulus(f0, jitter, seed, sr=sr, kind=kind)


OCTAVE_TOL_REL = 0.05


def octave_refused(a: np.ndarray, sr_a: int, b: np.ndarray, sr_b: int,
                   octave_margin: float | None = None) -> bool:
    """THE APPARATUS'S PRECONDITION FOR EVERY jit COLUMN, as one predicate.

    The jit columns answer about the partial `dominant_period` picked, so two
    sides of a rate pair that picked DIFFERENT partials are not measuring the
    same thing and a fold-geometry gap read off them is not about fold
    geometry. True means the pair is REFUSED for the objective -- a third
    outcome, not a pass.

    It lives here, as the one definition, because #774's dial is swept against
    exactly this statistic: `tools/probes/octave_margin_sweep.py` imports it
    rather than restating it, so a margin that drives this probe's `refused`
    count to zero cannot be a margin measured against a different test.
    `octave_margin` is passed through to `dominant_period` so the sweep drives
    the shipped function."""
    ta = D.dominant_period(a, sr_a, octave_margin=octave_margin) / float(sr_a)
    tb = D.dominant_period(b, sr_b, octave_margin=octave_margin) / float(sr_b)
    return not (ta > 0 and tb > 0 and abs(tb / ta - 1.0) < OCTAVE_TOL_REL)


def period_ratio(a: np.ndarray, sr_a: int, b: np.ndarray, sr_b: int,
                 octave_margin: float | None = None) -> float:
    """`dominant_period`'s period at `sr_b` over its period at `sr_a`, for the
    same clip. 1.0 when the two rates agree; ~2 or ~0.5 on an octave error.
    The continuous form of `octave_refused`, so the dial has an objective that
    is a measurement rather than a count of threshold crossings."""
    ta = D.dominant_period(a, sr_a, octave_margin=octave_margin) / float(sr_a)
    tb = D.dominant_period(b, sr_b, octave_margin=octave_margin) / float(sr_b)
    return (tb / ta) if ta > 0 else float("nan")


def pair(f0: float, build_sr: int, taps: int, kind: str = "square") -> dict:
    """One (f0, direction, taps) row: build at `build_sr`, resample to the
    other rate, read both sides."""
    other = SR_B if build_sr == SR_A else SR_A
    a = stimulus(f0, build_sr, kind=kind)
    b = D._resample(a, build_sr, other)
    ra, rb = read(a, build_sr, taps), read(b, other, taps)
    gaps = {}
    for nm in ra:
        if not (nm in JIT_COLS or (nm.startswith("mpd") and ".rowcorr." in nm)):
            continue
        gaps[nm] = (abs(ra[nm] - rb[nm]), tolerance(nm, ra[nm], rb[nm]), ra[nm], rb[nm])
    # THE APPARATUS'S PRECONDITION, ASSERTED RATHER THAN ASSUMED: a pair whose
    # two sides picked different partials is REFUSED for the objective, which
    # is a third outcome and not a pass. The mpd columns do not use
    # `dominant_period` and are unaffected. #774 repaired the octave pick, so
    # this now refuses nothing on the shipped grid -- it is kept because a
    # precondition that stops being checked the day it stops firing is not a
    # precondition.
    # `octave_refused` is the single definition of this predicate -- see it.
    refused = octave_refused(a, build_sr, b, other)
    return {
        "f0": f0, "build_sr": build_sr, "other_sr": other, "taps": taps, "kind": kind,
        "gaps": gaps, "jit_refused": refused,
        "period_ratio": period_ratio(a, build_sr, b, other),
        "dp_err_build": dp_fractional_error_pct(a, build_sr, f0),
        "dp_err_other": dp_fractional_error_pct(b, other, f0),
    }


def sweep(taps_grid=TAPS_GRID, f0_grid=F0_GRID, kinds=("square",)) -> list[dict]:
    rows = []
    for taps in taps_grid:
        for kind in kinds:
            for f0 in f0_grid:
                for build_sr in (SR_A, SR_B):
                    rows.append(pair(f0, build_sr, taps, kind))
    return rows


# ---------------------------------------------------------------------------
# the controls that bound the dial from the other side
#
# A fold that aligns every row to the waveform could pass the rate test by
# destroying what the columns exist to see, so the objective is never read
# alone. Both of these are the property an existing self-test asserts, read
# here as a number so the sweep can show where on the dial it breaks.


def walks_at_strides(taps: int) -> int:
    """How many of the six MPD strides a PERFECTLY STABLE tone still walks at.

    `test_a_fixed_stride_fold_is_not_a_drift_measure`: the strides are not
    commensurate with any oscillator here, so a stable tone must still move
    from row to row. If cycle-aligning the fold made this fall, the MPD
    columns would have quietly become a drift view, which they are not."""
    sr = SR_B
    t = np.arange(int(0.24 * sr)) / sr
    x = np.sin(2 * np.pi * 131.0 * t)
    return sum(D._fold_stats(D._fold_at(x, sr * T, taps))["dshape"] > 0.05
               for T in D.MPD_PERIODS_S)


def jitter_margin(taps: int) -> float:
    """`jit.cyclecorr` of a stable square minus that of a jittered one.

    `test_jitter_separates_a_stable_oscillator_from_a_drifting_one` requires
    this above 0.02. It is the constraint that matters most here: folding at
    the oscillator's own refined period is exactly what would hide per-cycle
    jitter if it were taken too far."""
    sr = SR_A
    s = dict(zip(*reversed(D.jitter_features(D._square(410.0, sr), sr, taps=taps))))
    j = dict(zip(*reversed(D.jitter_features(
        D._square(410.0, sr, jitter=0.005, seed=3), sr, taps=taps))))
    return float(s["jit.cyclecorr.seg0"] - j["jit.cyclecorr.seg0"])


# ---------------------------------------------------------------------------
# the report


def summarise(rows: list[dict]) -> list[dict]:
    """One row per taps value: the worst gap each mechanism reaches anywhere
    on the grid, plus the two controls."""
    out = []
    for taps in sorted({r["taps"] for r in rows}):
        mine = [r for r in rows if r["taps"] == taps]
        ok = [r for r in mine if not r["jit_refused"]]
        jit_gap = max(r["gaps"][OBJECTIVE_COL][0] for r in ok
                      if OBJECTIVE_COL in r["gaps"])
        mpd_gap = max((g[0] for r in mine for nm, g in r["gaps"].items()
                       if nm.startswith("mpd")), default=float("nan"))
        n_over = sum(1 for r in ok for g in r["gaps"].values() if g[0] > g[1])
        out.append({"taps": taps, "rate_gap": round(jit_gap, 4),
                    "mpd_gap": round(mpd_gap, 4), "n_over": n_over,
                    "refused": len(mine) - len(ok),
                    "walks": walks_at_strides(taps),
                    "sees_jit": round(jitter_margin(taps), 4)})
    return out


def _fmt(table: list[dict], cols: list[str], widths: list[int]) -> list[str]:
    lines = ["".join(f"{c:>{w}}" for c, w in zip(cols, widths))]
    for row in table:
        cells = []
        for c, w in zip(cols, widths):
            v = row[c]
            cells.append(f"{v:>{w}}" if isinstance(v, (int, np.integer))
                         else f"{v:>{w}.4f}")
        lines.append("".join(cells))
    return lines


def report(rows: list[dict], detail: bool = True) -> str:
    out = [
        "fold_rate_sweep -- #767. The 44.1 kHz <-> 48 kHz gap in the two fold",
        "families, swept over the fold interpolator's kernel half-width. taps=-1",
        "is the integer-lag fold that shipped before the repair. Both resampling",
        f"directions and {len({r['f0'] for r in rows})} values of f0 are behind every row.",
        "",
        "fold_interpolator -- worst gap anywhere on the grid, and the two controls",
        "that bound the dial from the other side. rate_gap is the objective",
        "(jit.cycledshape.seg0, REFUSED pairs excluded -- see pair()); walks is",
        "how many of the six MPD strides a stable tone still walks at, and must",
        "stay at 6; sees_jit is how far jit.cyclecorr falls on a 0.5 %-jittered",
        "square, and is the constraint that bounds the dial from above.",
        "",
    ]
    summary = summarise(rows)
    out += _fmt(summary, ["taps", "rate_gap", "mpd_gap", "n_over", "refused",
                          "walks", "sees_jit"],
                [6, 10, 10, 8, 9, 7, 10])
    out.append("")
    if not detail:
        return "\n".join(out) + "\n"
    for taps in sorted({r["taps"] for r in rows}):
        out += [
            f"detail_taps{taps if taps >= 0 else 'legacy'} -- per f0 and direction at "
            f"taps={taps}. dp_err is dominant_period's",
            "fractional error to the true period in percent, on the resampled side;",
            "cycledshape and mpd_worst are gaps, tol is the module's own tolerance.",
            "",
        ]
        det = []
        for r in sorted((r for r in rows if r["taps"] == taps),
                        key=lambda r: (r["f0"], r["build_sr"])):
            if OBJECTIVE_COL not in r["gaps"]:
                continue
            g = r["gaps"][OBJECTIVE_COL]
            mw = max(((v[0], nm) for nm, v in r["gaps"].items() if nm.startswith("mpd")),
                     default=(float("nan"), ""))
            det.append({
                "f0": round(r["f0"], 1), "build_sr": r["build_sr"],
                "dp_err": round(r["dp_err_other"], 4),
                "cycledshape": round(g[0], 4), "tol": round(g[1], 4),
                "mpd_worst": round(mw[0], 4),
                "n_over": sum(1 for v in r["gaps"].values() if v[0] > v[1]),
                "jit_refused": int(r["jit_refused"]),
            })
        out += _fmt(det, ["f0", "build_sr", "dp_err", "cycledshape", "tol",
                          "mpd_worst", "n_over", "jit_refused"],
                    [8, 10, 10, 13, 9, 11, 8, 13])
        out.append("")
    out += [
        "stride_error -- round(sr*T) against sr*T, in percent, for every MPD",
        "stride at both rates. Not swept: it is arithmetic, and it is the",
        "quantity the mpd*.rowcorr mechanism is read against.",
        "",
    ]
    out += _fmt([{"stride_ms": round(T * 1e3, 2),
                  "err44100_pct": round(stride_error_pct(SR_A, T), 4),
                  "err48000_pct": round(stride_error_pct(SR_B, T), 4)}
                 for T in D.MPD_PERIODS_S],
                ["stride_ms", "err44100_pct", "err48000_pct"], [11, 14, 14])
    out.append("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path, help="write the report here too")
    ap.add_argument("--taps", type=int, nargs="+", default=list(TAPS_GRID))
    ap.add_argument("--f0", type=float, nargs="+", default=list(F0_GRID))
    ap.add_argument("--detail", action="store_true", default=False)
    ap.add_argument("--no-detail", dest="detail", action="store_false")
    a = ap.parse_args(argv)
    rows = sweep(tuple(a.taps), tuple(a.f0))
    text = report(rows, detail=a.detail or a.out is not None)
    sys.stdout.write(text)
    if a.out:
        a.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
