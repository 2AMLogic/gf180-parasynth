#!/usr/bin/env python3
"""Sweep the octave margin `dominant_period` chooses between octaves with. #774

THE DIAL. `JIT_OCTAVE_MARGIN_PERMILLE`, in thousandths of a normalised
autocorrelation: among the integer SUBMULTIPLES of the winning lag, the
shortest one that is a local autocorrelation peak and is within this margin of
the winning peak is returned instead of the winner. 0 is not a margin -- it
selects the bare argmax that shipped before #774, and it is on the grid so the
record prices the repair and not only the dial inside it. The dial is swept
THROUGH THE SHIPPED FUNCTION (`dominant_period(..., octave_margin=)`), not
through a copy of it.

THE OBJECTIVE, and why it is not a count. `ratio_err` is the worst
|period(sr_b)/period(sr_a) - 1| over the whole f0 ladder in BOTH resampling
directions: the same clip read at the two rates should report the same period
in seconds, and an octave error puts that ratio near 2 or near 0.5. A count of
threshold crossings would be a step function of the dial, would be exactly 0
once the class is repaired -- which the sensitivity gate's relative decision
rule cannot divide by -- and would hide how far the surviving disagreement is
from the 5 % the sweep instrument refuses at. `n_mismatch` is carried beside
it as the count the acceptance criterion is written in.

THE CONSTRAINT, in the same artefact, because the objective alone picks the
largest margin on the grid. `keeps_2t` is how many of a PERIOD-DOUBLED panel
still report their true (longer) period: a square whose alternate cycles are
scaled by 1-depth really has period 2P, and a margin wide enough to swallow
the resulting autocorrelation gap reports it an octave HIGH. That panel is the
input that defeats this guard -- `docs/verification-rules.md` rule 8 -- and it
is a measured column rather than a reassurance. Its precondition is asserted:
every entry must be a clip the BARE ARGMAX already gets right, or the row is
not about the dial at all and the probe REFUSES.

`exact` is the third reading: clips whose period is a whole number of samples,
so the answer is arithmetic and comes from outside this repository. It must
not move at any margin.

    tools/probes/octave_margin_sweep.py -o docs/sensitivity/dominant-period-octave-margin-sweep.txt
    tools/probes/octave_margin_sweep.py --margins 0 20 --detail

Validation cases: tools/probes/test_octave_margin_sweep.py.
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

# THE GRID, STATED BEFORE THE SWEEP RAN (and committed before it, so the claim
# is checkable in `git log`). In thousandths of a normalised autocorrelation.
#   0    the bare argmax that shipped before #774.
#   5    DELIBERATELY BELOW the worst margin the class needs (+0.0053,
#        measured by tools/probes/dominant_period_octave.py), so the grid
#        holds a margin that is real and still insufficient.
#   10   just above it: 1.9x the worst gap.
#   20   RATE_TOL_ABS["ratio"], the resolution this module already states for
#        a correlation-unit column. 3.8x the worst gap.
#   30,50  above every gap measured on the f0 ladder, where the objective can
#        no longer improve and only the constraint can move.
#   100,200  far above it, on the grid so the upper bound is a measured row
#        rather than an assertion: this is where the period-doubled panel is
#        lost.
MARGIN_GRID = (0, 5, 10, 20, 30, 50, 100, 200)

# The f0 ladder. The 5 Hz ladder over the band `dominant_period` can make this
# error in at all (2T must fall inside the 120..1200 Hz lag band, so below
# ~240 Hz the octave error is impossible by construction), PLUS every whole Hz
# across 840..890 -- because the class turned out to be a bounded band inside
# the 25 Hz ladder #767's instrument shipped with, and a 25 Hz ladder has no
# value between 855 and 870. THE THREE VALUES #774 WAS FILED WITH ARE NOT THE
# CLASS and must not be the panel.
F0_LADDER = tuple(sorted(set(float(f) for f in range(200, 1001, 5))
                         | set(float(f) for f in range(840, 891))))

# The period-doubled panel: (f0, build rate, alternation depth). Every entry is
# a clip whose true period is 2 * round(sr/f0) and which the bare argmax
# already reports correctly -- asserted in `doubling_panel`, not assumed.
DOUBLING_PANEL = tuple((f0, sr, d)
                       for sr in (SR_A, SR_B)
                       for f0 in (400.0,)
                       for d in (0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50))

# Clips whose period is a whole number of samples at the rate they are read at,
# so `dominant_period`'s answer is arithmetic: (f0, sr, expected lag).
EXACT_PANEL = ((300.0, SR_A, 147), (300.0, SR_B, 160),
               (240.0, SR_B, 200), (441.0, SR_A, 100))


class Refused(Exception):
    """The probe could not answer. Never reported as a measurement."""


# ---------------------------------------------------------------------------
# the measurement


def clip_pairs(f0_ladder=F0_LADDER) -> list[tuple]:
    """(f0, build_sr, clip, other_sr, resampled clip) for the whole ladder.

    Built once and reused for every margin: the dial changes which lag is
    returned, never the stimulus, so rebuilding the clips per margin would
    only add the resampler's cost eight times over."""
    out = []
    for f0 in f0_ladder:
        for build_sr in (SR_A, SR_B):
            other = SR_B if build_sr == SR_A else SR_A
            a = FRS.stimulus(f0, build_sr)
            out.append((f0, build_sr, a, other, D._resample(a, build_sr, other)))
    return out


