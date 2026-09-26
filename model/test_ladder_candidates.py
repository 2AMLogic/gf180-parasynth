#!/usr/bin/env python3
"""Ground truth and injected controls for the rungs-2-to-4 comparison.

    python3 -m pytest model/test_ladder_candidates.py -q      # ~2 min

`model/ladder_candidates.py` and `tools/compare_ladder_candidates.py` decide
whether any candidate ladder is worth changing to. That decision is worth
nothing unless the apparatus can be shown to (a) agree with an answer derived
somewhere other than here, and (b) go red when the defect it is looking for is
injected. One section each, per `docs/verification-rules.md` rules 1 and 2.

What is ground truth for what, because "it agrees with itself" is the failure
this repository keeps re-finding:

  * the **rung-4 reference** is checked against the ladder's closed-form
    transfer function -- a textbook expression this repository did not derive,
    written out in `ladder_candidates.analytic_response_db` in six lines that
    can be read against any DSP text. It is also checked for SECOND-ORDER
    convergence, which is the evidence that what is left really is
    discretisation and not an estimator artefact.
  * the **`tanh(w/2)` coefficient law** is checked as pure algebra, with no
    filter run at all: the trapezoidal pole `(1-G)/(1+G)` must equal `exp(-w)`.
  * the **peak locator** is checked against the analytic response it will be
    used to compare against, so a bias in the locator cannot be mistaken for a
    candidate's tuning error.
  * the **shipped core** is checked bit-exactly against `fixed.LadderFx`, so
    the baseline of the comparison is the filter that ships.
  * the **zero-order hold** carries its own control, because omitting it was a
    real 1.02 dB error in this harness before it was found.
"""
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import audio_measure as am                                          # noqa: E402
import fixed                                                        # noqa: E402
import ladder_candidates as lc                                      # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402

# Short windows: every reference run in this file is a convergence or
# calibration check, not a reported number, so it buys nothing from the long
# analysis window the report uses.
N, SETTLE = 4096, 2048
REF_OS = 16


# =============================================================================
# 1. the apparatus, checked against something that is not itself
# =============================================================================
def test_the_shipped_core_is_bit_exact_against_the_filter_that_ships():
    """`ShippedCore` transcribes `fixed.LadderFx.process` so the inner loop can
    count its own operations. A transcription is a drift risk, so it is pinned
    to the last bit over a driven buffer -- if it ever parts, the baseline of
    the whole rungs-2-to-4 comparison is a lookalike and not the filter."""
    rng = np.random.default_rng(11)
    xq = rng.integers(-24000, 24000, 4000).astype(np.int16)
    ref = fixed.LadderFx(**vf.LADDER_CFG)
    k, gain, ogain = ref.regs(1.6, 1.0)
    args = dict(g_q16=np.full(len(xq), 9000, dtype=np.int64),
                k=k, gain=gain, ogain=ogain)
    a = fixed.LadderFx(**vf.LADDER_CFG).process(xq, None, 1.6, 1.0, **args)
    b = lc.ShippedCore(**vf.LADDER_CFG).process(xq, None, 1.6, 1.0, **args)
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_the_candidate_device_reproduces_reference_rigs_ourladder():
    """The harness's own control path -- ROM read, DR 0006 compensation,
    stimulus, scaling -- is the one every other filter measurement in this
    repository used, not a second implementation of it."""
    ours = rr.OurLadder("ours").ring(800.0, 1.4, seconds=0.3)
    mine = lc.Candidate("shipped", tuned=True, compensated=True).ring(800.0, 1.4, seconds=0.3)
    assert len(ours) == len(mine)
    assert np.allclose(ours, mine, atol=1e-12)


def test_the_expo_law_rom_is_the_shipped_untuned_rom_byte_for_byte():
    """`make_coef_rom` fills the candidates' table. For the shipped law it must
    BE the shipped table, or the candidates are not being compared under the
    same coefficient quantisation the issue requires."""
    assert np.array_equal(lc.make_coef_rom("expo", tune=False),
                          vf.make_g_rom(tune=False))
    assert np.array_equal(lc.make_coef_rom("expo", tune=True), vf.make_g_rom(tune=True))


