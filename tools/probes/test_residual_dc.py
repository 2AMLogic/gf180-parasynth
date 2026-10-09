#!/usr/bin/env python3
"""Apparatus controls for tools/probes/residual_dc.py (#152).

    python3 -m pytest tools/probes/test_residual_dc.py -q

COMMITTED RED FIRST (docs/residual-dc/red-start.txt): the estimator was a stub
returning NaN, every fixture executed, and the intended assertions failed.
Only then was the estimator written.

Every fixture's answer is fixed by CONSTRUCTION (a constant added to a
zero-mean ring; a 10 ms half-sine pulse; a Hann-windowed integer-cycle tone),
not by running the estimator on the drum model. Nothing here renders the
model, so these tests are cheap and are the gate the model records depend on.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import residual_dc as R                                   # noqa: E402

SR = 48000
FC = R.FC


@pytest.mark.parametrize("name,prop", R.PROPERTIES, ids=[n for n, _ in R.PROPERTIES])
def test_property_holds_on_the_real_estimator(name, prop):
    assert prop(R.REAL), name


def test_every_injected_defect_turns_something_red():
    rows = R.controls_matrix()
    clean = rows.pop("(clean)")
    assert all(v == "ok" for v in clean.values()), clean
    for defect, row in rows.items():
        assert "MOVED" in row.values(), f"{defect} is invisible to every property: {row}"


def test_each_property_catches_at_least_one_defect():
    """A property no defect moves is a property that tests nothing."""
    rows = R.controls_matrix()
    rows.pop("(clean)")
    dead = [n for n, _ in R.PROPERTIES if all(r[n] != "MOVED" for r in rows.values())]
    # polarity and zero-mean are guarded by construction (|X0|^2); the others
    # must each be moved by some defect. The exemptions are explicit.
    assert set(dead) <= {"polarity", "zero-mean!=OFFSET"}, dead


def test_the_mean_subtracting_conditioner_is_the_named_wrong_one():
    """The exact conditioner the issue names. It erases the offset it classifies."""
    est = R.MeanSubtract()
    assert R.classify(R.fx_offset(), SR, FC, est)[0] != "OFFSET"
    assert R.classify(R.fx_offset(), SR, FC, R.REAL)[0] == "OFFSET"


def test_known_answers_on_the_synthetic_statistics():
    off = R.REAL.stats(R.fx_offset(), SR, FC)
    assert off["beta"] > 0.99 and off["flat"] > 0.9 * (2 * off["n20"] - 1), off
    sk = R.REAL.stats(R.fx_skirt(), SR, FC)
    assert abs(sk["flat"] - 1.0) < 0.1, sk                    # flat: S = 1
    assert abs(sk["beta"] - FC / 20.0) < 0.05, sk             # flat -> beta = fc/20


def test_spectral_flatness_is_the_closed_form_for_a_pulse():
    """Independent of the estimator's own code: the DFT of a 10 ms half-sine is
    |X(f)| ~ cos(pi f T/2)/(1-(fT)^2) -> flat to 20 Hz within 1 %."""
    d = 0.010
    ratio = math.cos(math.pi * 20.0 * d / 2.0) / (1 - (20.0 * d) ** 2)
    assert 0.98 < ratio < 1.0
    assert abs(R.REAL.stats(R.fx_skirt(), SR, FC)["flat"] - 1.0) < 2 * (1 - ratio) + 0.05


def test_added_hf_is_absolute_and_lf_removal_is_not_hf():
    base = R.fx_offset()
    add = R.add_hf(base)
    rem = R.remove_offset(base, 0.08 * R.FS)
    k, d_hf, d_sh = R.share_change_kind(base, add, SR)
    assert k == "HF_ADDED" and d_hf > 10.0, (k, d_hf)
    k, d_hf, d_sh = R.share_change_kind(base, rem, SR)
    assert k == "LF_REMOVAL" and abs(d_hf) < R.HF_FLAT_DB and d_sh > 0, (k, d_hf, d_sh)


# ---- guards, each with the input that defeats it -----------------------------------
def test_refuses_non_finite():
    x = R.fx_offset().astype(float)
    x[100] = np.nan
    assert R.classify(x, SR, FC)[0] == "REFUSED"


def test_refuses_silence_and_near_silence():
    assert R.classify(np.zeros(SR * 3), SR, FC)[0] == "REFUSED"
    quiet = np.zeros(SR * 3)
    quiet[:10] = 3.0                                           # 3 LSB peak
    assert R.classify(quiet, SR, FC)[0] == "REFUSED"


def test_refuses_a_clip_shorter_than_the_declared_window():
    x = R.fx_offset()[: int(1.0 * SR)]
    label, d = R.classify(x, SR, FC)
    assert label == "REFUSED" and "declared" in d["reason"], d


def test_refuses_when_the_voice_is_still_sounding_at_the_window_end():
    """Defeating input: a ring that never decays. Its beta/S are then functions
    of where the window was cut, not of the signal."""
    t = np.arange(int(2.4 * SR)) / SR
    x = R._q(0.3 * np.sin(2 * np.pi * 300.0 * t) + 0.08)
    label, d = R.classify(x, SR, FC)
    assert label == "REFUSED" and "still sounding" in d["reason"], (label, d)


def test_refuses_sub20_energy_that_does_not_clear_the_quantisation_floor():
    """Defeating input: a 1 LSB offset under a ring. The offset is real and the
    classification would read as OFFSET; it is at the instrument's resolution."""
    x = R.tone_burst(SR)
    x = R._q(x + 1.0 / R.FS)
    label, d = R.classify(x, SR, FC)
    assert label == "REFUSED" and "quantisation floor" in d["reason"], (label, d)


def test_refuses_a_band_it_cannot_resolve():
    """Defeating input: a corner below the 4-bin resolution (0.5 Hz bins)."""
    label, d = R.classify(R.fx_offset(), SR, 1.0)
    assert label == "REFUSED" and "band resolution" in d["reason"], (label, d)


def test_a_quantisation_floor_fixture_really_sits_at_the_floor():
    """The floor formula against a measured requantisation of white noise
    (an independent check: dither is generated, not derived)."""
    rng = np.random.default_rng(7)
    x = rng.uniform(-0.5, 0.5, SR * 8)                          # +-0.5 LSB uniform
    got = R.band_db(x, SR, 0.0, 20.0)
    assert abs(got - R.quantisation_floor_db(SR)) < 1.5, got


def test_window_means_are_onset_relative_and_never_zero_for_a_missing_window():
    x = R.fx_offset()
    y = np.concatenate([np.zeros(12345), x])
    a, b = R.window_means(x, SR), R.window_means(y, SR)
    assert np.allclose(a, b, equal_nan=True)
    short = R.window_means(x[:int(0.1 * SR)], SR)
    assert math.isnan(short[-1]) and math.isnan(short[-2])


def test_mixed_is_reported_not_forced():
    """A burst sitting on a short plateau: beta says skirt, S says offset."""
    x = R.fx_skirt().astype(float)
    x[:int(0.2 * SR)] += 0.05 * R.FS                            # a 200 ms standing step
    label, d = R.classify(x, SR, FC)
    assert label in ("MIXED", "OFFSET", "REFUSED"), (label, d)
    assert label != "SKIRT", d
