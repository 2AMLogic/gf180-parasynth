"""Tests for #138's promoted metrics. Each answer is closed-form or measured
elsewhere; none is read back from our own model."""
import json
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
import measure_promoted_bands as mpb
import promoted_bands as pb
import promoted_measures as pm
import audio_measure as am

SR = 44100


def test_every_known_signal_is_read_correctly():
    bad = [(l, d) for l, ok, d in mpb.known_cases() if not ok]
    assert not bad, bad


def test_every_injected_bug_is_caught():
    """Start red: a control that is not caught proves nothing."""
    missed = [l for l, caught in mpb.injected_bugs() if not caught]
    assert not missed, missed


def test_a_broken_estimator_turns_the_known_cases_red(monkeypatch):
    """The known cases have power: swap in a band-edge bug and they fail."""
    real = pm.lowband_level_db
    monkeypatch.setattr(pm, "lowband_level_db",
                        lambda x, sr, band=pm.DEFAULT_LOWBAND_HZ, **k: real(x, sr, band=(100.0, 400.0), **k))
    assert any(not ok for _l, ok, _d in mpb.known_cases())


def test_nonfinite_audio_is_refused_by_raising():
    x = np.ones(20000)
    x[5] = np.nan
    with pytest.raises(am.InsufficientEvidence):
        pm.lowband_level_db(x, SR)
    with pytest.raises(am.InsufficientEvidence):
        pm.dominant_period_ms(x, SR, (40.0, 200.0))


def test_the_period_band_has_no_default():
    with pytest.raises(TypeError):
        pm.dominant_period_ms(np.ones(20000), SR)


def test_the_study_band_would_miss_a_low_tom():
    """Why the band is per-voice: the study's 120-1200 Hz excludes LT's 80 Hz."""
    t = np.arange(int(0.24 * SR)) / SR
    x = np.sin(2 * np.pi * 80 * t) * np.exp(-t / 0.1) + 0.2 * np.sin(2 * np.pi * 400 * t)
    study = pm.dominant_period_ms(x, SR, (120.0, 1200.0))
    voice = pm.dominant_period_ms(x, SR, (40.0, 200.0))
    assert voice.ok and abs(voice.value - 12.5) < 0.05
    assert not study.ok or abs(study.value - 12.5) > 1.0


def test_no_floor_means_every_tolerance_refuses():
    """The honest state today: MACHINE_FLOOR is empty and nothing is invented."""
    assert pb.MACHINE_FLOOR == {}
    for m in pb.METRICS:
        for v in ("BD", "LT", "HC", "CB"):
            tol, basis = pb.band_tolerance(m, v, 5.0)
            assert math.isnan(tol) and "REFUSED" in basis


def test_with_a_floor_the_tolerance_is_the_geometric_mean(monkeypatch):
    monkeypatch.setitem(pb.MACHINE_FLOOR, ("dominant_period_ms", "LT"),
                        dict(value=0.1, source="test"))
    tol, _ = pb.band_tolerance("dominant_period_ms", "LT", 0.4)
    assert tol == pytest.approx(0.2)
    # floor >= ceiling: no usable tolerance, refused rather than clamped
    tol, basis = pb.band_tolerance("dominant_period_ms", "LT", 0.05)
    assert math.isnan(tol) and "REFUSED" in basis
    tol, basis = pb.band_tolerance("dominant_period_ms", "LT", None)
    assert math.isnan(tol) and "REFUSED" in basis


def test_the_repeatability_corpus_has_no_floor_for_these_metrics():
    """Why MACHINE_FLOOR is empty, checked rather than asserted in prose: the
    only repeat data is #111's BD session pair, and it has no entry for either
    metric."""
    d = json.loads((ROOT / "docs" / "bd-repeatability-results.json").read_text())
    names = set(d["session_to_session"]["metrics"])
    assert not any("lowband" in n.lower() or "period" in n.lower() for n in names), names