@pytest.mark.parametrize("cut", [30.0, 400.0, 6400.0, 21600.0])
def test_the_tanh_half_law_places_the_trapezoidal_pole_exactly(cut):
    """Pure algebra, no filter run: the trapezoidal one-pole's pole is
    `(1-G)/(1+G)`, and the exact continuous pole is `exp(-w)`. `G = tanh(w/2)`
    is the unique choice that makes them equal, which is why the implicit
    candidates get that law and not the shipped `1 - exp(-w)`.

    This is what makes the candidate a fair one rather than a strawman, so it
    is asserted rather than asserted-in-a-comment."""
    w = 2.0 * math.pi * cut / lc.FS_OS
    g = math.tanh(0.5 * w)
    assert math.isclose((1.0 - g) / (1.0 + g), math.exp(-w), rel_tol=1e-12)
    # and it fits the Q0.16 word at every cutoff, which backward Euler does not
    assert 0.0 < g < 1.0
    assert math.exp(w) - 1.0 > 1.0 if cut > 15000 else True


@pytest.mark.parametrize("cut", [400.0, 6400.0])
def test_the_rung4_reference_reproduces_the_closed_form(cut):
    """THE calibration signal. The rung-4 model is the large-signal yardstick
    every candidate is compared against, and its own credibility comes from
    agreeing with an expression nobody here derived."""
    fr = lc.snap_freqs(np.array([0.5, 0.8, 1.0, 1.25, 2.0]) * cut, N)
    err = lc.reference_response_db(cut, 0.9, fr, oversample=REF_OS, n=N, settle=SETTLE) \
        - lc.analytic_response_db(cut, 0.9, fr)
    assert np.abs(err).max() < 0.25, dict(zip(fr.round(1), err.round(3)))


def test_the_rung4_reference_converges_at_second_order():
    """Quadrupling the oversampling must cut the error by about sixteen. A
    trapezoidal rule that does not is not integrating what it claims to, and an
    error that does NOT shrink is not discretisation at all -- which is exactly
    how the zero-order-hold bug below was found."""
    cut = 6400.0
    fr = lc.snap_freqs(np.array([0.8, 1.0, 1.25, 2.0]) * cut, N)
    ana = lc.analytic_response_db(cut, 0.9, fr)
    e4 = np.abs(lc.reference_response_db(cut, 0.9, fr, oversample=4, n=N,
                                         settle=SETTLE) - ana).max()
    e16 = np.abs(lc.reference_response_db(cut, 0.9, fr, oversample=16, n=N,
                                          settle=SETTLE) - ana).max()
    assert e16 < e4 / 8.0, (e4, e16)


def test_control_omitting_the_zero_order_hold_is_a_one_db_error():
    """A recorded wrong-then-right, kept as a control rather than deleted.

    Every model here holds each input sample for `1/48000` s, so its input is a
    staircase and really does lose `sin(x)/x`. The closed form was written
    without that term, and the residual read as a 1.02 dB "discretisation
    error" at a 6.4 kHz cutoff which did not shrink when the reference's
    oversampling was doubled. This asserts the term is worth what it was worth,
    so it cannot be quietly dropped again."""
    f = np.array([12800.0])
    with_zoh = lc.analytic_response_db(6400.0, 0.9, f)
    without = lc.analytic_response_db(6400.0, 0.9, f, zoh=False)
    assert 0.9 < float(without[0] - with_zoh[0]) < 1.2


def test_the_peak_locator_recovers_the_analytic_peak_it_will_be_compared_to():
    """Ground truth for the estimator itself: hand `peak_of` the CLOSED FORM
    sampled on the probe grid and it must return the closed form's own peak. A
    bias here would be read as every candidate's tuning error."""
    for cut, res in ((400.0, 0.9), (1600.0, 0.5), (6400.0, 0.9)):
        grid = np.unique(lc.snap_freqs(
            cut * np.geomspace(*lc.PEAK_SPAN, lc.PEAK_POINTS)))
        f_hat, g_hat = lc.peak_of(grid, lc.analytic_response_db(cut, res, grid))
        dense = np.geomspace(*lc.PEAK_SPAN, 4001) * cut
        a = lc.analytic_response_db(cut, res, dense)
        j = int(np.argmax(a))
        assert abs(1200.0 * math.log2(f_hat / dense[j])) < 3.0, (cut, res, f_hat, dense[j])
        assert abs(g_hat - a[j]) < 0.05


def test_the_instrumented_cost_is_the_shape_each_core_declares():
    """`tanh` and divide counts are counted by the inner loop; the report's
    clock estimate is built on them. Pin them so a core that changes shape
    cannot keep a stale cost, and so the claim "the whole difference is
    divides" stays checkable."""
    x = np.zeros(200)
    x[0] = 20000.0
    want = {"shipped": (5.0, 0.0), "zdf-newton-2": (10.0, 8.0),
            "zdf-newton-3": (15.0, 12.0), "zdf-explicit": (10.0, 1.0)}
    for name, (tanh, div) in want.items():
        cand = lc.Candidate(name)
        cand.render(x, 1600.0, 1.2)
        c = cand.last_cost
        assert math.isclose(c["tanh"], tanh), (name, c)
        assert math.isclose(c["divide"], div), (name, c)
        assert c["multiply"] == lc.CORES[name][0](**vf.LADDER_CFG).MULS


