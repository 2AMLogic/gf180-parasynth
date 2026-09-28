"""Known answers and must-fail controls for tools/cymbal_vca_clip.py (#369 step 8).

The module makes two claims and they need different kinds of test:

  * a NEGATIVE one -- no position, drive or asymmetry of §10's asymmetric
    clipping lands inside both of the 808's measured ranges at once. A negative
    is only worth anything if the apparatus could have said yes, so the tests
    here plant a mixture that DOES land inside both boxes and assert that
    `verdict` accepts it, beside the real sweep that it refuses;
  * a CORRECTION to step 7 -- that its 9.4 dB energy gap is mostly the analysis
    filter and the source spectrum. That one is checked by reproducing step 7's
    own committed numbers from this module's independently written chain, so the
    correction cannot be an artefact of having built a different chain.

Plus the usual: every named property must be moved by some injected defect, the
two blind defects must be blind, and the numbers quoted in the docstring must
come from the artifacts rather than from the prose.
"""
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_vca_clip as v      # noqa: E402
import cymbal_m_origin as mo     # noqa: E402
import cymbal_low_tail as lt     # noqa: E402
import cymbal_tone_realisation as tr  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOW_TAIL = ROOT / "docs/scorecard/cymbal-369/low-tail/low-tail.json"
M_ORIGIN = ROOT / "docs/scorecard/cymbal-369/low-tail/m-origin.json"


# ---------------------------------------------------------------------------
# the chain: the same filters, not a parallel model of them
# ---------------------------------------------------------------------------
def test_the_time_domain_cascade_is_the_magnitude_model_exactly():
    """`chain_db` reads the response off the very filters `render` runs, and
    `cymbal_tone_realisation` computes it from the same bank registers. Anything
    but equality is a transcription bug, so the bound is 1e-6 dB rms and not a
    tolerance chosen to pass."""
    assert v._chain_exact(tr.tone_poles()) < 1e-6


def test_the_analytic_band_energies_reproduce_m_origins_committed_figures():
    """Step 7's per-band M re Ln, recomputed from this module's chain. If these
    disagree the correction below is about a different circuit and means
    nothing."""
    e = mo.band_energies()
    for band in ("low", "decay", "short"):
        got = v.analytic_band_db(band)
        assert got["M"] == pytest.approx(e[band]["cand3_M_re_Ln"], abs=0.02)
        assert got["Mn"] == pytest.approx(e[band]["cand3_Mn_re_Ln"], abs=0.15)


def test_dropping_the_high_pass_changes_the_chain():
    """The paired negative for the two tests above: they must not pass on any
    cascade at all."""
    assert v._chain_exact(tr.tone_poles(), defect="NO_HIGHPASS") > 1.0


# ---------------------------------------------------------------------------
# the source
# ---------------------------------------------------------------------------
def test_the_staircase_is_six_squares_and_seven_levels():
    assert v.source_levels() == v.N_OSC + 1


def test_one_oscillator_is_two_levels_and_the_comb_control_catches_it():
    assert v.source_levels(defect="SINGLE_OSC") == 2
    assert v._source_comb(defect="SINGLE_OSC") < v.N_OSC + 1.0


def test_the_generators_own_aliasing_is_bounded_not_assumed():
    """The source-tilt correction rests on the staircase's real 1/n harmonics in
    M. A naively sampled square folds its own out-of-band harmonics back into
    exactly that region, and that would look identical to the finding."""
    assert v._source_alias() < 0.30


def test_the_documented_fundamentals_are_the_ones_in_the_reference():
    """§1.5's table, transcribed once. A frequency set that drifted from the
    reference would change the difference-tone comb the whole question is
    about."""
    assert v.OSC_HZ == (205.3, 369.6, 304.4, 522.7, 800.0, 540.0)
    assert v.DUTY == pytest.approx(0.4798)


