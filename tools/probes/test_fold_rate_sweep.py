#!/usr/bin/env python3
"""Validation cases for tools/probes/fold_rate_sweep.py -- #767.

AN INSTRUMENT CALIBRATED ON THE THING IT MEASURES IS NOT VALIDATED
(docs/failure-modes.md). The sweep's job is to say how much of a fold
statistic is the oscillator and how much is the sample grid, so a test that
only checked it against a saved run of itself would establish repeatability
and nothing else. Three of the five cases below have answers that come from
somewhere other than this repository's code:

  * 300 Hz divides both 44100 and 48000, so its fold is exactly aligned at
    both rates and the excess MUST be zero. That is arithmetic.
  * `round(sr*T)` against `sr*T` is arithmetic too, and is checked against
    hand-written numbers rather than against the function.
  * the three rows #767 was filed with are a prior measurement, made on a
    different numpy and before any of this code existed, and the instrument
    has to reproduce them on the fold that shipped then.

The remaining two are about the instrument's own honesty: that it reads the
functions that SHIP rather than a copy of them, and that it REFUSES rather
than reports when its precondition fails.

    python3 -m pytest tools/probes/test_fold_rate_sweep.py -q
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
from model import discrimination_features as D  # noqa: E402


def test_a_period_whole_at_both_rates_shows_no_fold_excess():
    """THE VALIDATION CASE WITH AN EXTERNALLY KNOWN ANSWER.

    44100/300 = 147 and 48000/300 = 160 are both whole numbers, so at 300 Hz
    there is no fractional part for a fold to misalign on at either rate.
    Whatever the instrument reports there is its own noise floor, and it must
    be far below the 0.02 the module calls a difference -- on the REPAIRED
    fold and on the integer one that shipped before it, because the mechanism
    is absent from this stimulus either way.

    The answer is division, not a reference run, which is the whole point.
    If this ever goes red the instrument is manufacturing an excess and every
    other number it prints is partly that."""
    assert 44100 % 300 == 0 and 48000 % 300 == 0
    for taps in (-1, D.FOLD_SINC_TAPS):
        for build_sr in (44100, 48000):
            r = S.pair(300.0, build_sr, taps)
            assert not r["jit_refused"], r["period_ratio"]
            assert abs(r["dp_err_other"]) < 1e-9, r["dp_err_other"]
            gap, tol, a, b = r["gaps"][S.OBJECTIVE_COL]
            assert gap < 0.1 * tol, (taps, build_sr, gap, tol, a, b)


def test_the_stride_error_is_the_arithmetic_it_claims():
    """5.0 ms is 220.5 samples at 44.1 kHz and exactly 240 at 48 kHz: the
    asymmetry the `mpd*.rowcorr` mechanism is read against. Checked against
    numbers written out by hand, not against the module."""
    assert S.stride_error_pct(48000, 5.0e-3) == 0.0
    assert S.stride_error_pct(44100, 5.0e-3) == pytest.approx(
        100.0 * (220 - 220.5) / 220.5, abs=1e-12)
    assert S.stride_error_pct(44100, 10.0e-3) == 0.0        # 441 samples exactly
    assert S.stride_error_pct(44100, 7.07e-3) == pytest.approx(
        100.0 * (312 - 44100 * 7.07e-3) / (44100 * 7.07e-3), abs=1e-12)


def test_the_instrument_reproduces_the_three_rows_the_issue_was_filed_with():
    """#767 was filed with three measured gaps, on a numpy this host does not
    have and against code that no longer exists in this file. The instrument
    has to find them again on the fold that shipped then (`taps=-1`), to the
    four decimals they were quoted at -- otherwise it is not measuring the
    defect that was reported."""
    filed = {
        (440.0, "jit.cycledshape.seg0"): (0.0281, 0.0536),
        (660.0, "mpd5.00ms.rowcorr.seg0"): (-0.1339, -0.1613),
        (660.0, "mpd5.00ms.rowcorr.seg1"): (-0.1343, -0.1617),
    }
    for (f0, nm), (want_a, want_b) in filed.items():
        r = S.pair(f0, 44100, -1)
        gap, tol, got_a, got_b = r["gaps"][nm]
        assert round(got_a, 4) == want_a, (f0, nm, got_a, want_a)
        assert round(got_b, 4) == want_b, (f0, nm, got_b, want_b)
        assert gap > tol, (f0, nm, gap, tol)


def test_the_instrument_reads_the_functions_that_ship():
    """A probe that reimplements the thing it measures measures the
    reimplementation. `read` must return exactly what `extra_features` puts
    in the vector, and its `taps=None` default must follow the shipped
    constant -- so moving FOLD_SINC_TAPS moves the probe with it."""
    x = D._rate_stimulus(523.0, 0.0, 0)
    shipped = dict(zip(*reversed(D.extra_features(x, 44100))))
    got = S.read(x, 44100, None)
    for nm, v in got.items():
        assert v == shipped[nm], nm
    # and it is the constant, not a literal 16 compiled into the probe
    assert got["jit.cycledshape.seg0"] == S.read(x, 44100, D.FOLD_SINC_TAPS)[
        "jit.cycledshape.seg0"]
    assert got["jit.cycledshape.seg0"] != S.read(x, 44100, -1)["jit.cycledshape.seg0"]


def test_the_instrument_refuses_the_octave_mismatch_rather_than_reporting():
    """REFUSED IS A FIRST-CLASS OUTCOME. 880 Hz built at 48 kHz and resampled
    down makes `dominant_period` return two periods at 44.1 kHz and one at
    48 kHz, so the two sides of that pair are not measuring the same partial
    and a fold-geometry gap read off them is not about fold geometry. The
    instrument must mark that row refused and keep it out of the objective
    -- a tool that answers when it cannot is worse than one that is absent,
    because its output looks exactly like data.

    The forward direction of the same f0 is the control: it is NOT refused,
    so `jit_refused` is not just on for everything at 880 Hz."""
    bad = S.pair(880.0, 48000, D.FOLD_SINC_TAPS)
    assert bad["jit_refused"]
    assert abs(bad["period_ratio"] - 2.0) < 0.05, bad["period_ratio"]
    good = S.pair(880.0, 44100, D.FOLD_SINC_TAPS)
    assert not good["jit_refused"], good["period_ratio"]

    rows = [bad, good]
    summary = S.summarise(rows)
    assert len(summary) == 1
    assert summary[0]["refused"] == 1
    # the objective is the unrefused row's gap, not the refused row's 0.66
    assert summary[0]["rate_gap"] == round(good["gaps"][S.OBJECTIVE_COL][0], 4)


def test_the_two_controls_bound_the_dial_from_the_other_side():
    """The objective alone is not a criterion: a fold that aligned every row
    perfectly would drive the rate gap to zero by destroying what the columns
    exist to see. Both of these are properties an existing self-test in
    `discrimination_features.py` asserts, read here as numbers so the sweep
    can show WHERE on the dial they break rather than only that they hold at
    the shipped value."""
    assert S.walks_at_strides(D.FOLD_SINC_TAPS) == len(D.MPD_PERIODS_S)
    assert S.walks_at_strides(-1) == len(D.MPD_PERIODS_S)
    assert S.jitter_margin(D.FOLD_SINC_TAPS) > 0.0
    assert S.jitter_margin(-1) > 0.0


def test_the_report_parses_as_the_sensitivity_gate_reads_it():
    """The sweep's artefact is the evidence behind
    docs/sensitivity/fold-interpolator.json, and `tools/sensitivity.py`
    re-extracts every recorded point from it. A report the gate cannot parse
    is a record with no evidence under it, which that gate REFUSES -- so the
    shape is asserted here rather than discovered there."""
    sys.path.insert(0, str(ROOT / "tools"))
    import sensitivity

    rows = S.sweep(taps_grid=(-1, D.FOLD_SINC_TAPS), f0_grid=(300.0, 440.0))
    tables = sensitivity.parse_fixed_width_tables(S.report(rows, detail=True))
    assert "fold_interpolator" in tables
    got = {r["taps"]: r for r in tables["fold_interpolator"]}
    assert set(got) == {-1.0, float(D.FOLD_SINC_TAPS)}
    for r in got.values():
        assert "rate_gap" in r and "sees_jit" in r and "walks" in r
    assert "stride_error" in tables
    assert len(tables["stride_error"]) == len(D.MPD_PERIODS_S)