def test_the_delay_free_explicit_ladder_cannot_oscillate_above_a_known_cutoff():
    """The closed form FIRST, then the measurement -- in that order, because a
    prediction that is only made after the data is not a prediction.

    A forward Euler one-pole's maximum phase lag is `arcsin(1 - g)`, so four of
    them reach -180 degrees only while `1 - g >= sin(45 deg)`, i.e. below

        f = -fs_os / (2 pi) * ln(sin(45 deg)) = 5295.6 Hz

    Above that the delay-free explicit ladder CANNOT self-oscillate at any
    feedback. The half-sample delay the shipped filter carries is what supplies
    the missing phase, which makes it load-bearing rather than an
    approximation (DR 0017)."""
    limit = lc.delay_free_oscillation_limit_hz("expo")
    assert 5295.0 < limit < 5296.0, limit
    assert math.isinf(lc.delay_free_oscillation_limit_hz("tanh-half"))
    assert lc.stage_max_lag_deg("expo", 6400.0) < 45.0
    assert lc.stage_max_lag_deg("expo", 3200.0) > 45.0

    import compare_ladder_candidates as cc
    below = cc.stage_resonance(["zdf-explicit"], (3200.0,), (1.45,))["zdf-explicit"]
    above = cc.stage_resonance(["zdf-explicit"], (6400.0,), (1.45,))["zdf-explicit"]
    assert below["sings_everywhere"], below
    assert not above["sings_everywhere"], above


def test_the_implicit_candidate_holds_the_resonant_peak_where_the_shipped_one_loses_it():
    """The one place a candidate beats the shipped filter, pinned so it cannot
    quietly stop being true: at a 6.4 kHz cutoff and res 0.9 the trapezoidal
    implicit form reproduces the closed form's resonant peak to a fraction of a
    dB, where the shipped explicit form is several dB short."""
    import compare_ladder_candidates as cc
    r = cc.stage_linear(["shipped", "zdf-newton-2"], (6400.0,), (0.9,))
    ours = abs(r["shipped"]["points"]["6400/0.9"]["peak_db"])
    theirs = abs(r["zdf-newton-2"]["points"]["6400/0.9"]["peak_db"])
    assert ours > 4.0, ours
    assert theirs < 1.0, theirs


def test_a_third_newton_iteration_buys_nothing_over_a_second():
    """DR 0001's reversal condition says "2- or 3-iteration". They are the same
    filter here to within this harness's resolution, so the answer to "which"
    is 2 -- and the extra 4 divides and 5 `tanh` of the third iteration are
    spent for nothing."""
    import compare_ladder_candidates as cc
    r = cc.stage_linear(["zdf-newton-2", "zdf-newton-3"], (6400.0,), (0.9,))
    a = r["zdf-newton-2"]["points"]["6400/0.9"]
    b = r["zdf-newton-3"]["points"]["6400/0.9"]
    assert abs(a["cents"] - b["cents"]) < 5.0, (a, b)
    assert abs(a["peak_db"] - b["peak_db"]) < 0.5, (a, b)


# =============================================================================
# 2. start red: a core with the right ports and no behaviour
# =============================================================================
def test_start_red_the_null_core_is_silent():
    y = lc.Candidate("null").ring(800.0, 1.45)
    assert am.is_silent(y)
    assert "null" not in lc.SHIPPABLE


@pytest.mark.parametrize("stage", ["linear", "resonance", "bass", "drive"])
def test_start_red_every_dimension_refuses_or_condemns_the_null_core(stage):
    """A stage that returns a plausible number for silence is not a
    measurement. Four harnesses in this repository shipped in exactly that
    state, which is why this runs before any candidate is believed."""
    import compare_ladder_candidates as cc
    if stage == "linear":
        r = cc.stage_linear(["null"], (400.0,), (0.9,))["null"]
        assert r["refused"] == 1
    elif stage == "resonance":
        r = cc.stage_resonance(["null"], (400.0,), (1.45,))["null"]
        assert not r["sings_everywhere"] and r["sings_fraction"] == 0.0
    elif stage == "bass":
        r = cc.stage_bass(["null"])["null"]
        assert "refused" in r
    else:
        r = cc.stage_drive(["null"])["null"]
        assert r["saturation_dbfs"] is None and r["chord_rms"] == 0.0


