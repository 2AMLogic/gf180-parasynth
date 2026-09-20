"""Ground truth and refusal controls for measure_mono_case."""
from __future__ import annotations

import numpy as np
import pytest

import measure_mono_case as m
import voice_fx as vf
import audio_measure as am
import oversampled_osc as os2


def test_measure_accepts_a_known_saw():
    f0 = 440.0
    t = np.arange(0.7 * m.SR) / m.SR
    x = np.zeros_like(t)
    for k in range(1, int(m.SR / (2 * f0))):
        x += np.sin(2 * np.pi * k * f0 * t) / k
    got = m._measure(x / np.max(np.abs(x)), f0)
    assert abs(got["f0_hz"] - f0) < 0.1
    assert got["inharmonic_db"] < -80


def test_measure_reports_absolute_level_against_a_known_tone():
    f0 = 440.0
    t = np.arange(int(0.7 * m.SR)) / m.SR
    got = m._measure(0.5 * np.sin(2 * np.pi * f0 * t), f0)
    assert abs(got["rms_dbfs"] - (-9.0309)) < 0.01
    assert abs(got["peak_dbfs"] - (-6.0206)) < 0.01


def test_measure_refuses_silence():
    with pytest.raises(m.Refused, match="silent"):
        m._measure(np.zeros(int(0.7 * m.SR)), 440.0)


def test_measure_refuses_nonfinite():
    x = np.ones(int(0.7 * m.SR))
    x[10] = np.nan
    with pytest.raises(m.Refused, match="non-finite"):
        m._measure(x, 440.0)


def test_high_note_oscillator_filter_reduces_alias_metric():
    """The fix must move the independent inharmonic-energy measurement."""
    note = 84
    f0 = vf.note_hz(note)
    n = int(m.SECONDS * m.SR)
    inc = vf.phase_inc(f0)
    raw = vf.OscFx("saw", smooth=False).render(n, inc) / 32768.0
    filtered = vf.OscFx("saw", smooth=True).render(n, inc) / 32768.0
    before = am.inharmonic_fraction_db(raw, f0, m.SR).require("raw")
    after = am.inharmonic_fraction_db(filtered, f0, m.SR).require("filtered")
    assert before - after > 10.0, (before, after)


def test_true_2x_path_is_close_to_the_qualified_external_target():
    note = 84
    f0 = vf.note_hz(note)
    n = int(m.SECONDS * m.SR)
    fixed = os2.render_saw(n, vf.phase_inc(f0)) / 32768.0
    measured = m._measure(fixed, f0)
    # Surge's independently measured target is -47.2265 dB. Keep this gate
    # local and deterministic; the live plugin comparison remains in the
    # measurement command.
    assert measured["inharmonic_db"] < -44.0
    assert abs(measured["f0_cents"]) < 0.02
