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


def test_the_onset_window_gives_the_first_30_ms_its_own_weight():
    """The quantitative reason `lowband_onset_db` exists, both figures computed
    here rather than quoted. Against the whole-clip Hann's 0.15 % of the power
    budget and 0.050 mean amplitude weight (test above), an 80 ms window gives
    the first 30 ms 19.8 % and 0.350 -- 132x and 7.0x.

    WRONG-THEN-RIGHT: `promoted_measures`' header first said "roughly twenty
    times", which is neither figure. This test is why the file does not say it.
    """
    w = np.hanning(int(0.080 * SR))
    k = int(0.030 * SR)
    assert (w[:k] ** 2).sum() / (w ** 2).sum() == pytest.approx(0.198, abs=0.002)
    assert w[:k].mean() == pytest.approx(0.350, abs=0.002)
    whole = np.hanning(int(0.24 * SR))
    assert w[:k].mean() / whole[:k].mean() == pytest.approx(7.0, abs=0.1)


def test_a_30_ms_window_cannot_read_a_40_hz_edge_and_says_so():
    """#138's third increment, and the finding inside it: the reading the
    trajectory report's own 30 ms resolution would suggest does not exist at a
    40 Hz lower edge, and the estimator REFUSES rather than approximating it.

    The refusal is a property of the band/window PAIR. Raising the lower edge
    to 150 Hz makes the same 30 ms window legal and exact, which is what stops
    this being a blanket ban on short windows."""
    t = np.arange(int(0.24 * SR)) / SR
    x = np.sin(2 * np.pi * 90 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    e = pm.lowband_onset_db(x, SR, window_ms=30.0)
    assert not e.ok and "cannot resolve" in e.reason
    assert e.detail["need_window_ms"] == pytest.approx(75.0)
    x2 = np.sin(2 * np.pi * 300 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    ok = pm.lowband_onset_db(x2, SR, (150.0, 600.0), 30.0)
    assert ok.ok and ok.value == pytest.approx(10 * math.log10(1 / 1.09), abs=0.01)


def test_a_window_that_does_not_fit_is_refused_not_truncated():
    """`x[a:a+n]` truncates silently in Python, so this check is the only thing
    between a caller and a number for a window that was not in the clip -- and
    that number would be compared against full-length ones."""
    t = np.arange(int(0.24 * SR)) / SR
    x = np.sin(2 * np.pi * 90 * t)
    e = pm.lowband_onset_db(x, SR, start_ms=200.0)
    assert not e.ok and "fit inside" in e.reason
    assert pm.lowband_onset_db(x, SR, start_ms=160.0).ok


def test_the_two_low_band_readings_are_not_interchangeable():
    """They are the same ratio over different spans, which is exactly why one
    must never be substituted for the other: on a clip whose low band is
    early-only they differ by 9.17 dB. `band_tolerance` keys on the metric
    name, so their floors cannot be crossed either."""
    x = mpb._early_low_band_clip()
    on, whole = pm.lowband_onset_db(x, SR), pm.lowband_level_db(x, SR)
    assert on.ok and whole.ok
    assert on.value - whole.value == pytest.approx(9.17, abs=0.05)
    assert "lowband_onset_db" in pb.METRICS and "lowband_level_db" in pb.METRICS
    doc = mpb._repeat_doc(metric="lowband_level_db", floor=0.04, span=0.01)
    tol, basis = pb.band_tolerance("lowband_onset_db", "BD", 0.25, doc=doc)
    assert math.isnan(tol) and "REFUSED" in basis, basis


def test_a_glide_is_read_as_its_settled_frequency_not_the_notes_mean():
    """#138 AC2's missing validation case: every 808 tom glides, and
    `promoted_measures` said the reader's error on one was unknown.

    Both halves are asserted, because only the pair makes the reading safe to
    quote: it tracks f(end) to 1.12 % over 0-20 % depth, and it is 3.12 % away
    from the energy-weighted mean at 10 % depth. The second is what stops a
    reading being called the pitch of the note."""
    assert mpb.glide_error_vs_settled_pct() == pytest.approx(1.124, abs=0.01)
    assert mpb.glide_error_vs_settled_pct() > mpb.period_error_on_known_cases()
    for depth, f_end, f_bar, got, e_set, e_mean in mpb.glide_report():
        assert got == pytest.approx(f_end, rel=0.025), depth
        if depth >= 0.10:
            assert abs(e_mean) > 2.0, (depth, e_mean)
            assert e_mean < 0.0 < e_set, (depth, e_set, e_mean)


def test_a_glide_depth_difference_alone_moves_the_apparent_pitch():
    """The consequence recorded in `promoted_measures`' header, measured rather
    than argued: two arms at the SAME settled pitch but different glide depths
    read differently, by ~1 % at 20 points of depth difference. That is the same
    order as the 0.44-2.01 % ours-minus-machine figures in
    docs/discrimination.md 5c, so a pitch difference between two arms is not
    evidence of a pitch difference until their glide depths are known."""
    reads = []
    for depth in (0.0, 0.20):
        x, _ = mpb._glide(90.0, depth)
        e = pm.dominant_period_ms(x, SR, (40.0, 200.0))
        assert e.ok
        reads.append(1000.0 / e.value)
    apparent = 100.0 * (reads[1] / reads[0] - 1.0)
    assert 0.8 < apparent < 1.6, apparent


def test_the_onset_metric_is_registered_in_the_harness_that_produces_floors():
    """Same wiring check as the other two, and the same reason: without a
    registration the floor can never appear and the refusal would be permanent
    and silent. Its UNITS are dB and its estimator is its own, not a view of
    `lowband_level_db`'s."""
    plan = mr.metrics()
    assert plan["lowband_onset_db"][0] == "dB"
    assert plan["lowband_onset_db"][1] is not plan["lowband_level_db"][1]


def test_the_onset_metric_refuses_the_one_voice_that_has_a_repeat_session():
    """HOW THIS TEST WAS WRITTEN MATTERS, so it is recorded here rather than in
    a commit message nobody will read beside the number.

    It was written asserting `abs(e.value) < 0.05` on the #111 bass drum
    fixture, by analogy with `lowband_level_db`, which reads -0.0007 dB there.
    It FAILED at -0.798 dB. The cause was not the attack and not the fixture's
    click (click=0 reads -0.798 too): all 16.8 % of the "out of band" energy is
    BELOW 40 Hz, and it is the 50 Hz line's own lower skirt. An 80 ms Hann has a
    25 Hz main-lobe half-width and the line sits 10 Hz above the band edge.
    `EDGE_LEAK_MAX` and the guard exist because of this failure.

    So the registered estimator REFUSES this fixture, and the refusal is the
    correct reading. The consequence is a data gap, stated here because this is
    the test that establishes it: clearing a 50 Hz line of a 40 Hz edge needs a
    main-lobe half-width under 10 Hz, i.e. a window of at least 200 ms -- which
    is not an onset window. **`lowband_onset_db` therefore cannot be floored
    from the only repeat-session voice this repository has**, in any window, and
    needs a second recording of a voice whose lines sit clear of both edges."""
    plan = mr.metrics()
    y = rc.prepare(mr.synthetic_bd(44100, f0=50.0), 44100)
    e = plan["lowband_onset_db"][1](y, 44100)
    assert not e.ok and "main lobe" in e.reason, e
    assert e.detail["edge_leak"] == pytest.approx(0.200, abs=0.005)
    assert e.detail["main_lobe_half_hz"] == pytest.approx(25.0, abs=0.1)
    # ...and the arithmetic behind "at least 200 ms", so the claim above is
    # checked rather than asserted: 2*sr/n <= 50-40 Hz.
    assert 2.0 * 44100 / int(0.200 * 44100) == pytest.approx(10.0, abs=0.01)


def test_the_edge_guard_fires_on_a_straddle_and_not_on_a_clear_line():
    """A guard that fired on everything would be as useless as none. Measured:
    6e-6 of edge-adjacent energy for a 90 Hz line against a 40 Hz edge at
    80 ms, 0.20 for a 50 Hz one. The threshold is 0.05, between them."""
    t = np.arange(int(0.24 * SR)) / SR
    clear = pm.lowband_onset_db(np.sin(2 * np.pi * 90 * t), SR)
    assert clear.ok and clear.detail["edge_leak"] < 0.001, clear
    straddle = pm.lowband_onset_db(np.sin(2 * np.pi * 50 * t), SR)
    assert not straddle.ok and "main lobe" in straddle.reason
    assert clear.detail["edge_leak"] < pm.EDGE_LEAK_MAX < straddle.detail["edge_leak"]


def test_the_whole_clip_reading_reports_its_edge_leak_but_does_not_refuse():
    """The known hole, pinned so it cannot be mistaken for an absence of one.
    The same straddle applies to any band share, but turning the guard on for
    `lowband_level_db` would turn already-reported Fischer readings into
    refusals and `docs/promoted-bands-results.json` can only be re-measured on
    a host with the reference packs. So the number is reported in `detail` on
    every reading and the refusal waits for that run."""
    t = np.arange(int(0.24 * SR)) / SR
    e = pm.lowband_level_db(np.sin(2 * np.pi * 50 * t), SR)
    assert e.ok, e
    assert "edge_leak" in e.detail and "main_lobe_half_hz" in e.detail
    # A 240 ms window's half-width is 8.3 Hz, which does clear 50 from 40 --
    # which is exactly why the whole-clip reading did not expose this at all.
    assert e.detail["main_lobe_half_hz"] == pytest.approx(8.33, abs=0.05)


def test_the_stub_allowance_list_is_derived_from_the_metric_list():
    """A hand-written allowance list goes stale the moment a metric is
    promoted, and a stale entry reads as a case the stub is ALLOWED to pass.
    Checked here because this is exactly the mistake the third increment would
    have made: `floor_cases` generates one case per metric."""
    for m in pb.METRICS:
        assert f"the shipped #111 record has no floor for {m}" in mpb._FLOOR_REFUSALS
    labels = {l for l, _ok, _d in mpb.known_cases()}
    for name, allowed in mpb.STUB_MAY_PASS.items():
        assert allowed <= labels, (name, sorted(allowed - labels))


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