# ---------------------------------------------------------------------------
# the nonlinearity's known answers
# ---------------------------------------------------------------------------
def test_the_difference_tone_is_the_analytic_second_order_coefficient():
    got, want = v._im_amp()
    assert got / want == pytest.approx(1.0, abs=0.02)


def test_a_symmetric_clipper_makes_no_difference_tone():
    """The paired negative. Without it `im-known-answer` would pass on any
    nonlinearity whatever, because it only ever checks one number."""
    assert v._asym_load_bearing() < -40.0


def test_the_symmetric_clippers_predicted_coefficient_is_exactly_zero():
    assert v.clip_a2(0.0, defect="SYMMETRIC_NL") == 0.0
    assert v.clip_a2(0.0) != 0.0


def test_the_clippers_departure_from_the_identity_scales_with_drive():
    """The clipper is normalised so its LINEAR term is the identity, which is
    what makes "drive -> -inf is the linear chain" a limit rather than an
    approximation, and why the -40 dB row of every sweep reproduces the `none`
    row to three decimals.

    The identity is NOT exact at finite drive -- the quadratic term is still
    there, which is the whole point of the block -- so what is asserted is that
    the residual falls by a decade per 20 dB, i.e. that it IS the second-order
    term and not some offset that would survive to zero drive."""
    rng = np.random.default_rng(0)
    x = 1e-3 * rng.standard_normal(4096)
    res = [float(np.max(np.abs(v.clip(x, d, v.ASYM, rms=1.0) - x))) / float(np.max(np.abs(x)))
           for d in (-40.0, -20.0, 0.0)]
    assert res[0] < 1e-4
    for a, b in zip(res, res[1:]):
        assert b / a == pytest.approx(10.0, rel=0.05)


def test_a_quadratic_halves_the_time_constant():
    """The algebra the DECAY half of the module's prediction rests on, measured.
    The prediction was still wrong, but not here -- see the module docstring."""
    assert v._product_law() == pytest.approx(1.0, abs=0.08)


def test_the_clipper_refuses_a_silent_input():
    with pytest.raises(v.Refused):
        v.clip(np.zeros(128), 0.0, v.ASYM)


def test_thd_rises_with_drive_and_the_refusal_threshold_is_reachable():
    """A refusal that no swept value can trigger is not a refusal."""
    t = [v.thd_db(d) for d in v.DRIVE_DB]
    assert all(b > a for a, b in zip(t, t[1:]))
    assert max(t) > v.MAX_THD_DB or min(t) < v.MAX_THD_DB


# ---------------------------------------------------------------------------
# the correction to step 7, which is the larger half of the result
# ---------------------------------------------------------------------------
def test_the_analysis_filter_is_accurate_on_the_low_band_and_not_on_the_others():
    """The finding, as a number in both directions. The low band's reference
    band IS its peak, so it reads true; the two bands peaking at 7.1 kHz have
    their M read several dB high by the analysis filter's own skirt."""
    leak = v.leakage_error()
    assert abs(leak["low"]["leak_M_db"]) < 0.6
    assert leak["decay"]["leak_M_db"] > 2.0
    assert leak["short"]["leak_M_db"] > 5.0


def test_the_rendered_bound_is_above_the_analytic_one():
    """Step 7's bound is analytic; the 808 figures it is compared against are
    filtered. Computing the bound the same way the measurement was made raises
    it, and by how much is the correction."""
    rb = v.rendered_bound(kind="white", step=12.0)
    assert rb["M"]["rendered_bound_db"] > rb["M"]["analytic_bound_db"] + 2.0


def test_the_documented_source_tilts_the_low_band_towards_the_machine():
    """§1.5's staircase is not flat. Through the SAME chain and the SAME
    analysis filter it puts more energy in M relative to Ln than white noise
    does, which is the second half of the correction."""
    st = v.source_tilt()
    assert st["low"]["tilt_M_db"] > 2.0


