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


def test_the_harness_starts_red_against_stubs():
    """Rule 1, run rather than claimed: against a stub that answers a constant
    and one that always refuses, every case outside STUB_MAY_PASS is red. The
    first run of this found `lowband a_hi=0.1` passing the 0.0 stub."""
    for name, red, green in mpb.start_red():
        assert set(green) <= mpb.STUB_MAY_PASS[name], (name, sorted(set(green) - mpb.STUB_MAY_PASS[name]))
        assert red, name


@pytest.mark.parametrize("label,attr,make,case", mpb.MUTANTS, ids=[m[0] for m in mpb.MUTANTS])
def test_each_mutant_turns_its_named_case_red(monkeypatch, label, attr, make, case):
    """Rule 5's three conditions, per mutant: the named case passes clean, the
    mutant is what known_cases() executes, and THAT case goes red."""
    clean = {l: ok for l, ok, _d in mpb.known_cases()}
    assert clean[case], f"{case} must pass clean"
    calls = []
    repl = make()
    if callable(repl):
        def spy(*a, _r=repl, **k):
            calls.append(1)
            return _r(*a, **k)
        repl = spy
    monkeypatch.setattr(pm, attr, repl)
    broken = {l: ok for l, ok, _d in mpb.known_cases()}
    if callable(repl):
        assert calls, f"mutant for {label} never executed"
    assert not broken[case], f"{label}: {case} stayed green"


def test_the_shipped_controls_report_caught():
    missed = [(l, c, d) for l, c, caught, d in mpb.injected_bugs() if not caught]
    assert not missed, missed


def test_a_rectangular_window_cannot_pass_any_lowband_case(monkeypatch):
    """The tolerance has margin: at 0.05 dB a rectangular window passed three
    of four cases; at 0.01 it passes none, while Hann is within 0.0004 dB."""
    monkeypatch.setattr(pm, "np", mpb._NumpyWithRectangularWindow())
    lb = [ok for l, ok, _d in mpb.known_cases() if l.startswith("lowband a_hi")]
    assert lb and not any(lb)


def test_period_error_on_steady_tones_is_far_below_a_one_percent_difference():
    """What the period estimator's own error is, on what it was validated on:
    steady decaying sines (no glide). Pinned so the text quoting it is true."""
    err = mpb.period_error_on_known_cases()
    assert 0.0 < err < 0.1, err


def test_the_whole_clip_hann_all_but_ignores_the_first_30_ms():
    """The single Hann taper over the 240 ms conditioned clip: mean amplitude
    weight 0.05 over the first 30 ms, and that region carries 0.15 % of the
    window's power budget (12.5 % if flat). The onset excess `cqt.0-200Hz`
    was promoted for lives there, so lowband_level_db cannot see it."""
    n = int(0.24 * SR)
    w = np.hanning(n)
    k = int(0.03 * SR)
    assert w[:k].mean() == pytest.approx(0.050, abs=0.001)
    assert (w[:k] ** 2).sum() / (w ** 2).sum() == pytest.approx(0.0015, abs=0.0001)


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
