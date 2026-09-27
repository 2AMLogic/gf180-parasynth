#!/usr/bin/env python3
"""The pitch-drop law's own bench: the fit, the comparator, and the constants
drums_fx actually ships, each with the refusals demonstrated red.

`model/tom_drop_fit.py` and `model/tom_drop_compare.py` produced the numbers in
`docs/tom-pitch-drop-correction.md`. A tool that produced a number is a
deliverable, so it carries its validation cases here -- including the three
preconditions that must REFUSE rather than answer.
"""
from __future__ import annotations
import json, math, os, pathlib, sys
import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audition"))
import drums_fx as dx
import tom_drop_fit as F
import tom_drop_compare as C
import tom_drop_docs as D

REPO = HERE.parent
LAW = json.loads((REPO / "docs" / "tom-pitch-drop-law.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------- the fit ----

def test_the_fit_reproduces_the_committed_law():
    """Re-running the fit on the merged measurement must give back the constants
    the correction was published with. If `docs/tom-pitch-drop-results.json`
    ever changes, this is what notices."""
    r = F.run()
    assert r["tom"]["K"] == pytest.approx(LAW["tom"]["K"], rel=1e-9)
    assert r["tom"]["A0"] == pytest.approx(LAW["tom"]["A0"], rel=1e-9)
    assert r["G"]["tom"] == pytest.approx(LAW["G"]["tom"], rel=1e-9)
    assert r["conga"]["A0"] == pytest.approx(LAW["conga"]["A0"], rel=1e-9)


def test_what_drums_fx_SHIPS_is_what_the_fit_measured():
    """The constants in the block and the constants in the fit are the same
    numbers. Rounding to three or four places is allowed; drifting is not.

    This is the join that matters: a law derived in one file and a coefficient
    typed into another is exactly the shape of defect this repository keeps
    finding."""
    K = LAW["tom"]["K"]
    A0 = LAW["tom"]["A0"]
    assert dx.TOM_DROP_ACCENT_0 == pytest.approx(A0, abs=5e-4)
    assert dx.TOM_DROP_ACCENT_0_CONGA == pytest.approx(LAW["conga"]["A0"], abs=5e-4)
    assert dx.TOM_DROP_TUNING_G == pytest.approx(LAW["G"]["tom"], abs=5e-3)
    assert dx.TOM_DROP_TUNING_G_CONGA == pytest.approx(LAW["G"]["conga"], abs=5e-3)
    # TOM_DROP_RATIO is the law evaluated at the reference setting.
    assert dx.TOM_DROP_RATIO - 1.0 == pytest.approx(K * (1.0 - A0), abs=5e-4)
    # and the block's own function agrees with the fit's, away from the anchor
    for accent in (0.0, 0.5, 1.0, 1.4, 2.0):
        for f0 in (0.93 * 90.0, 90.0, 1.07 * 90.0):
            want = F.predict(accent, f0 / 90.0 - 1.0, K, A0, LAW["G"]["tom"])
            got = dx.tom_drop_excess(dx.M_LT, f0, accent)
            assert got == pytest.approx(want, abs=2e-3), (accent, f0, got, want)


def test_the_fit_refuses_when_the_measurement_is_missing():
    with pytest.raises(F.Refused):
        F.load_rows(REPO / "docs" / "no-such-measurement.json")


def test_the_fit_refuses_an_accent_law_that_shrinks_with_accent():
    """An unsatisfiable law is worse than none. If the levels ever come back
    ordered the other way, the fit must REFUSE rather than ship a negative
    slope that would make a hard hit sweep less than a soft one."""
    centre = {"A": {"mean": 0.24}, "B": {"mean": 0.14}, "C": {"mean": 0.05}}
    with pytest.raises(F.Refused):
        F.fit_accent(centre, F.ACCENT_MAP)


def test_the_conga_threshold_needs_the_level_it_is_fitted_to():
    with pytest.raises(F.Refused):
        F.fit_conga_threshold({"A": {"mean": 0.004}}, 0.18, F.ACCENT_MAP)


# -------------------------------------------------------- the comparator ----

def test_the_comparator_refuses_audio_the_index_does_not_vouch_for():
    """Three preconditions, each demonstrated red: a member that is not in the
    committed index, a member the index knows but the cache does not have, and
    a file whose size is not the size the index states."""
    sizes = C.indexed_sizes()
    assert len(sizes) > 100, "the committed index should list the whole pack"
    with pytest.raises(C.Refused):
        C.reference_wav("LT", "A", 99, sizes)                 # not indexed
    with pytest.raises(C.Refused):
        C.reference_wav("LT", "A", 6, dict(sizes, **{C.member("LT", "A", 6): 1}))  # wrong size
    real = C.member("LT", "A", 6)
    if not (C.CACHE / real).exists():
        with pytest.raises(C.Refused):
            C.reference_wav("LT", "A", 6, sizes)              # indexed, absent


def test_the_resample_control_refuses_a_conversion_that_filters_the_onset(monkeypatch):
    """`validate_resample` is the comparator's own injected-bug control and it
    runs on every comparison. The defect it exists to catch is a conversion that
    damages the SWEEP, so that is what is injected: a band-pass around the
    fundamental, which #110's own validation measured as costing 40 % of the
    excess to `filtfilt` pre-ringing landing on the first retained period.

    Note what does NOT work as an injection, because it says something about the
    estimator: a symmetric moving average is zero-phase and does not move the
    zero crossings of a sinusoid at all, so it changes the amplitude and not the
    measurement. The probe reads times, not levels."""
    from scipy.signal import resample_poly, butter, sosfiltfilt

    def filtered(x):
        y = resample_poly(np.asarray(x, dtype=np.float64), 147, 160)
        sos = butter(4, [0.75 * 90.0 / 22050.0, 2.05 * 90.0 / 22050.0],
                     btype="band", output="sos")
        return sosfiltfilt(sos, y)

    monkeypatch.setattr(C, "to_44100", filtered)
    with pytest.raises(C.Refused):
        C.validate_resample()


def test_a_uniform_rate_error_is_invisible_to_this_control_and_that_is_correct(monkeypatch):
    """Recorded because it is a real blind spot, not because it is a pass.

    Converting to the WRONG rate scales every frequency in the file by the same
    factor, and the measurement is a RATIO of two frequencies from that file, so
    the error cancels exactly. `validate_resample` therefore cannot see it, and
    no tolerance on the ratio ever could. What rules it out instead is that the
    conversion is a fixed 147/160 written once, and that `tom_pitch_probe`
    REFUSES any file whose sample rate is not 44100 -- a precondition, not a
    tolerance."""
    from scipy.signal import resample_poly
    monkeypatch.setattr(C, "to_44100", lambda x: resample_poly(np.asarray(x, float), 140, 160))
    r = C.validate_resample()                       # does NOT refuse
    assert max(c["rel_error_of_excess"] for c in r["cases"]) < 0.01
    assert C.P.SR_EXPECTED == 44100
    bad = P_measure_at_wrong_rate()
    assert bad["verdict"] == "REFUSED" and "sample rate" in bad["why"]


def P_measure_at_wrong_rate():
    import tom_pitch_probe as P
    y = P.synth_tom(90.0, 25.0, P.tau_from_q(90.0, 25.0), ratio=1.24, sr=44100, trim=False)
    return P.measure(y, 48000, label="wrong rate")


def test_the_resample_control_passes_the_conversion_actually_used():
    r = C.validate_resample()
    assert len(r["cases"]) == 3
    for c in r["cases"]:
        assert c["rel_error_of_excess"] < 0.01, c


def test_the_dead_tail_trim_touches_nothing_inside_the_ring():
    x = np.array([0.0, 0.3, -0.2, 0.1, 0.0, 0.0, 0.0])
    y = C.trim_dead_tail(x)
    assert len(y) == 4 and np.array_equal(y, x[:4])
    assert np.array_equal(C.trim_dead_tail(np.zeros(5)), np.zeros(5))


# ------------------------------------------------- the law's own extremes ----

def test_the_tuning_term_is_clamped_to_the_pot_it_was_measured_over():
    """Reference 1.7 puts the TUNING pot at +-10 %, which is also the span the
    99 files cover. A host may write any f0; the law must not extrapolate past
    the audio it came from."""
    inside = dx.tom_drop_excess(dx.M_LT, 90.0 * 1.10, 2.0)
    outside = dx.tom_drop_excess(dx.M_LT, 90.0 * 1.25, 2.0)
    assert outside == pytest.approx(inside, rel=1e-12)
    lo_in = dx.tom_drop_excess(dx.M_LT, 90.0 * 0.90, 2.0)
    assert dx.tom_drop_excess(dx.M_LT, 90.0 * 0.80, 2.0) == pytest.approx(lo_in, rel=1e-12)


def test_the_position_boundary_sits_between_the_two_sounds_not_inside_either():
    """The circuit's position is read off the tuning, so the boundary matters.
    It is the geometric mean of the pair -- 129 Hz for LT/LC -- and both sounds'
    whole pot ranges clear it: a tom detuned +25 % is 112 Hz and a conga
    detuned -25 % is 139 Hz. A host that writes something between them gets the
    nearer position, which is the only answer available."""
    for name, mode in (("LT", dx.M_LT), ("MT", dx.M_MT), ("HT", dx.M_HT)):
        conga = {"LT": "LC", "MT": "MC", "HT": "HC"}[name]
        f_tom, f_conga = dx.TOM_PRESET[name][0], dx.TOM_PRESET[conga][0]
        assert f_conga > 1.7 * f_tom, (name, f_tom, f_conga)
        for k in (0.75, 0.9, 1.0, 1.1, 1.25):
            assert dx.tom_position(mode, f_tom * k) == name, (name, k)
            assert dx.tom_position(mode, f_conga * k) == conga, (conga, k)


def test_a_mode_that_is_not_a_tom_circuit_gets_no_sweep():
    assert dx.tom_position(dx.M_BD, 49.4) is None
    assert dx.tom_drop_excess(dx.M_BD, 49.4, 2.0) == 0.0


def test_each_step_holds_the_intervals_mean_of_the_law():
    """The written coefficient sequence must be the interval-mean staircase,
    not the left-edge one. Checked against the closed form directly, because
    this is the defect that made the delivered sweep 22-38 % larger than the
    law it was delivering."""
    f0, q, amp = 90.0, 25.0, 0.2
    w = dx.tom_pitch_drop_writes(0, dx.M_LT, f0, q, amp, 2.0)
    s = dx.TOM_DROP_STEPS
    e = dx.tom_drop_excess(dx.M_LT, f0, 2.0)
    assert len(w) == 2 * (s + 1)
    for i in range(s + 1):
        a1, a2 = w[2 * i][2], w[2 * i + 1][2]
        got, _ = dx.poles_from_regs(a1, a2)
        shape = ((s / 3.0) * (math.exp(-3.0 * i / s) - math.exp(-3.0 * (i + 1) / s))
                 if i < s else math.exp(-3.0))
        assert got == pytest.approx(f0 * (1.0 + e * shape), rel=2e-3), i
    assert w[0][0] == 0
    assert w[-1][0] == int(round(dx.TOM_DROP_MS * 1e-3 * dx.SR))


# ------------------------------------------ the prose against the tree (#95) ----

def test_the_reference_documents_carry_the_measured_magnitude():
    """`tr808-reference.md` 4 and `drum-verification.md` 12 must state the
    magnitude, the accent threshold and the tuning slope that `drums_fx`
    actually ships -- and the per-accent table must be #110's own medians.

    THIS IS ISSUE #95's residual. #110 measured the drop and #154 shipped the
    correction, and for a week afterwards both reference documents still said
    x1.7: `tr808-reference.md` in the paragraph that ORIGINATED the inference,
    `drum-verification.md` 8.4 in a table that had upgraded it from *magnitude
    inferred* to *verified in a source*. Nothing caught that, because nothing
    was checking. This is the check."""
    ref = D.REFERENCE.read_text(encoding="utf-8")
    ver = D.VERIFICATION.read_text(encoding="utf-8")
    assert D.prose_disagreements(ref, ver) == []
    # and the refuted figure is still named as refuted, not quietly deleted:
    # a reader who arrives with x1.7 in hand has to be able to find out why.
    assert "REFUTED" in ver
    assert "x1.7" in ver.replace("×1.7", "x1.7")


def test_the_prose_check_refuses_a_document_with_no_law_in_it(monkeypatch):
    """The pre-#154 documents, verbatim. A document that does not state the law
    cannot agree with it, and REFUSED is the only honest verdict -- reporting
    OK for an absent claim is how a claim outlives its evidence.

    Run against the real files as they stood on `origin/main`, not a mock, so
    this test also demonstrates that the gate was RED before the change."""
    import subprocess
    try:
        old = subprocess.run(["git", "-C", str(REPO), "show",
                              "origin/main:docs/tr808-reference.md"],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("no origin/main in this checkout")
    if "1.063" in old:
        pytest.skip("origin/main already carries the correction")
    with pytest.raises(D.Refused):
        D.stated_law(old)


def test_the_prose_check_goes_STALE_if_the_inferred_magnitude_is_restored():
    """The injected defect: put x1.7 back in the reference document's table and
    leave everything else alone. STALE, not REFUSED -- the evidence was found
    and it contradicts the prose."""
    ref = D.REFERENCE.read_text(encoding="utf-8")
    ver = D.VERIFICATION.read_text(encoding="utf-8")
    hurt = ref.replace("**×1.063**", "**×1.700**")
    assert hurt != ref, "the amendment table moved; this control no longer injects anything"
    bad = D.prose_disagreements(hurt, ver)
    assert any("x1.7" in b for b in bad), bad


def test_the_prose_check_goes_STALE_if_the_shipped_constant_moves(monkeypatch):
    """The other direction, which is the one that will actually happen: the code
    changes and the prose does not. A future refit must turn this red."""
    monkeypatch.setattr(dx, "TOM_DROP_RATIO", 1.200)
    monkeypatch.setattr(dx, "TOM_DROP_ACCENT_0", 0.400)
    bad = D.prose_disagreements(D.REFERENCE.read_text(encoding="utf-8"),
                                D.VERIFICATION.read_text(encoding="utf-8"))
    assert any("TOM_DROP_RATIO" in b for b in bad), bad
    assert any("A0 (tom)" in b for b in bad), bad


# --------------------------------------------- the congas re-examined (#95) ----

def test_the_congas_carry_neither_the_clamp_nor_the_missing_tuning_term():
    """#95's last question: the congas share the toms' circuit at about half the
    drop, so do they share the two defects the toms had?

    They do not. Against #110's 63 conga files, each placed on the MODEL's pot
    (chart f0 scaled by that row's own u -- centre to centre, see
    `tom_drop_docs`), the shipped law is within 0.0055 of excess on every cell's
    median and 0.0226 at worst. 0.005 of excess is under 1 Hz at LC and under
    2 Hz at HC, which is the board's own `pitch_drop_hz` floor.

    The *No Accent* cells read shipped 0 against a measured 0.003-0.005 -- the
    conga threshold A0 = 1.064 sits above an unaccented hit, and the machine's
    unaccented congas sit at the measurement floor. That agreement is the
    threshold working, and it is exactly what the old clamp destroyed."""
    r = D.conga_verdict()
    assert r["verdict"] == "PASS", r["failing_cells"]
    assert set(r["cells"]) == {"LC A", "LC B", "MC A", "MC B", "HC A", "HC B"}
    assert r["max_abs_median"] < D.CONGA_MEDIAN_MAX
    assert r["worst"] < D.CONGA_WORST_MAX


def test_the_conga_check_fires_when_the_accent_clamp_is_restored():
    """Injected defect 1: the pre-#154 clamp, at the CORRECTED magnitude, so
    this isolates the clamp from the constant. An unaccented conga then gets
    the full 0.060 where the machine does 0.004, and the No Accent cells' median
    error goes to +0.055 -- five times the bound."""
    def clamped(mode, f0_hz, accent):
        if dx.tom_position(mode, f0_hz) is None:
            return 0.0
        return (dx.TOM_DROP_RATIO - 1.0) * min(max(accent, 0.0), 1.0)

    r = D.conga_verdict(clamped)
    assert r["verdict"] == "FAIL"
    assert r["max_abs_median"] > 5 * D.CONGA_MEDIAN_MAX, r["max_abs_median"]
    assert {"LC A", "MC A", "HC A"} <= set(r["failing_cells"]), r["failing_cells"]


def test_the_conga_check_fires_when_the_conga_is_given_the_toms_tuning_slope():
    """Injected defect 2: G pooled across both positions, i.e. the conga run on
    the tom's 3.58 instead of its own 7.46.

    NOTE WHICH BOUND CATCHES IT, because it is a design point and not an
    accident. At the pot CENTRE the two slopes agree exactly -- exp(G*0) = 1 for
    any G -- so a tuning fault is invisible to a centre-only comparison and
    invisible to the median. It shows only at the pot's ends, which is why this
    check reads every file at its own u and bounds the WORST row as well as the
    median. A centre-only version of this test would have been a false green."""
    def pooled_g(mode, f0_hz, accent):
        name = dx.tom_position(mode, f0_hz)
        if name is None:
            return 0.0
        conga = name in D.CONGAS
        a0 = dx.TOM_DROP_ACCENT_0_CONGA if conga else dx.TOM_DROP_ACCENT_0
        drive = max(0.0, accent - a0) / (1.0 - dx.TOM_DROP_ACCENT_0)
        if drive <= 0.0:
            return 0.0
        u = f0_hz / dx.TOM_PRESET[name][0] - 1.0
        u = min(max(u, -dx.TOM_DROP_TUNING_SPAN), dx.TOM_DROP_TUNING_SPAN)
        return (dx.TOM_DROP_RATIO - 1.0) * drive * math.exp(dx.TOM_DROP_TUNING_G * u)

    clean = D.conga_verdict()
    hurt = D.conga_verdict(pooled_g)
    assert hurt["verdict"] == "FAIL", hurt
    assert hurt["worst"] > 2 * clean["worst"], (clean["worst"], hurt["worst"])
    # the medians barely move: that is the blind spot this control documents
    assert hurt["max_abs_median"] == pytest.approx(clean["max_abs_median"], abs=2e-3)


def test_the_conga_re_examination_refuses_a_voice_it_has_no_files_for():
    with pytest.raises(D.Refused):
        D.conga_cell_errors(voices=("XX",))
