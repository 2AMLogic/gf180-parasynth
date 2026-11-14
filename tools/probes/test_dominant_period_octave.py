#!/usr/bin/env python3
"""Validation cases for tools/probes/dominant_period_octave.py -- #774.

AN INSTRUMENT CALIBRATED ON THE THING IT MEASURES IS NOT VALIDATED
(docs/failure-modes.md). This probe's job is to say WHICH autocorrelation
`dominant_period` maximises and by how much, so a test that only compared it
with a saved run of itself would establish repeatability and nothing else.
Four of the five cases below have answers that come from somewhere other than
this probe:

  * the r it prints is the r the SHIPPED function argmaxes -- asserted by
    taking the probe's own argmax over the band and comparing it with
    `dominant_period(x, sr, octave_margin=0)`, which is the function.
  * `emitted_period` is arithmetic: `_square` emits round(48000/880) = 55
    samples per period, which is 50.531 samples at 44.1 kHz, and that is
    checked against hand-written numbers.
  * a peak deliberately SPLIT between two integer lags is found at its full
    height by `peak_near`, which is the property that lets this probe refute
    the split-peak mechanism rather than merely fail to find it.
  * THE WRONG-THEN-RIGHT RECORD, kept executable rather than as a sentence in
    a docstring: the first version of this probe built its clips with `_square`
    instead of the sweep's `_rate_stimulus`, and reported the 44.1 kHz-built
    side picking 2T at 850 Hz -- a pair the sweep does NOT refuse. The decay,
    not the square, is what makes those two disagree. If that ever stops being
    true, the docstring's account of how the error was caught is wrong and
    somebody should find out before quoting it.

The fifth is about the probe's honesty: that its `refused` column is the sweep
instrument's own predicate and not a restatement of it.

    python3 -m pytest tools/probes/test_dominant_period_octave.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import dominant_period_octave as M  # noqa: E402
import fold_rate_sweep as S  # noqa: E402
from model import discrimination_features as D  # noqa: E402


def test_the_r_it_prints_is_the_r_the_shipped_function_argmaxes():
    """The probe conditions the signal itself -- non-zero span, mean, FFT
    length, lag band, JIT_MIN_CYCLES cap -- so the one thing that must be
    checked is that it conditions it the SAME way. If it did not, every r in
    the mechanism table would be about a different estimator."""
    for f0, sr in ((865.0, 48000), (440.0, 44100), (205.0, 48000)):
        x = M.FRS.stimulus(f0, sr)
        r, lo, hi = M.acf(x, sr)
        assert lo + int(np.argmax(r[lo:hi + 1])) == D.dominant_period(
            x, sr, octave_margin=0), (f0, sr)
        # and normalised to r[0], so a margin read off it is in the units
        # JIT_OCTAVE_MARGIN_PERMILLE is stated in
        assert r[0] == 1.0


def test_the_emitted_period_is_arithmetic():
    """`_square` quantises its period to whole samples of the BUILD rate, so
    the clip's true period is that duration at either rate and NOT sr/f0.
    Hand-written numbers, because reading it against the nominal f0 is exactly
    the mistake fold-interpolator.json's fourth limit records."""
    assert M.emitted_period(880.0, 48000, 48000) == 55.0
    assert M.emitted_period(880.0, 48000, 44100) == 55.0 * 44100 / 48000
    assert abs(M.emitted_period(880.0, 48000, 44100) - 50.53125) < 1e-9
    # 300 Hz divides both rates, so there is nothing to quantise
    assert M.emitted_period(300.0, 44100, 44100) == 147.0
    assert abs(M.emitted_period(300.0, 44100, 48000) - 160.0) < 1e-9


def test_a_split_peak_is_found_at_its_full_height():
    """THE PROPERTY THAT MAKES THE REFUTATION POSSIBLE. #774 suspected the
    lag-T peak was split between two integer lags. A probe that read a single
    lag could not tell a split peak from a low one, so it could not refute
    anything; `peak_near` takes the max over +-2 lags. Built here out of an
    array with a known answer, not out of a signal."""
    r = np.zeros(200)
    r[0] = 1.0
    r[99] = r[100] = 0.9          # a peak split between two integer lags
    r[150] = 0.95                 # and a taller unsplit one elsewhere
    # the full height, from either side, whichever of the two lags the tie
    # falls to -- the height is the claim, the tie-break is not
    for lag in (99.5, 100.4, 98.0, 101.0):
        height, found = M.peak_near(r, 40, 190, lag)
        assert height == 0.9 and found in (99, 100), (lag, height, found)
    assert M.peak_near(r, 40, 190, 150.0) == (0.95, 150)
    # outside the band it answers nothing rather than answering wrongly
    val, lag = M.peak_near(r, 40, 190, 300.0)
    assert np.isnan(val) and lag == -1


def test_the_first_version_of_this_probe_measured_a_stimulus_nobody_measures():
    """THE WRONG-THEN-RIGHT RECORD, AS A TEST.

    850 Hz built at 44.1 kHz: with the sweep's own stimulus (`_square` x an
    80 ms decay) the pre-#774 argmax agrees at both rates and the sweep does
    not refuse the pair. With a bare `_square`, the same f0 and direction picks
    TWO periods at 48 kHz -- 113 samples against 52 -- and would have been
    reported as part of the class. The 80 ms decay is the whole difference.

    docs/failure-modes.md: a record of a result that looked good and was wrong
    is worth more than one that was right first time, and it is exactly the
    file that gets deleted. This is that record, and it fails if the account
    in the probe's docstring stops being true."""
    bare = D._square(850.0, 44100, dur=0.24)
    decayed = M.FRS.stimulus(850.0, 44100)
    b_other = D._resample(bare, 44100, 48000)
    d_other = D._resample(decayed, 44100, 48000)
    assert D.dominant_period(b_other, 48000, octave_margin=0) == 113
    assert D.dominant_period(d_other, 48000, octave_margin=0) == 57
    assert S.octave_refused(bare, 44100, b_other, 48000, octave_margin=0)
    assert not S.octave_refused(decayed, 44100, d_other, 48000, octave_margin=0)
    # and the stimulus the probe actually builds is the decayed one
    assert np.allclose(M.FRS.stimulus(850.0, 44100), decayed)


def test_the_refused_column_is_the_sweep_instruments_own_predicate():
    """The probe prints `refused` so a row it calls clean cannot be a row the
    sweep refuses. That is only true if it is the SAME predicate, so it is
    imported rather than restated -- and this asserts the import, by checking
    the probe's column against `fold_rate_sweep.pair`'s own field on the rows
    that disagreed before #774."""
    for f0, build_sr in ((865.0, 48000), (865.0, 44100), (440.0, 48000)):
        mine = M.row(f0, build_sr)
        theirs = S.pair(f0, build_sr, D.FOLD_SINC_TAPS)
        assert {r["refused"] for r in mine} == {int(theirs["jit_refused"])}, (
            f0, build_sr)
