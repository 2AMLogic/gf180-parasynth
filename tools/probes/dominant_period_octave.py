#!/usr/bin/env python3
"""Why does `dominant_period` pick a different octave at the two rates? #774

THE QUESTION. `tools/probes/fold_rate_sweep.py` REFUSES a handful of its
(f0, direction) pairs because `dominant_period` returns one period on one side
of the rate pair and TWO on the other -- an octave error in the column
`jit.period_ms` reports and in the five more that are folded at it. #774 was
filed with a suspected mechanism: that the lag-T autocorrelation peak is
"split between two integer lags while the lag-2T peak is not". This instrument
measures that, and the measurement REFUTES it.

WHAT IT PRINTS, per (f0, build rate), for the clip and for its resampling:

    frac(T)        where the true period falls between two samples.
    dist(2T,int)   how far TWO periods fall from a whole number of samples.
    r[T], r[2T]    the normalised autocorrelation `dominant_period` itself
                   maximises, at the best integer lag within +-2 of one and
                   of two true periods. The +-2 window is the point: a peak
                   SPLIT between two integer lags cannot hide from it.
    r2T-rT         the deciding margin, and the quantity the fix's dial is
                   stated in.
    pick           which of the two `dominant_period` actually returned.

WHAT IT SHOWS (#774, 2026-11-13/14). `r[T]` is not split: the best integer lag
near a fractional T simply correlates LESS (0.9857 -> 0.9660), because the
waveform does not line up there. `2T` is fractional too, but when
frac(T) ~ 0.5 the lag 2T lands NEAR AN INTEGER, so `r[2T]` barely moves. The
error is fractional-lag attenuation of r[T] against a 2T that happens to be
aligned -- and the margin that decides it is only +0.003 to +0.005, against an
intrinsic preference for T of -0.014 on an aligned clip. That margin is the
measured basis for JIT_OCTAVE_MARGIN_PERMILLE; its sweep record is
docs/sensitivity/dominant-period-octave-margin.json.

APPARATUS PRECONDITIONS, ASSERTED HERE RATHER THAN ASSUMED:

  * The clip is built by the SWEEP'S OWN stimulus (`_rate_stimulus`, which is
    `_square` x an 80 ms exponential decay), not by `_square` alone. THE FIRST
    VERSION OF THIS PROBE USED `_square` and reported the 44.1 kHz-built side
    picking 2T at 850 Hz -- a clip the sweep does NOT refuse. The decay, not
    the square, is what makes those two disagree, so the first numbers were
    about a stimulus nobody measures. It was caught by printing each row
    against the sweep's own refusal statistic rather than by inspection, and
    `tools/probes/test_dominant_period_octave.py` keeps that refutation
    executable instead of leaving it as a sentence in a docstring.
  * `acf()` reproduces `dominant_period`'s own conditioning -- the non-zero
    span, the mean removal, the FFT length, the lag band and the
    JIT_MIN_CYCLES cap -- so the r printed is the r the shipped function
    argmaxes, which the same test file asserts by comparing its argmax
    against `dominant_period(x, sr, octave_margin=0)`.
  * `refused` is `fold_rate_sweep.octave_refused`, imported rather than
    restated, so a row this probe calls clean cannot be a row the sweep
    refuses.

    tools/probes/dominant_period_octave.py                 # the #774 panel
    tools/probes/dominant_period_octave.py --gaps          # the gap ladder
    tools/probes/dominant_period_octave.py --f0 865 --build-sr 48000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import fold_rate_sweep as FRS                           # noqa: E402
from model import discrimination_features as D          # noqa: E402

SR_A, SR_B = FRS.SR_A, FRS.SR_B

# The panel #774 was filed on and the passing neighbours that bound it. 865 Hz
# is the middle of the band; 845 and 885 pass in both directions; 440 Hz is far
# from the band and is the row that shows the intrinsic preference for T.
PANEL = ((850.0, SR_B), (865.0, SR_B), (880.0, SR_B),
         (850.0, SR_A), (865.0, SR_A), (880.0, SR_A),
         (845.0, SR_B), (885.0, SR_B), (440.0, SR_B))


def acf(x: np.ndarray, sr: int, band=D.JIT_BAND_HZ) -> tuple[np.ndarray, int, int]:
    """`dominant_period`'s own autocorrelation, normalised to r[0].

    Every step here is the shipped function's step, in its order: the non-zero
    span, the mean over that span, the power-of-two FFT length, the lag band
    and the JIT_MIN_CYCLES cap. A probe that conditioned the signal its own
    way would be measuring its own estimator."""
    x = np.asarray(x, float)
    nz = np.nonzero(x)[0]
    x = x[nz[0]:nz[-1] + 1]
    lo, hi = int(sr / band[1]), int(sr / band[0])
    hi = min(hi, len(x) // D.JIT_MIN_CYCLES)
    n = 1 << int(np.ceil(np.log2(2 * len(x))))
    X = np.fft.rfft(x - x.mean(), n)
    r = np.fft.irfft(X * np.conj(X), n)[:hi + 1]
    return r / r[0], lo, hi


def emitted_period(f0: float, build_sr: int, sr: int) -> float:
    """The period `_square` ACTUALLY emitted, in samples at `sr`.

    `_square` quantises its period to whole samples of the BUILD rate, so the
    true period of the clip is `round(build_sr/f0)` there and that same
    duration -- not `sr/f0` -- after resampling. Reading against the nominal
    f0 instead is what makes the sweep's `dp_err` understate the
    misalignment (fold-interpolator.json, limits 4)."""
    return round(build_sr / f0) * (sr / float(build_sr))


def peak_near(r: np.ndarray, lo: int, hi: int, lag: float,
              window: int = 2) -> tuple[float, int]:
    """The largest r within +-`window` integer lags of `lag`.

    The window is why this probe can refute a split peak: if the peak at T
    were split between lags 55 and 56, the max over 53..57 would still see
    its full height."""
    i = int(round(lag))
    a, b = max(lo, i - window), min(hi, i + window) + 1
    if a >= b:
        return float("nan"), -1
    w = r[a:b]
    return float(w.max()), int(a + int(np.argmax(w)))


def row(f0: float, build_sr: int, octave_margin=None) -> list[dict]:
    """One (f0, build rate) pair as two rows: the clip, and its resampling."""
    other = SR_B if build_sr == SR_A else SR_A
    a = FRS.stimulus(f0, build_sr)
    b = D._resample(a, build_sr, other)
    refused = FRS.octave_refused(a, build_sr, b, other, octave_margin=octave_margin)
    out = []
    for x, sr, tag in ((a, build_sr, 0), (b, other, 1)):
        r, lo, hi = acf(x, sr)
        p = D.dominant_period(x, sr, octave_margin=octave_margin)
        T = emitted_period(f0, build_sr, sr)
        rT, iT = peak_near(r, lo, hi, T)
        r2T, i2T = peak_near(r, lo, hi, 2 * T)
        out.append({
            "f0": round(f0, 1), "build_sr": build_sr, "resampled": tag,
            "sr": sr, "T": round(T, 3),
            "fracT": round(T - np.floor(T), 3),
            "dist2Tint": round(abs(2 * T - round(2 * T)), 3),
            "rT": round(rT, 4), "lagT": iT,
            "r2T": round(r2T, 4), "lag2T": i2T,
            "margin": round(r2T - rT, 4),
            "dp": p, "picked2T": int(abs(p - 2 * T) < abs(p - T)),
            "refused": int(refused),
        })
    return out


def gap_ladder(f0s, build_srs=(SR_A, SR_B), octave_margin=None) -> list[dict]:
    """`r[2T] - r[T]` for every clip on a ladder, with the sweep's refusal.

    This is the quantity the margin dial is compared against: a margin above
    every POSITIVE value here picks T on every clip measured, and the record
    in docs/sensitivity/ states its grid against this distribution."""
    rows = []
    for f0 in f0s:
        for build_sr in build_srs:
            rows.extend(r for r in row(f0, build_sr, octave_margin) if r["resampled"])
    return rows


# ---------------------------------------------------------------------------
# the report


COLS = ["f0", "build_sr", "resampled", "sr", "T", "fracT", "dist2Tint",
        "rT", "lagT", "r2T", "lag2T", "margin", "dp", "picked2T", "refused"]
WIDTHS = [8, 10, 10, 7, 9, 8, 10, 9, 6, 9, 7, 9, 6, 9, 8]


def report(rows: list[dict], title: str, prose: list[str]) -> str:
    out = [f"{title} -- {prose[0]}"] + prose[1:] + [""]
    out.append("".join(f"{c:>{w}}" for c, w in zip(COLS, WIDTHS)))
    for r in rows:
        out.append("".join(
            f"{r[c]:>{w}}" if isinstance(r[c], (int, np.integer))
            else f"{r[c]:>{w}.4f}" for c, w in zip(COLS, WIDTHS)))
    out.append("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--f0", type=float, nargs="+", default=None)
    ap.add_argument("--build-sr", type=int, nargs="+", default=None,
                    choices=(SR_A, SR_B))
    ap.add_argument("--gaps", action="store_true",
                    help="the r[2T]-r[T] ladder over 840..890 Hz at 1 Hz")
    ap.add_argument("--margin", type=float, default=None,
                    help="override JIT_OCTAVE_MARGIN_PERMILLE (per mille of r)")
    ap.add_argument("-o", "--out", type=Path)
    a = ap.parse_args(argv)

    print(f"band={D.JIT_BAND_HZ} JIT_MIN_CYCLES={D.JIT_MIN_CYCLES} "
          f"JIT_OCTAVE_MARGIN_PERMILLE={D.JIT_OCTAVE_MARGIN_PERMILLE} "
          f"margin_override={a.margin} numpy={np.__version__}")
    if a.gaps:
        f0s = a.f0 if a.f0 else [float(f) for f in range(840, 891)]
        srs = tuple(a.build_sr) if a.build_sr else (SR_A, SR_B)
        rows = gap_ladder(f0s, srs, a.margin)
        text = report(rows, "octave_gaps", [
            "r[2T]-r[T] on the RESAMPLED side of every pair on the ladder,",
            "which is the side the octave error lands on. A positive margin is",
            "a clip where two periods correlate better than one; refused is",
            "fold_rate_sweep's own octave-mismatch statistic on the pair.",
        ])
    else:
        rows = []
        panel = (tuple((f, s) for f in (a.f0 or []) for s in (a.build_sr or (SR_B,)))
                 if a.f0 else PANEL)
        for f0, build_sr in panel:
            rows.extend(row(f0, build_sr, a.margin))
        text = report(rows, "octave_mechanism", [
            "the autocorrelation dominant_period maximises, at one and at two",
            "true periods, for the clips fold_rate_sweep REFUSES and for",
            "passing neighbours. resampled=0 is the built clip, 1 is its",
            "resampling to the other rate. r is normalised to r[0] and each",
            "peak is the max over +-2 lags, so a split peak cannot hide.",
        ])
    sys.stdout.write(text)
    if a.out:
        a.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
