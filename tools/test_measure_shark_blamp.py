#!/usr/bin/env python3
"""Ground truth for `tools/measure_shark_blamp.py`.

A measurement tool's only failure mode that matters is a false green, and for
this one that means printing a number where the estimator could not answer. So
the refusal paths are tested first and the answer second.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import measure_shark_blamp as msb                                   # noqa: E402
import voice_fx as vf                                               # noqa: E402


def test_it_refuses_a_span_shorter_than_the_two_octaves_it_claims():
    """#48's acceptance criterion is "across at least two octaves". A tool that
    answers a one-octave request has silently changed the claim."""
    with pytest.raises(msb.Refused) as e:
        msb.measure([69, 75, 81])
    assert "two octaves" in str(e.value)


def test_it_refuses_fewer_than_three_notes():
    with pytest.raises(msb.Refused):
        msb.measure([57, 105])


def test_it_refuses_when_both_arms_would_be_the_same_signal(monkeypatch):
    """`BLAMP_THIRD = 0` is how the before arm is produced. If it is ALSO the
    live value there is no experiment, and a delta of 0.00 dB would look like a
    measurement saying BLAMP does nothing."""
    monkeypatch.setattr(vf, "BLAMP_THIRD", 0)
    with pytest.raises(msb.Refused) as e:
        msb.measure([57, 81, 105])
    assert "BLAMP_THIRD" in str(e.value)


def test_a_floor_limited_row_is_refused_and_not_reported():
    """The #61 trap: an estimator reading its own leakage floor looks exactly
    like a signal. An unreachable headroom demand must produce REFUSED, not a
    plausible number."""
    with pytest.raises(msb.Refused):
        msb.measure([57, 81, 105], min_headroom=200.0)
    # and the per-row form: one row's worth of the same check, rendered
    rep = msb.measure([57, 81, 105], min_headroom=10.0)
    assert all(r["inharmonic_headroom_before_db"] > 10.0 for r in rep["rows"])
    assert "REFUSED" in msb._cell(None)


def test_the_before_arm_is_the_pre_issue_48_expression():
    """`render(note, 0)` must reproduce the BLEP-only shark-tooth exactly --
    the mix of an already-corrected saw with a PLAIN triangle -- or the whole
    comparison is against something that never shipped."""
    import numpy as np
    import dsp
    note = 96
    inc = dsp.phase_inc(dsp.note_hz(note))
    got, f0 = msb.render(note, 0, seconds=0.05)
    n = len(got)
    ph = (inc * np.arange(n, dtype=np.int64)) & vf.PHASE_MASK
    e, r = vf.recip_of(inc)
    c = vf.blep_fx(ph, inc, e, r)
    want = vf.sat16((vf.SHARK_W_SAW * vf.sat16(vf._saw_fx(ph) - c)
                     + vf.SHARK_W_TRI * vf._tri_fx(ph)) >> 15)
    assert np.array_equal(np.round(got * 32768.0).astype(np.int64), want)
    assert abs(f0 - dsp.note_hz(note)) < 0.5


def test_the_answered_rows_show_the_direction_and_the_pitch_dependence():
    """The claim the tool exists to support: BLAMP never makes it worse, and it
    helps more at higher pitch. Both estimators, where both answer."""
    rep = msb.measure([57, 69, 81, 93, 105], seconds=0.25)
    assert rep["trend"]["rows_answered"] >= 4
    assert rep["trend"]["octaves"] >= 2.0
    assert rep["trend"]["worst_regression_db"] > -0.5, rep["trend"]
    assert rep["trend"]["best_gain_db"] > 3.0, rep["trend"]
    assert rep["trend"]["monotone_in_pitch"], [r["inharmonic_gain_db"] for r in rep["rows"]]
    for row in rep["rows"]:
        if row.get("foldback_gain_db") is not None:
            assert abs(row["foldback_gain_db"] - row["inharmonic_gain_db"]) < 1.0, row
    assert msb.render_table(rep).count("\n") > 5


def test_the_reference_rig_arm_refuses_rather_than_substituting_our_own_numbers():
    """The Surge XT / Mini V3 rigs are macOS VST3 bundles. On a host without
    them the tool must exit REFUSED under `--require-references`, not print its
    own-model table under a heading that implies a plugin comparison."""
    present = [p for p in msb.REFERENCE_RIG_PATHS if pathlib.Path(p).exists()]
    rc = msb.main(["--require-references", "--note", "57", "81", "105"])
    assert rc == (0 if len(present) == len(msb.REFERENCE_RIG_PATHS) else 3)