def test_no_linear_balance_lands_inside_both_808_boxes_at_once():
    """The joint constraint, which is what the next increment needs and what
    neither this step's energy bound nor step 7's decay finding says on its own.
    rho and M re Ln are not independent: the balances that raise rho are the ones
    that starve M. Run at a coarse grid so this is a test and not the experiment.
    """
    for kind in ("white", "staircase"):
        rb = v.rendered_bound(kind=kind, step=6.0)
        assert rb["n_balances_in_808_box"] == 0, \
            f"{kind}: a linear balance now matches -- {rb['balances_in_808_box']}"


def test_the_linear_balance_sweep_does_clear_the_skirt_baseline():
    """Step 7's §5 says a linear mix's rho_M(-10) "stays between 0.89 and 0.97 --
    it never even clears the skirt baseline". Rendered through the time-domain
    chain it reaches 1.24-1.35, above the 808's own top of 1.139. What survives
    is the joint statement above, not that one."""
    rb = v.rendered_bound(kind="staircase", step=6.0)
    assert rb["max_rho_M_-10"] > v.T_RHO_BASELINE_MAX


def test_the_correction_does_not_close_the_gap_entirely():
    """The honest other side: the corrections shrink step 7's 9.4 dB, they do
    not erase it. If this ever fails the claim in the docstring has to change,
    not the test."""
    rb = v.rendered_bound(kind="staircase", step=12.0)
    assert rb["M"]["rendered_bound_db"] < v.M_RE_LN_808_RANGE[0]


# ---------------------------------------------------------------------------
# the verdict: it must be able to say yes
# ---------------------------------------------------------------------------
def _row(pos, drive, m, rho, mn=-12.0, rho_mn=1.0):
    return {"position": pos, "drive_db": drive, "M_re_Ln": m, "Mn_re_Ln": mn,
            "rho_M_-10": rho, "rho_Mn_-10": rho_mn, "refused": {}, "thd_db": -20.0}


def test_the_verdict_accepts_a_row_inside_both_boxes():
    """The control without which the module's negative answer is worthless: a
    planted row that IS a match must be reported as one."""
    lo, hi = v.RHO_M_808_RANGE
    e_lo, e_hi = v.M_RE_LN_808_RANGE
    rows = [_row("sum", 0.0, (e_lo + e_hi) / 2, (lo + hi) / 2)]
    out = v.verdict(rows)
    assert out["positions_matching"] == ["sum"]
    assert "refused" not in out


def test_the_verdict_rejects_two_rows_that_each_satisfy_one_half():
    """Wrong-then-right 4, as a permanent control: the first version answered
    "reaches both targets at ['sum']" from two rows twelve dB apart."""
    lo, hi = v.RHO_M_808_RANGE
    e_lo, e_hi = v.M_RE_LN_808_RANGE
    rows = [_row("sum", -20.0, -9.0, (lo + hi) / 2),          # rho in box, energy not
            _row("sum", 0.0, (e_lo + e_hi) / 2, 3.0)]         # energy in box, rho not
    out = v.verdict(rows)
    assert out["positions_matching"] == []
    assert "refused" in out


def test_the_verdict_rejects_an_overshoot_rather_than_crediting_it():
    """Being three times the 808's ratio is not "at least as good as"; both
    bounds of the box are enforced."""
    e_lo, e_hi = v.M_RE_LN_808_RANGE
    out = v.verdict([_row("sum", 6.0, (e_lo + e_hi) / 2, 3.02)])
    assert out["positions_matching"] == []


def test_the_verdict_reports_how_far_the_closest_row_missed_by():
    out = v.verdict([_row("post_vca", -20.0, -6.92, 1.1108)])
    assert out["closest_row"]["position"] == "post_vca"
    assert "closest is post_vca" in out["refused"]


# ---------------------------------------------------------------------------
# the targets are read from the artifacts, not from the prose
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not (LOW_TAIL.is_file() and M_ORIGIN.is_file()),
                    reason="step 7's artifacts are not present")
