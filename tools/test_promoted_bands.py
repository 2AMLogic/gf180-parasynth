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
import measure_repeatability as mr
import promoted_bands as pb
import promoted_measures as pm
import audio_measure as am
import discrimination_features as df
import run_case as rc

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


@pytest.mark.parametrize("label,module,attr,make,case", mpb.MUTANTS,
                         ids=[m[0] for m in mpb.MUTANTS])
def test_each_mutant_turns_its_named_case_red(monkeypatch, label, module, attr, make, case):
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
    monkeypatch.setattr(module, attr, repl)
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
    """The honest state today: no override is recorded, the committed #111
    record holds no entry for either metric, and nothing is invented."""
    assert pb.MACHINE_FLOOR == {}
    for m in pb.METRICS:
        for v in ("BD", "LT", "HC", "CB"):
            tol, basis = pb.band_tolerance(m, v, 5.0)
            assert math.isnan(tol) and "REFUSED" in basis


def test_the_shipped_record_still_has_no_floor_for_either_metric():
    """The live state, read off the file rather than asserted in prose. When
    `measure_repeatability.py --all` is next re-run on a host that has the
    reference packs, this test is what goes red, and that is the signal to
    re-derive the tolerances and revisit board registration -- not a
    regression."""
    d = json.loads((ROOT / "docs" / "bd-repeatability-results.json").read_text())
    have = set(d["session_to_session"]["metrics"])
    assert not (set(pb.METRICS) & have), sorted(set(pb.METRICS) & have)


def test_both_metrics_are_registered_in_the_harness_that_produces_floors():
    """The wiring, not the number. A floor can only ever appear in that record
    if the #111 harness measures these two metrics, so the registration is the
    thing to pin; without it the refusal above would be permanent and silent."""
    plan = mr.metrics()
    for m in pb.METRICS:
        assert m in plan, (m, sorted(plan))
    assert plan["lowband_level_db"][0] == "dB"
    assert plan["dominant_period_ms"][0] == "ms"


def test_the_harness_reads_the_promoted_estimators_on_a_closed_form_bass_drum():
    """Ground truth for the registration, independent of our model: the #111
    fixture is one damped 50 Hz sinusoid, so its period is EXACTLY 20 ms.

    It also reproduces, on a signal with a known answer, the saturation that
    `promoted_measures`' header warns about -- essentially all of this
    fixture's energy is inside 40-200 Hz, so `lowband_level_db` reads ~0 dB
    and cannot discriminate. That is why a BD floor would not by itself put
    `lowband_level_db` on the board."""
    plan = mr.metrics()
    y = rc.prepare(mr.synthetic_bd(44100, f0=50.0), 44100)
    per = plan["dominant_period_ms"][1](y, 44100)
    assert per.ok and abs(per.value - 20.0) < 0.01, per
    lb = plan["lowband_level_db"][1](y, 44100)
    assert lb.ok and abs(lb.value) < 0.01, lb


def test_the_harness_floor_carries_its_provenance_into_the_basis():
    doc = mpb._repeat_doc(floor=0.04, span=0.01)
    entry, why = pb.harness_floor("lowband_level_db", "BD", doc)
    assert why is None and entry["value"] == 0.04
    assert "abs_diff_median" in entry["source"] and "#111" in entry["source"]
    tol, basis = pb.band_tolerance("lowband_level_db", "BD", 0.25, doc=doc)
    assert tol == pytest.approx(0.1) and "abs_diff_median" in basis


def test_an_explicit_override_wins_over_the_harness():
    """The override table is not dead code: a floor measured somewhere other
    than #111 must be usable, and must be the one that is used."""
    doc = mpb._repeat_doc(floor=0.04, span=0.01)
    pb.MACHINE_FLOOR[("lowband_level_db", "BD")] = dict(value=0.25, source="unit test")
    try:
        tol, basis = pb.band_tolerance("lowband_level_db", "BD", 1.0, doc=doc)
        assert tol == pytest.approx(0.5) and "unit test" in basis
    finally:
        pb.MACHINE_FLOOR.pop(("lowband_level_db", "BD"))


# --- the third acceptance criterion, as a mechanism rather than a sentence ---

def test_phasejit_is_not_promoted():
    """#138 AC3. `jit.phasejit_ppm` cannot tell drift from beating (see
    `model/discrimination_features.test_static_detuning_is_not_reported_as_drift`),
    so it is not a promoted metric and must not become one by accident."""
    assert not any("phasejit" in m for m in pb.METRICS), pb.METRICS
    assert not any("phasejit" in n for n in dir(pm)), [n for n in dir(pm) if "phasejit" in n]


def test_the_column_phasejit_must_be_quoted_beside_still_exists():
    """The pairing rule names `jit.dphase_ar1`. A rule naming a column that
    does not exist is unsatisfiable, and would read exactly like one that is
    being followed."""
    names = df.jitter_features(np.sin(2 * np.pi * 200 * np.arange(8192) / SR), SR)[1]
    assert "jit.dphase_ar1.seg0" in names, names
    assert "jit.phasejit_ppm.seg0" in names, names


@pytest.mark.parametrize("rel", sorted(
    p.relative_to(ROOT).as_posix()
    for p in list(ROOT.glob("docs/*.md")) + list(ROOT.glob("model/*.py"))
    + list(ROOT.glob("tools/*.py")) + list(ROOT.glob("tools/probes/*.py"))
    if "phasejit" in p.read_text(encoding="utf-8", errors="ignore")))
def test_nothing_names_phasejit_without_naming_its_pairing_column(rel):
    """#138 AC3, enforced: every file that mentions `phasejit` also mentions
    `dphase_ar1`, so the number that separates drift from beating is never
    more than a search away from the number that cannot."""
    body = (ROOT / rel).read_text(encoding="utf-8")
    assert "dphase_ar1" in body, (
        f"{rel} names phasejit without dphase_ar1 beside it; see "
        f"docs/discrimination.md 5c")


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