# =============================================================================
# 3. injected controls: each must turn its own dimension red
# =============================================================================
def test_control_a_dropped_pole_turns_the_linear_stage_red():
    """Three stages instead of four. The resonant peak moves a long way and the
    slope changes; a dimension that cannot see this cannot see an algorithm
    change either."""
    import compare_ladder_candidates as cc
    cuts, res = (1600.0,), (0.9,)
    ok = cc.stage_linear(["shipped"], cuts, res)["shipped"]
    bad = cc.stage_linear(["shipped"], cuts, res, stages=3)["shipped"]
    assert abs(bad["worst_peak_db"] - ok["worst_peak_db"]) > 3.0, (ok, bad)


def test_control_a_cutoff_skew_turns_the_tuning_axis_red():
    """A 6 % cutoff skew is about 100 cents, and the tuning axis must read it
    as about 100 cents -- not merely as "different"."""
    import compare_ladder_candidates as cc
    cuts, res = (1600.0,), (0.9,)
    ok = cc.stage_linear(["shipped"], cuts, res)["shipped"]["points"]["1600/0.9"]["cents"]
    bad = cc.stage_linear(["shipped"], cuts, res,
                          cut_skew=1.06)["shipped"]["points"]["1600/0.9"]["cents"]
    moved = bad - ok
    want = 1200.0 * math.log2(1.06)
    assert abs(moved - want) < 25.0, (ok, bad, want)


def test_control_a_four_entry_tanh_turns_the_drive_axis_red():
    """A deliberately crude nonlinearity. The saturation point must move; if it
    does not, the drive dimension is not measuring the nonlinearity."""
    import compare_ladder_candidates as cc
    ok = cc.stage_drive(["shipped"])["shipped"]
    bad = cc.stage_drive(["shipped"], cfg=dict(tanh_entries=4))["shipped"]
    assert ok["saturation_dbfs"] is not None
    assert (bad["saturation_dbfs"] is None
            or abs(bad["saturation_dbfs"] - ok["saturation_dbfs"]) > 1.0), (ok, bad)


def test_control_a_coarse_control_quantum_turns_the_movement_axis_red():
    """`model/reference_movement.py`'s own control: round the commanded cutoff
    to 32 Hz and the differential against the float control path must rise."""
    import compare_ladder_candidates as cc
    ok = cc.stage_movement(["shipped"])["shipped"]["worst_residual_db"]
    bad = cc.stage_movement(["shipped"], quantum=32)["shipped"]["worst_residual_db"]
    assert bad - ok > 10.0, (ok, bad)


# =============================================================================
# 4. preconditions: REFUSED is a first-class outcome
# =============================================================================
def test_the_harness_refuses_dr_0006_compensation_on_a_foreign_loop():
    """DR 0006's `k` ROM is bisected on the SHIPPED loop. Applying it to a
    candidate with a different loop would hide the very property the candidate
    is judged on -- how much compensation its own structure needs -- so it
    refuses instead of measuring it."""
    with pytest.raises(lc.Refused):
        lc.Candidate("zdf-newton-2", compensated=True)
    lc.Candidate("shipped", compensated=True)          # the one case it is valid


def test_the_harness_refuses_dr_0011_tuning_on_a_law_it_was_not_fitted_to():
    """`CUT_TRIM * fcr()` is one constant fitted to the shipped `expo` law at
    res = 1.05. A candidate on a different law has no such constant, and
    quietly applying ours would compare tuning corrections, not algorithms."""
    with pytest.raises(lc.Refused):
        lc.Candidate("zdf-newton-2", tuned=True)


def test_assert_apparatus_refuses_a_tanh_table_that_is_not_the_shipped_sixteen(monkeypatch):
    """DR 0006's `k_comp` ROM is derived from the table's bin-0 slope and issue
    #46 puts the table out of scope, so a changed table invalidates every
    number here rather than merely shifting one."""
    monkeypatch.setitem(vf.LADDER_CFG, "tanh_entries", 128)
    with pytest.raises(lc.Refused):
        lc.assert_apparatus(quick=True)


def test_the_peak_probe_refuses_a_peak_it_cannot_bracket():
    """A peak at the edge of the search window is not located, it is guessed.
    `REFUSED` is distinct from a bad score."""
    grid = np.array([100.0, 110.0, 120.0])
    with pytest.raises(lc.Refused):
        lc.peak_of(grid, np.array([3.0, 2.0, 1.0]))
    with pytest.raises(lc.Refused):
        lc.peak_of(grid, np.array([1.0, 1.0, 1.0]))


def test_a_divide_by_zero_in_a_solver_refuses_rather_than_crashes():
    with pytest.raises(lc.Refused):
        lc._div(1, 0)
    assert lc._div(-7, 2) == -3 and lc._div(7, 2) == 3        # toward zero, not floor