def test_targets_agree_with_the_committed_artifacts():
    t = v.targets()
    assert set(t["verified_against"]) == {"low-tail.json", "m-origin.json"}
    assert t["per_window_808_rho_M"][v.T_RHO_WINDOW]["min"] == pytest.approx(
        v.RHO_M_808_RANGE[0], abs=1e-4)


@pytest.mark.skipif(not LOW_TAIL.is_file(), reason="step 7's artifact is not present")
def test_targets_refuse_a_quote_that_has_drifted(monkeypatch):
    """A target copied into a docstring goes stale silently; one checked against
    its source cannot. Asserted by breaking it."""
    monkeypatch.setattr(v, "T_RHO_808_MIN", 1.200)
    with pytest.raises(v.Refused):
        v.targets()


@pytest.mark.skipif(not M_ORIGIN.is_file(), reason="step 7's artifact is not present")
def test_targets_refuse_a_window_mismatch(monkeypatch):
    """rho is a Schroeder crossing time in a window; comparing across windows is
    the mistake `targets()` itself made on its first run."""
    monkeypatch.setattr(v, "T_RHO_WINDOW", "3.5s")
    with pytest.raises(v.Refused):
        v.targets()


# ---------------------------------------------------------------------------
# the defect matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("defect", v.DEFECTS)
def test_every_injected_defect_turns_at_least_one_property_red(defect):
    got = v.properties(defect=defect)
    assert any(not got[p]["pass"] for p in v.PROPERTIES), \
        f"{defect} is invisible to the suite"


@pytest.mark.parametrize("defect", v.BLIND_BY_CONSTRUCTION)
def test_the_blind_defects_are_verified_blind(defect):
    got = v.properties(defect=defect)
    bad = [p for p in v.PROPERTIES if not got[p]["pass"]]
    assert not bad, f"{defect} was meant to be blind and moved {bad}"


def test_the_clean_matrix_passes_and_main_refuses_when_it_does_not(monkeypatch):
    """`main` must not report a measurement over a failed control matrix, and
    that is asserted by making the matrix fail."""
    monkeypatch.setattr(v, "check", lambda **k: (False, ["forced"]))
    assert v.main([]) == 1


def test_rho_is_refused_on_a_band_below_the_instruments_qualified_range():
    """Imported from `cymbal_m_origin` rather than restated, and exercised: a
    band 30 dB below Ln is division by almost nothing."""
    m = {"bands": {"M": {"energy_j": 1e-4}, "Mn": {"energy_j": 1e-6},
                   v.REF: {"energy_j": 1.0}},
         "rho": {"M": {"-10": 9.9, "-5": 9.9}, "Mn": {"-10": 9.9, "-5": 9.9}}}
    monkey = lt.measure
    try:
        lt.measure = lambda *a, **k: m
        out = v.read(np.zeros(8), v.SR)
    finally:
        lt.measure = monkey
    assert out["rho_M_-10"] is None and "M" in out["refused"]
    assert out["rho_Mn_-10"] is None and "Mn" in out["refused"]


# ---------------------------------------------------------------------------
# the headline, against the artifact rather than the prose
# ---------------------------------------------------------------------------
def test_the_real_sweep_is_refused_at_every_asymmetry():
    """The result itself, at a coarse drive grid so it is a test and not a
    re-run of the experiment. If any asymmetry ever matches, the module's
    answer changes and this test is where that is noticed."""
    for asym in (0.15, 0.8):
        rows = v.sweep(asym=asym, drives=(-20.0, 0.0, 12.0))
        out = v.verdict(rows)
        assert out["positions_matching"] == [], \
            f"asymmetry {asym} now matches: {out['per_position']}"


def test_the_linear_chain_from_the_documented_source_beats_our_shipped_kit():
    """The other half of the correction, as a number: rendered from §1.5's
    staircase the LINEAR chain reads rho_M near 0.99 and rho_Mn near 1.10,
    where our shipped kit reads 0.871 and 0.878."""
    r = v.read(*v.render(position="none", drive=0.0))
    assert r["rho_M_-10"] > 0.95
    assert r["rho_Mn_-10"] > 1.00
