#!/usr/bin/env python3
"""Validation cases for tools/probes/octave_margin_sweep.py -- #774.

AN INSTRUMENT CALIBRATED ON THE THING IT MEASURES IS NOT VALIDATED
(docs/failure-modes.md). This sweep's job is to price a dial, so a test that
only checked it against a saved run of itself would establish repeatability
and nothing else. Four of the six cases below have answers from somewhere
other than this probe:

  * the period-doubled constraint panel really is period-doubled -- asserted
    from the samples (`x[n + 2P] == x[n]` and `x[n + P] != x[n]`), not from
    what `dominant_period` says about it.
  * the `exact` panel's answers are arithmetic: 44100 and 48000 are divisible
    by 300, 48000 by 240 and 44100 by 441, so the lag is sr/f0 and is checked
    against hand-written numbers.
  * the objective and the count are the SAME predicate the sweep instrument
    refuses its own pairs on, imported from `fold_rate_sweep` rather than
    restated: `ratio_err < 0.05` must hold exactly when nothing is refused.
  * the count at the bare argmax reproduces a PRIOR measurement -- 10 of the
    322 pairs of a 5 Hz ladder over 200..1000 Hz, in both directions, measured
    on #774 before any of this code existed and on a different numpy.

The other two are about the probe's honesty: that it REFUSES rather than
scores a constraint entry the bare argmax already gets wrong, and that the
dial it sweeps is the constant the shipped function reads.

    python3 -m pytest tools/probes/test_octave_margin_sweep.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import fold_rate_sweep as S  # noqa: E402
import octave_margin_sweep as M  # noqa: E402
from model import discrimination_features as D  # noqa: E402


def test_the_doubled_panel_really_is_period_doubled():
    """FROM THE SAMPLES, NOT FROM THE ESTIMATOR. The constraint column is only
    a constraint if its clips genuinely have the longer period: a clip that
    merely LOOKS doubled to an autocorrelation would make `keeps_2t` a
    statement about the autocorrelation."""
    for f0, sr, depth in M.DOUBLING_PANEL:
        x = M.alternating(f0, sr, depth)
        period = round(sr / f0)
        n = 8 * period
        assert np.allclose(x[:n], x[2 * period:2 * period + n]), (f0, sr, depth)
        assert not np.allclose(x[:n], x[period:period + n]), (f0, sr, depth)
        # and the alternation is the stated depth, read off the samples
        lo = np.abs(x[period:2 * period]).max()
        hi = np.abs(x[:period]).max()
        assert abs(lo / hi - (1.0 - depth)) < 1e-9, (f0, sr, depth)


def test_the_exact_panel_answers_are_arithmetic():
    """Hand-written lags, and the divisibility that makes them exact. If an
    entry's rate stopped being divisible by its f0 the `exact` column would
    silently become a statement about rounding."""
    for f0, sr, lag in M.EXACT_PANEL:
        assert sr % f0 == 0, (f0, sr)
        assert lag == sr / f0, (f0, sr, lag)
    assert M.EXACT_PANEL == ((300.0, 44100, 147), (300.0, 48000, 160),
                             (240.0, 48000, 200), (441.0, 44100, 100))


def test_the_objective_and_the_count_are_the_same_predicate():
    """`ratio_err` is the continuous form of the count, so the two cannot
    disagree about a pair: the worst ratio error must be under the 5 % the
    sweep instrument refuses at exactly when the count is zero. A probe whose
    objective and whose criterion could disagree would let a margin look
    repaired while the instrument still refused its rows."""
    pairs = M.clip_pairs(tuple(float(f) for f in (440.0, 850.0, 865.0, 880.0)))
    for margin in (0, 10, 20):
        row = M.ladder_readings(pairs, margin)
        assert (row["ratio_err"] < S.OCTAVE_TOL_REL) == (row["n_mismatch"] == 0), (
            margin, row)
        assert row["n_mismatch"] == row["mm_44k"] + row["mm_48k"]


def test_the_bare_argmax_reproduces_the_prior_count_on_the_filed_ladder():
    """THE PRIOR MEASUREMENT, which is the one answer here this repository's
    current code did not produce. #774's 5 Hz ladder over 200..1000 Hz, both
    directions, 322 pairs, found 10 octave mismatches at the bare argmax --
    measured before this probe existed and on numpy 2.5.2. The instrument has
    to reproduce it, and has to reproduce WHICH pairs, because 10 of the right
    rows and 10 of the wrong ones are the same number."""
    ladder = tuple(float(f) for f in range(200, 1001, 5))
    pairs = M.clip_pairs(ladder)
    assert len(pairs) == 322
    row = M.ladder_readings(pairs, 0)
    assert row["n_mismatch"] == 10, row
    assert (row["mm_48k"], row["mm_44k"]) == (7, 3), row
    bad = sorted((f0, build_sr) for f0, build_sr, a, other, b in pairs
                 if S.octave_refused(a, build_sr, b, other, octave_margin=0))
    assert bad == [(850.0, 48000), (855.0, 48000), (860.0, 44100), (860.0, 48000),
                   (865.0, 44100), (865.0, 48000), (870.0, 44100), (870.0, 48000),
                   (875.0, 48000), (880.0, 48000)], bad
    # and the repair clears every one of them
    assert M.ladder_readings(pairs, D.JIT_OCTAVE_MARGIN_PERMILLE)["n_mismatch"] == 0


def test_the_constraint_panel_refuses_an_entry_the_argmax_already_gets_wrong():
    """REFUSED IS A FIRST-CLASS OUTCOME. The margin is only consulted after
    the argmax, so an entry the bare argmax already reports an octave high
    says nothing about this dial -- and would make `keeps_2t` look worse at
    every margin for a reason that is not the margin. A shallow alternation is
    exactly such an entry."""
    with pytest.raises(M.Refused):
        M.doubling_panel(0, panel=((400.0, 48000, 0.05),))
    # and the shipped panel is not one of those: every entry passes at margin 0
    assert M.doubling_panel(0) == len(M.DOUBLING_PANEL)


def test_the_dial_it_sweeps_is_the_constant_the_shipped_function_reads():
    """The sweep must price the code that ships. `octave_margin` is an
    override on `dominant_period` itself, so passing the shipped value must be
    indistinguishable from passing nothing -- if it were a separate code path
    the whole record would be about a copy."""
    x = M.FRS.stimulus(865.0, 48000)
    y = D._resample(x, 48000, 44100)
    for sr, clip in ((48000, x), (44100, y)):
        assert (D.dominant_period(clip, sr) ==
                D.dominant_period(clip, sr,
                                  octave_margin=D.JIT_OCTAVE_MARGIN_PERMILLE))
    assert D.JIT_OCTAVE_MARGIN_PERMILLE in M.MARGIN_GRID
