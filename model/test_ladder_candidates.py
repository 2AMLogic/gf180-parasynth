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
import json
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
from dsp import SR                                                  # noqa: E402

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
    bias here would be read as every candidate's tuning error.

    **The grid is centred on the ANALYTIC PEAK, not on the cutoff, because that
    is what `peak_probe` does.** An earlier version of this test centred it on
    the cutoff and refused at `1600/0.5` -- the ladder's peak sits at 0.819 x
    cutoff there, outside `PEAK_SPAN`, which is the exact failure
    `analytic_peak`'s docstring records having already been fixed in the
    apparatus. Testing a window the apparatus does not use measures nothing the
    apparatus does. Measured by `model/probe_rungs_2_4_gaps.py` probe 1."""
    for cut, res in ((400.0, 0.9), (1600.0, 0.5), (6400.0, 0.9), (1600.0, 0.9)):
        f_a, g_a = lc.analytic_peak(cut, res)
        grid = np.unique(lc.snap_freqs(
            f_a * np.geomspace(*lc.PEAK_SPAN, lc.PEAK_POINTS)))
        f_hat, g_hat = lc.peak_of(grid, lc.analytic_response_db(cut, res, grid))
        assert abs(1200.0 * math.log2(f_hat / f_a)) < 3.0, (cut, res, f_hat, f_a)
        assert abs(g_hat - g_a) < 0.05, (cut, res, g_hat, g_a)


def test_the_instrumented_cost_is_the_shape_each_core_declares():
    """`tanh` and divide counts are counted by the inner loop; the report's
    clock estimate is built on them. Pin them so a core that changes shape
    cannot keep a stale cost, and so the claim "the whole difference is
    divides" stays checkable.

    **Each core is instantiated with the kwargs `CORES` carries**, because
    `NewtonCore.MULS` is set in `__init__` from `iters` and is not a class
    constant. Instantiating without them compared the 3-iteration core's
    instrumented 60 multiplies against the 2-iteration default's declared 43 --
    a stale cost of exactly the kind this test exists to catch, caught here
    against the declaration rather than in the report. Measured by
    `model/probe_rungs_2_4_gaps.py` probe 2."""
    x = np.zeros(200)
    x[0] = 20000.0
    want = {"shipped": (5.0, 0.0, 6), "zdf-newton-2": (10.0, 8.0, 43),
            "zdf-newton-3": (15.0, 12.0, 60), "zdf-explicit": (10.0, 1.0, 25)}
    for name, (tanh, div, muls) in want.items():
        cand = lc.Candidate(name)
        cand.render(x, 1600.0, 1.2)
        c = cand.last_cost
        assert math.isclose(c["tanh"], tanh), (name, c)
        assert math.isclose(c["divide"], div), (name, c)
        cls, kw = lc.CORES[name]
        assert c["multiply"] == cls(**vf.LADDER_CFG, **kw).MULS == muls, (name, c)


def test_the_divider_latency_the_budget_allows_is_exact_not_read_off_the_grid():
    """DR 0001's reversal turns on one number -- how fast a divider has to be
    for the 2-iteration solve to fit 256 clocks -- and that number must be
    solved for, not read off the swept grid.

    Cost is affine in the divider latency, so the threshold is arithmetic:

        zdf-newton-2   20 tanh x 2 + 86 mult + 3 coefficient-update + 16 d
                     = 129 + 16 d <= 256   ->   d <= 7

    **DR 0017 first said "<= 8 clocks", because 8 was the grid point below 17.**
    At exactly 8 it is 257 clocks against a 256 budget and misses by one. This
    test exists because that is the single number the whole reversal condition
    hangs on, and it was wrong the first time it was written down."""
    import compare_ladder_candidates as cc
    cost = cc.stage_cost(["shipped", "zdf-newton-2", "zdf-newton-3",
                          "zdf-explicit"])

    assert cost["shipped"]["per_sample"]["divide"] == 0.0
    assert math.isinf(cost["shipped"]["max_divider_latency_that_fits"])

    n2 = cost["zdf-newton-2"]
    assert n2["clocks_without_divider"] == 129.0, n2
    assert n2["per_sample"]["divide"] == 16.0, n2
    assert n2["max_divider_latency_that_fits"] == 7, n2
    # the off-by-one itself, pinned from both sides
    assert n2["clocks_by_divider_latency"][8] == 257.0, n2
    assert n2["fits_budget"][8] is False, n2
    assert 129.0 + 16.0 * 7 <= cc.CLOCKS_PER_SAMPLE < 129.0 + 16.0 * 8

    # and the threshold really is per-candidate, not one number for all of them
    assert cost["zdf-newton-3"]["max_divider_latency_that_fits"] == 3, cost
    assert cost["zdf-explicit"]["max_divider_latency_that_fits"] >= 17, cost


def test_the_delay_free_explicit_ladder_cannot_oscillate_above_a_known_cutoff():
    """The closed form FIRST, then the measurement -- in that order, because a
    prediction that is only made after the data is not a prediction.

    A forward Euler one-pole's maximum phase lag is `arcsin(1 - g)`, so four of
    them reach -180 degrees only while `1 - g >= sin(45 deg)`, i.e. below

        f = -fs_os / (2 pi) * ln(sin(45 deg)) = 5295.6 Hz

    Above that the delay-free explicit ladder CANNOT self-oscillate at any
    feedback. The half-sample delay the shipped filter carries is what supplies
    the missing phase, which makes it load-bearing rather than an
    approximation (DR 0017).

    **AND THE CLOSED FORM IS A CEILING, NOT THE CEILING A PLAYER MEETS.** This
    test asserted the converse -- that below 5295.6 Hz the filter does sing --
    and that is not what the closed form says and not what happens. The bound
    is on the PHASE: below it a -180 crossing exists, but as the stage lag
    falls towards 45 degrees the crossing retreats into a band where the
    cascade's own magnitude is tiny, so the feedback needed to close the loop
    runs away. Swept (`model/probe_rungs_2_4_gaps.py` probe 3, resonance to
    4.0, well past the instrument's range):

        zdf-explicit   sings from res 1.45 at 800/1600 Hz, from res 2.00 at
                       3200/3600/4000 Hz, and NOT AT ALL at 4800 Hz -- which is
                       below the closed-form ceiling -- or at 6400 Hz
        shipped        sings at every one of those cutoffs, res 1.05 to 1.45

    So the delay-free ladder's usable ceiling is between 4000 and 4800 Hz, the
    closed form's 5295.6 Hz is a loose upper bound on it, and the contrast with
    the shipped filter at the same cutoffs is the measurement DR 0017 rests
    on."""
    limit = lc.delay_free_oscillation_limit_hz("expo")
    assert 5295.0 < limit < 5296.0, limit
    assert math.isinf(lc.delay_free_oscillation_limit_hz("tanh-half"))
    assert lc.stage_max_lag_deg("expo", 6400.0) < 45.0
    assert lc.stage_max_lag_deg("expo", 3200.0) > 45.0

    import compare_ladder_candidates as cc
    hard = (1.45, 2.00, 4.00)          # 4.0 is past anything the knob commands

    # below the ceiling and below the measured one: it sings.
    below = cc.stage_resonance(["zdf-explicit"], (3200.0,), hard)["zdf-explicit"]
    assert below["sings_fraction"] > 0.0, below

    # above the closed-form ceiling: no feedback closes the loop ...
    above = cc.stage_resonance(["zdf-explicit"], (6400.0,), hard)["zdf-explicit"]
    assert above["sings_fraction"] == 0.0, above

    # ... and neither does it BELOW the ceiling at 4800 Hz. The closed form is
    # necessary, not sufficient; this is the assertion the earlier version of
    # this test had backwards.
    under = cc.stage_resonance(["zdf-explicit"], (4800.0,), hard)["zdf-explicit"]
    assert 4800.0 < limit
    assert under["sings_fraction"] == 0.0, under

    # the contrast that makes the half-sample delay load-bearing rather than
    # merely different: the shipped filter sings at BOTH of those cutoffs.
    ours = cc.stage_resonance(["shipped"], (4800.0, 6400.0), (1.45,))["shipped"]
    assert ours["sings_everywhere"], ours


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


@pytest.mark.parametrize("stage", ["linear", "resonance", "bass", "drive",
                                   "movement", "cleanliness"])
def test_start_red_every_dimension_refuses_or_condemns_the_null_core(stage):
    """A stage that returns a plausible number for silence is not a
    measurement. Four harnesses in this repository shipped in exactly that
    state, which is why this runs before any candidate is believed.

    **All six dimensions, not the four that were easy.** `movement` and
    `cleanliness` were outside this parametrisation while the report's own
    start-red section covered them, so the two stages whose estimators are
    most likely to return a number for silence were the two not pinned here."""
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
    elif stage == "movement":
        r = cc.stage_movement(["null"])["null"]
        assert r["refused"] == len(r["sweeps"]) > 0, r
    elif stage == "cleanliness":
        # NOTE THE ASYMMETRY, IT IS THE POINT. `decays_to_silence` is a
        # sub-axis on which SILENCE SCORES BEST -- the null core "passes" it
        # and every real candidate "fails" it at the Q15 floor. So it can
        # never condemn anything on its own, and what condemns silence here is
        # the aliasing estimator refusing outright. Read together, never apart.
        r = cc.stage_cleanliness(["null"])["null"]
        assert r["alias_db"] is None and r["alias_refused"] == "silent", r
        assert r["decay_tail_rms"] == 0.0 and r["decays_to_silence"] is True, r
    else:
        r = cc.stage_drive(["null"])["null"]
        assert r["saturation_dbfs"] is None and r["chord_rms"] == 0.0


def test_the_aliasing_probe_frequency_is_one_its_estimator_can_attribute():
    """`foldback_alias_db` finds aliases at the PREDICTED image of each
    harmonic above Nyquist, so a probe frequency that divides the sample rate
    folds every image back onto a real harmonic and nothing is attributable.

    48000 / 2000 = 24 exactly. `CLEAN_F0` was 2000 Hz, the estimator refused
    for every candidate, and the report printed `alias_db=None` -- the
    aliasing half of the cleanliness dimension unmeasured and looking like a
    measurement. This test asserts the precondition at the point of use."""
    import compare_ladder_candidates as cc
    t = np.arange(int(0.5 * SR)) / SR
    hot = np.tanh(6.0 * np.sin(2.0 * math.pi * cc.CLEAN_F0 * t))
    good = am.foldback_alias_db(hot, cc.CLEAN_F0, SR)
    assert good.ok, (cc.CLEAN_F0, good.reason, good.detail)
    assert good.detail["collided"] == 0, good.detail
    assert good.detail["images"] >= 50, good.detail

    # and the degenerate frequency it replaced still refuses, so the test is
    # known to be able to fail
    bad_f0 = 2000.0
    bad = am.foldback_alias_db(np.tanh(6.0 * np.sin(2.0 * math.pi * bad_f0 * t)),
                               bad_f0, SR)
    assert not bad.ok and bad.detail["images"] == 0, bad.detail
    assert SR % cc.CLEAN_F0 != 0


# =============================================================================
# 3. injected controls: each must turn its own dimension red
# =============================================================================
def test_control_a_dropped_pole_turns_the_linear_stage_red():
    """Three stages instead of four. A dimension that cannot see this cannot
    see an algorithm change either.

    **It goes red by REFUSING, at every point, and that is the stronger
    outcome.** This control was written expecting a moved dB, and what happens
    is that three poles push the resonant peak clean out of the four-pole
    search window, so `peak_of` reports an unbracketed maximum instead of a
    number (`model/probe_rungs_2_4_gaps.py` probe 4 -- 6 of 6 points, every one
    at the TOP edge of the bracket: 390 Hz for a 400/0.5 window ending at
    390 Hz, 1854 for 1600/0.9, 7415 for 6400/0.9). `REFUSED` is a first-class
    outcome here and it is not a weaker signal than a large number; it is the
    apparatus declining to report a peak it cannot support, which is exactly
    what it should do with a filter that is not the one the closed form
    describes.

    So the assertion is unanimity and direction, not magnitude -- and it is
    paired with the four-pole run scoring every one of the same points, so the
    refusal cannot be the stage simply being unable to measure anything."""
    import compare_ladder_candidates as cc
    cuts, res = (400.0, 1600.0, 6400.0), (0.5, 0.9)
    ok = cc.stage_linear(["shipped"], cuts, res)["shipped"]
    bad = cc.stage_linear(["shipped"], cuts, res, stages=3)["shipped"]

    n = len(cuts) * len(res)
    assert ok["refused"] == 0 and len(ok["points"]) == n, ok
    assert bad["refused"] == n, bad
    assert all("not bracketed" in v["refused"] for v in bad["points"].values()), bad

    # direction: the dropped pole moves the peak UP, onto the TOP grid point of
    # a window the correct filter sits comfortably inside. Rebuild the window
    # exactly as `peak_probe` does so this compares grid point to grid point.
    for key, v in bad["points"].items():
        cut, r = (float(s) for s in key.split("/"))
        f_a, _ = lc.analytic_peak(cut, r)
        grid = np.unique(lc.snap_freqs(
            f_a * np.geomspace(*lc.PEAK_SPAN, lc.PEAK_POINTS)))
        found = float(v["refused"].split("(")[1].split()[0])
        assert round(found) == round(float(grid[-1])), (key, found, grid[-1])


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


# =============================================================================
# 5. the committed report: the artefact `docs/ladder-rungs-2-4.md` reads from
# =============================================================================
DIMENSIONS = ("bass", "drive", "movement", "resonance", "cleanliness", "cost")
SCORED = ("linear",) + DIMENSIONS


def test_the_committed_report_scores_every_candidate_on_every_dimension():
    """Issue #46's acceptance bar in one assertion: every candidate scored on
    all six named dimensions with NUMERIC results, not prose impressions --
    checked against the committed `docs/ladder-rungs-2-4-results.json`, which
    is the artefact the document's tables are read from.

    This is a shape-and-freshness test, not a re-measurement: it is cheap
    enough to live in `make verify`, where a twenty-minute report run is not.
    What it catches is the report drifting out from under the document -- a
    candidate added to `CORES` and never scored, a dimension quietly dropped,
    a score that went `None` because its estimator started refusing."""
    path = os.path.join(HERE, "..", "docs", "ladder-rungs-2-4-results.json")
    assert os.path.exists(path), (
        f"{path} is missing; regenerate with\n"
        "  python3 tools/compare_ladder_candidates.py "
        "--json docs/ladder-rungs-2-4-results.json")
    with open(path) as fh:
        rep = json.load(fh)

    assert rep["quick"] is False, "the committed report must be a full run"
    names = list(lc.SHIPPABLE)
    assert set(rep["candidates"]) == set(names), (sorted(rep["candidates"]), names)

    # every dimension carries every candidate ...
    for dim in SCORED:
        assert set(rep[dim]) == set(names), (dim, sorted(rep[dim]))

    # ... and the summary each candidate is judged on is numeric throughout.
    for n in names:
        c = rep["candidates"][n]
        for key in ("worst_cents", "worst_peak_db", "worst_pass_db"):
            assert isinstance(c["matches"][key], (int, float)), (n, key)
            assert not math.isnan(c["matches"][key]), (n, key)
        for key in ("bass_weight_db", "compression_db_per_db", "saturation_dbfs"):
            assert isinstance(c["bigger"][key], (int, float)), (n, key)
        assert isinstance(c["bigger"]["sings_everywhere"], bool), n
        for key in ("tanh", "divide", "multiply"):
            assert isinstance(c["cost"][key], (int, float)), (n, key)
        assert c["cost"]["max_divider_latency_that_fits"] is not None, n
        # coefficient-update cost under MOVING controls, per issue #46
        assert rep["movement"][n]["coefficient_update_mults_per_sample"] == 3, n
        assert rep["movement"][n]["inner_loop_cost_moves"] is False, n

    # the controls ran in the same report as the result they guard
    red = rep["controls"]["start_red"]
    assert set(red) >= set(("linear", "resonance", "bass", "drive", "movement",
                            "cleanliness")), sorted(red)
    injected = rep["controls"]["injected"]
    assert set(injected) == {"dropped-pole", "cutoff-skew-6pct", "tanh-4-entries",
                             "control-quantum-32hz"}, sorted(injected)
    for name, v in injected.items():
        moved = [vv for kk, vv in v.items() if kk.startswith("moved")]
        assert moved, (name, v)

    # the verdict is derived from the numbers, not written into the file
    assert rep["verdict"]["retain"] is (not rep["verdict"]["winners"])