def ladder_readings(pairs: list[tuple], margin: int) -> dict:
    """The objective and the counts, at one margin."""
    worst, n_mm, per_dir = 0.0, 0, {SR_A: 0, SR_B: 0}
    for f0, build_sr, a, other, b in pairs:
        ratio = FRS.period_ratio(a, build_sr, b, other, octave_margin=margin)
        if not np.isfinite(ratio):
            raise Refused(f"margin {margin}: {f0} Hz built at {build_sr} returned "
                          "no period at one of the two rates")
        worst = max(worst, abs(ratio - 1.0))
        if FRS.octave_refused(a, build_sr, b, other, octave_margin=margin):
            n_mm += 1
            per_dir[build_sr] += 1
    return {"ratio_err": round(worst, 4), "n_mismatch": n_mm,
            "mm_44k": per_dir[SR_A], "mm_48k": per_dir[SR_B]}


def alternating(f0: float, sr: int, depth: float, dur: float = 0.24) -> np.ndarray:
    """A square at `f0` whose alternate periods are scaled by `1 - depth`.

    Its true period is TWO of `_square`'s periods. The scaling is applied on
    `_square`'s own period grid (`round(sr/f0)` samples), so the doubling is
    exact at the sample level and is not an artefact of where the envelope
    happens to fall."""
    x = D._square(f0, sr, dur=dur)
    period = round(sr / f0)
    return x * np.where((np.arange(len(x)) // period) % 2 == 1, 1.0 - depth, 1.0)


def doubling_panel(margin: int, panel=DOUBLING_PANEL) -> int:
    """How many period-doubled clips still report their TRUE (longer) period.

    THE PRECONDITION IS ASSERTED, NOT ASSUMED: an entry the bare argmax
    already gets wrong says nothing about this dial, because the margin is
    only consulted after the argmax. Such an entry would silently make the
    column look better than the dial deserves, so it REFUSES instead."""
    kept = 0
    for f0, sr, depth in panel:
        x = alternating(f0, sr, depth)
        true_lag = 2 * round(sr / f0)
        bare = D.dominant_period(x, sr, octave_margin=0)
        if bare != true_lag:
            raise Refused(
                f"doubling panel ({f0} Hz, {sr}, depth {depth}): the bare argmax "
                f"returns {bare}, not the true {true_lag} -- this entry cannot "
                "measure what the margin costs")
        kept += D.dominant_period(x, sr, octave_margin=margin) == true_lag
    return kept


def exact_panel(margin: int, panel=EXACT_PANEL) -> int:
    """How many whole-sample-period clips still return the arithmetic answer."""
    return sum(D.dominant_period(FRS.stimulus(f0, sr), sr, octave_margin=margin) == lag
               for f0, sr, lag in panel)


def sweep(margins=MARGIN_GRID, f0_ladder=F0_LADDER) -> list[dict]:
    pairs = clip_pairs(f0_ladder)
    rows = []
    for margin in margins:
        row = {"margin": int(margin)}
        row.update(ladder_readings(pairs, margin))
        row["keeps_2t"] = doubling_panel(margin)
        row["exact"] = exact_panel(margin)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# the report


COLS = ["margin", "ratio_err", "n_mismatch", "mm_44k", "mm_48k", "keeps_2t", "exact"]
WIDTHS = [8, 11, 12, 8, 8, 10, 7]


def report(rows: list[dict], pairs: int, detail: bool = False) -> str:
    out = [
        "dominant_period_octave_margin -- #774. JIT_OCTAVE_MARGIN_PERMILLE is the",
        "margin, in thousandths of a normalised autocorrelation, within which",
        "`dominant_period` prefers a SHORTER octave to the bare argmax; 0 is the",
        "bare argmax that shipped before the repair.",
        "",
        f"ratio_err is the objective: the worst |period(b)/period(a) - 1| over",
        f"{pairs} (f0, direction) pairs, where the same clip read at 44.1 kHz and",
        "at 48 kHz should report the same period in seconds. n_mismatch counts the",
        "pairs over the 5 % `fold_rate_sweep` REFUSES at, split by which rate the",
        "clip was built at; mm_44k is the half of the class the margin was NOT",
        "selected against. keeps_2t is the constraint from the other side: of 16",
        "period-doubled clips the bare argmax gets right, how many a margin this",
        "wide still gets right. exact is 4 clips whose period is a whole number of",
        "samples, where the answer is arithmetic rather than this module's.",
        "",
    ]
    out.append("".join(f"{c:>{w}}" for c, w in zip(COLS, WIDTHS)))
    for r in rows:
        out.append("".join(
            f"{r[c]:>{w}}" if isinstance(r[c], (int, np.integer))
            else f"{r[c]:>{w}.4f}" for c, w in zip(COLS, WIDTHS)))
    out.append("")
    if detail:
        out += [
            "octave_margin_shipped -- the shipped constant, so the artefact says",
            "which row is the one in force.",
            "",
            "   shipped",
            f"{D.JIT_OCTAVE_MARGIN_PERMILLE:>10}",
            "",
        ]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--margins", type=int, nargs="+", default=list(MARGIN_GRID))
    ap.add_argument("--f0", type=float, nargs="+", default=list(F0_LADDER))
    ap.add_argument("--detail", action="store_true", default=False)
    a = ap.parse_args(argv)
    try:
        rows = sweep(tuple(a.margins), tuple(a.f0))
    except Refused as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 2
    text = report(rows, len(a.f0) * 2, detail=a.detail)
    sys.stdout.write(text)
    if a.out:
        a.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
