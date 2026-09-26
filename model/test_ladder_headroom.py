#!/usr/bin/env python3
"""Ground truth and injected controls for `model/ladder_headroom.py`.

The audit exists to answer "is there any tuning or precision headroom left in
the shipped ladder", and the only way that answer is worth anything is if the
apparatus can be shown to (a) agree with an independently-derived number where
one exists, and (b) go red when the defect it is looking for is injected. Both
are here, one section each, per `docs/verification-rules.md` rules 1 and 2.

    python3 -m pytest model/test_ladder_headroom.py -q      # ~40 s

What is ground truth for what, because "it agrees with itself" is the failure
this repository keeps re-finding:

  * the linearised loop is checked against `voice_fx.k_onset`, which DR 0006's
    ROM is built from -- not a restatement, a different caller of the same
    published derivation, and the audit REFUSES if they disagree
  * `cutoff_from_g` is checked by round-tripping the closed-form coefficient
    law, so the cents conversion cannot be a fit to itself
  * at a cutoff that lands exactly on a ROM entry the read error is checked
    against a closed form computed in the test -- the truncating shift is
    EXACTLY zero there and the remainder is that one entry's own rounding, a
    known answer that does not depend on the estimator
  * the predicted tracking is checked against `test_moog_acceptance.TRACKING`,
    which is MEASURED on the fixed-point filter and locked by DR 0011
  * the external row is read out of `docs/reference-compare-results.json`, the
    frozen reference profile, never re-rendered here
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

import fixed                                                        # noqa: E402
import ladder_headroom as lh                                        # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                              # noqa: E402
from dsp import SR                                                  # noqa: F401,E402


# =============================================================================
# 1. the apparatus, checked against something that is not itself
# =============================================================================
@pytest.mark.parametrize("cut", [30, 100, 800, 3000, 10000, 21600])
def test_the_linearised_loop_reproduces_voice_fx_k_onset(cut):
    """`lh.loop_onset` duplicates the bisection `voice_fx.k_onset` runs, so that
    the audit can evaluate the loop at an arbitrary FLOAT coefficient instead of
    only at a ROM entry. A duplicate is a drift risk, so it is pinned: same k,
    same frequency, to the last bit of a double."""
    rom = vf.make_g_rom()
    k_ref, f_ref = vf.k_onset(cut, rom)
    g = int(vf.g_from_cut(np.array([cut]), rom)[0])
    k, f = lh.loop_onset(g / 65536.0 * vf.tanh_bin0_slope())
    assert k == pytest.approx(k_ref, rel=1e-12)
    assert f == pytest.approx(f_ref, rel=1e-12)


def test_cutoff_from_g_inverts_the_shipped_coefficient_law():
    """Known answer, both directions: `g_exact_q16` is the law `fixed.py`
    computes and `cutoff_from_g` is its algebraic inverse, so composing them has
    to return the TUNED cutoff -- `f * CUT_TRIM * fcr(f)` -- and not the
    commanded one. Getting that distinction wrong would put the whole tuning
    correction into the audit's own error term."""
    cuts = np.array([30.0, 100.0, 1000.0, 10000.0, 21600.0])
    back = lh.cutoff_from_g(lh.g_exact_q16(cuts))
    assert np.allclose(back, fixed.tuned_cutoff(cuts), rtol=1e-12)
    # ... and it is NOT the commanded cutoff, at a level a rounding error cannot reach
    assert abs(back[1] / 100.0 - 1.0) > 0.005


def test_at_a_rom_entrys_own_cutoff_the_error_is_exactly_that_entrys_rounding():
    """**A known answer, derived independently of the estimator.** The ROM is
    EDGE-sampled at `i * (32768 >> GROM_BITS)` Hz, so at those cutoffs there is
    no fractional index: the truncating shift contributes EXACTLY zero, and the
    whole remaining error is the rounding of one Q0.16 word. Both are asserted
    against a value computed here from `round()` rather than read back from the
    module -- if the cents conversion or the entry grid were wrong this is where
    it would show, and every other number on the axis would be void.

    Half an LSB is worth up to 0.16 cents at the bottom of the range, which is
    also the floor the size sweep converges to, two decades below the shipped
    table's 29 cents."""
    step = (1 << 15) >> vf.GROM_BITS
    on_grid = np.array([i * step for i in range(2, 1 + (vf.CUT_MAX // step))])
    err = lh.rom_read_error_cents(on_grid)
    assert np.abs(err["trunc"]).max() == 0.0, float(np.abs(err["trunc"]).max())
    exact = lh.g_exact_q16(on_grid)
    want = 1200.0 * np.log2(lh.cutoff_from_g(np.round(exact)) / lh.cutoff_from_g(exact))
    assert np.allclose(err["interp"], want, atol=1e-9)
    assert np.abs(err["total"]).max() < 0.30, float(np.abs(err["total"]).max())


def test_the_rom_error_split_adds_up_to_the_total():
    """The split is an identity, not a fit: interpolation of the exact law,
    plus the chord deficit of the entries actually stored, plus the truncating
    shift `g_from_cut` performs, is the whole error. Asserted so a later change
    to one term cannot quietly stop accounting for the others."""
    cuts = np.arange(vf.CUT_MIN, 4000, 7)
    e = lh.rom_read_error_cents(cuts)
    assert np.allclose(e["interp"] + e["trunc"], e["total"], atol=1e-9)


def test_the_predicted_tracking_agrees_with_the_measured_table_dr_0011_locked():
    """The audit's dense sweep uses the LINEARISED loop, which is cheap; the
    thing anyone cares about is the fixed-point filter's measured ring. DR 0011
    locked six measured ratios in `model/test_moog_acceptance.TRACKING`, and the
    prediction has to reproduce them or the dense sweep is not a proxy for
    anything. The stated accuracy of the proxy is its own result and is
    reported by the audit: 0.31 pp at 10 kHz, better than 0.1 pp below 3 kHz."""
    import test_moog_acceptance as tma
    pred = lh.predicted_tracking(sorted(tma.TRACKING))
    err = {c: abs(pred[c] - tma.TRACKING[c]) for c in tma.TRACKING}
    assert max(err.values()) < 0.0035, err
    assert max(err[c] for c in (200, 400, 800, 3000)) < 0.0012, err


def test_the_frozen_surge_row_is_not_a_like_for_like_limit_cycle():
    """**The confound that decides how the external comparison may be read.**
    `docs/discrimination.md` section 8.4 sets our self-oscillation tracking
    against Surge Type 2's, and section 8.3 records that Surge Type 2 clamps
    resonance at 0.9925 and CANNOT self-oscillate. So the frozen Surge row is a
    DECAYING resonant ring and ours is a limit cycle, which is visible in the
    profile itself: the two differ by more than 30 dB of ring level at the same
    nominal maximum resonance. Read from the frozen profile, so this caveat
    cannot be argued about."""
    ours = lh.frozen_reference_tracking("ours")
    surge = lh.frozen_reference_tracking("surge-huov")
    assert 20.0 * math.log10(ours["rms"][0] / surge["rms"][0]) > 30.0
    # and the drift-spread figures section 8.4 quotes are what is in the file
    assert lh.tracking_summary(ours["ratio"])["spread_pp"] == pytest.approx(7.92, abs=0.05)
    assert lh.tracking_summary(surge["ratio"])["spread_pp"] == pytest.approx(0.62, abs=0.05)


# =============================================================================
# 2. the findings, locked
# =============================================================================
def test_dr_0011_removed_the_drift_and_left_a_resonance_dependent_offset():
    """**The audit's headline, and the reason it is a finding and not a
    restatement.** DR 0011 fitted `CUT_TRIM` at res = 1.05 and its own metric --
    the drift, how much the ratios differ from each other -- is blind to a
    uniform offset by construction, which that record says in as many words. At
    res = 1.05 the mean offset is under 0.2 %; at res = 2.00 it is about -5.3 %,
    while the drift barely moves. The offset is the larger error by an order of
    magnitude and no tuning POLYNOMIAL can remove it, because it is not a
    function of frequency."""
    tab = lh.resonance_offset_table(cuts=(100.0, 800.0, 6400.0), resonances=(1.05, 2.00))
    assert abs(tab[1.05]["mean_offset_pct"]) < 0.35, tab[1.05]
    assert tab[2.00]["mean_offset_pct"] < -4.0, tab[2.00]
    # the drift is nearly unchanged between the two: the offset is not drift
    assert abs(tab[2.00]["spread_pp"] - tab[1.05]["spread_pp"]) < 0.6, tab


def test_a_refitted_tuning_polynomial_recovers_most_of_the_remaining_drift():
    """**What is left for the tuning LAW, measured by inverting the loop.**
    `CUT_TRIM * fcr()` is the paper's cubic fitted to a different
    implementation; the ratio OUR loop actually needs is a slightly different
    cubic. Refitting the same shape -- same ROM shape, same one multiply at
    build time, no datapath change, i.e. exactly DR 0011's class of change --
    takes the worst ratio error from about 0.99 % to about 0.28 %.

    Reported as available-but-small: 0.7 pp is about 12 cents, against the 92
    cents of resonance-dependent offset above."""
    cuts = lh.REFIT_CUTS
    shipped = lh.tuning_ratio_error(cuts)
    assert 100.0 * np.abs(shipped).max() == pytest.approx(0.99, abs=0.10)
    _, worst3 = lh.refit_tuning(cuts, 3)
    _, worst4 = lh.refit_tuning(cuts, 4)
    assert 100.0 * worst3 == pytest.approx(0.28, abs=0.06)
    assert 100.0 * worst4 < 0.10
    assert worst3 < 0.5 * float(np.abs(shipped).max())


def test_more_coefficient_rom_entries_is_a_poor_buy_and_the_sweep_floors():
    """**Sweep the parameter before arguing about it** (`CLAUDE.md`). The
    interpolated ROM read is worst at the BOTTOM of the range, where `g` is a
    few hundred LSB: -29 cents at 30 Hz, -17 at 100 Hz, under a cent above
    1 kHz. Doubling the table to 257 entries buys only about 9 cents for 2048
    more ROM bits, and the sweep then FLOORS near 9 cents however many entries
    are added -- because what is left is the Q0.16 coefficient word itself, and
    widening that is a datapath change, not a ROM-build one."""
    sweep = lh.rom_size_sweep(range(5, 11))
    shipped = sweep[vf.GROM_BITS]
    assert abs(shipped["worst_cents"]) == pytest.approx(29.0, abs=1.5)
    gain = abs(shipped["worst_cents"]) - abs(sweep[vf.GROM_BITS + 1]["worst_cents"])
    assert 5.0 < gain < 12.0, gain
    # the floor: three doublings past the shipped table and it stops improving
    assert abs(sweep[10]["worst_cents"]) == pytest.approx(abs(sweep[9]["worst_cents"]), abs=0.2)
    assert abs(sweep[10]["worst_cents"]) > 8.0


def test_refitting_the_existing_129_entries_beats_a_1025_entry_table():
    """**The free win on this axis, and the reason it exists.** The ROM is
    EDGE-sampled, and linear interpolation between edge samples of a CONCAVE law
    always lands below it, so the read error is one-sided -- every entry is
    biased the same direction. Choosing the 129 stored words to minimise the
    interpolated error instead of to sample the law removes that bias for
    nothing: same 2064 ROM bits, same read, no datapath change.

    Worst error 29.0 -> 8.0 cents, which is better than the 9.2-cent floor a
    1025-entry table converges to. Measured, not shipped: moving the table moves
    DR 0006's k ROM, the contract revision and every bit-exact expectation, so
    it is a decision record and not an audit."""
    cuts = np.arange(vf.CUT_MIN, vf.CUT_MAX + 1)
    shipped = np.abs(lh.rom_read_error_cents(cuts)["total"]).max()
    refit = np.abs(lh.rom_read_error_cents(cuts, rom=lh.refit_rom_entries())["total"]).max()
    assert shipped == pytest.approx(29.0, abs=1.5)
    assert refit < 10.0, refit
    assert refit < abs(lh.rom_size_sweep((10,))[10]["worst_cents"])
    # the entries really are the same shape and width -- not a wider ROM in disguise
    entries = lh.refit_rom_entries()
    assert entries.shape == vf.make_g_rom().shape
    assert entries.min() >= 0 and entries.max() <= 65535


def test_the_state_width_has_no_precision_headroom_and_four_bits_of_margin():
    """**Numerical precision is not a lever here, measured rather than
    assumed.** Eight extra state bits move the measured tracking by under
    0.01 pp, and FOUR FEWER than the shipped 24/20 move it by under 0.02 pp. So
    the shipped width is past the plateau with margin, and no candidate ladder
    can be argued for on the ground that ours is arithmetic-limited."""
    rows = lh.state_width_sweep(((20, 16), (24, 20), (32, 28)), cuts=(100.0, 1600.0))
    ref = rows[(24, 20)]["spread_pp"]
    assert abs(rows[(32, 28)]["spread_pp"] - ref) < 0.010, rows
    assert abs(rows[(20, 16)]["spread_pp"] - ref) < 0.020, rows


def test_the_drive_stage_saturation_point_is_in_the_references_range():
    """**Drive, referred to the input** -- the level at which the third harmonic
    reaches -40 dB. `docs/discrimination.md` section 8.3 has ours at
    -6.6 dBFS, Arturia Mini V3 at -5.7 and Surge's RK model at -0.6, and reads
    our agreement with the dedicated Minimoog emulation as the evidence that the
    gain staging is in trim. Re-measured on the shipped filter it is about
    -8 dBFS: still within a few dB of Mini V3, so there is no drive-mapping
    headroom to recover and the axis is closed."""
    dbfs = lh.drive_saturation_dbfs()
    assert -11.0 < dbfs < -5.0, dbfs
    assert abs(dbfs - (-5.7)) < 4.0, dbfs          # Mini V3, the dedicated emulation


# =============================================================================
# 3. the suite can fail: injected defects, one per axis
# =============================================================================
def test_control_the_untuned_rom_turns_the_tuning_axis_red():
    """Defect: DR 0011 reverted -- `make_g_rom(tune=False)`. The drift the audit
    reports as nearly closed has to reopen. Without this, "1.1 pp of drift
    remains" is a number nobody has watched move."""
    untuned = vf.make_g_rom(tune=False)
    drift_now = lh.tracking_summary(lh.predicted_tracking(lh.REPORT_CUTS))["spread_pp"]
    drift_bad = lh.tracking_summary(
        lh.predicted_tracking(lh.REPORT_CUTS, rom=untuned))["spread_pp"]
    assert drift_now < 2.0, drift_now
    assert drift_bad > 6.0, drift_bad


def test_control_a_thirty_three_entry_rom_turns_the_coefficient_axis_red():
    """Defect: the cutoff ROM shrunk to 33 entries. The audit's verdict on that
    axis is "no cheap headroom", which is only meaningful if the metric can see
    a table that IS too small -- 90 cents against the shipped 29."""
    sweep = lh.rom_size_sweep((5, vf.GROM_BITS))
    assert abs(sweep[5]["worst_cents"]) > 3.0 * abs(sweep[vf.GROM_BITS]["worst_cents"])


def test_control_a_two_pole_cascade_turns_the_loop_prediction_red():
    """Defect: a pole dropped from the linearised loop. The loop prediction is
    the audit's cheap proxy for the measured ring, and a proxy that agrees with
    the measurement no matter what the loop is would not be one."""
    rom = vf.make_g_rom()
    g = int(vf.g_from_cut(np.array([800]), rom)[0]) / 65536.0 * vf.tanh_bin0_slope()
    _, f4 = lh.loop_onset(g)
    _, f2 = lh.loop_onset(g, stages=2)
    assert abs(1200.0 * math.log2(f2 / f4)) > 200.0, (f2, f4)


def test_the_audit_refuses_when_the_coefficient_path_is_not_the_shipped_one():
    """**REFUSED is a first-class outcome** (`CLAUDE.md`). The audit's every
    number is about the filter that ships, so it asserts that `voice_fx`'s ROM
    and `fixed.tuned_cutoff` still describe one filter, and refuses rather than
    reporting if they do not. Injected here by detuning the ROM under it."""
    with pytest.raises(lh.Refused):
        lh.assert_apparatus(rom=vf.make_g_rom(tune=False))


def test_the_rig_refuses_to_apply_the_tuning_polynomial_twice():
    """**The apparatus defect this audit found.** `reference_rigs.OurLadder`
    predates DR 0011: its `huov_fcr=True` device multiplied the cutoff by
    `fcr()` BEFORE the ROM lookup, when the shipped ROM did not carry the
    polynomial. It does now, so that device silently applies the correction
    twice -- and `reference_compare.py` still builds it as `ours-huovtune`. A
    wrong instrument in a wrong state is exactly the failure mode `CLAUDE.md`
    names, so the precondition is asserted at the point of use and the rig
    refuses."""
    with pytest.raises(AssertionError):
        rr.OurLadder("ours-huovtune", huov_fcr=True)
    # the shipped device is unaffected, and still the one under test
    assert rr.OurLadder("ours").huov_fcr is False


def test_the_rig_still_carries_the_papers_constant_not_surges_typo():
    """`OurLadder.fcr` documents that the quadratic term is the paper's 0.4955
    where `sst-filters` ships 0.4995, and `voice_fx` has to agree with it or the
    rig and the shipped ROM are two different filters. Guarded because a reader
    comparing against Surge's source will be tempted to 'fix' one of them."""
    assert vf.FCR_C2 == 0.4955
    assert rr.OurLadder.fcr(10000.0) == pytest.approx(float(vf.fcr(10000.0)), rel=1e-12)


# =============================================================================
# 4. the report
# =============================================================================
def test_the_audit_reports_every_axis_with_a_verdict_and_a_number():
    """A report that can omit an axis can omit the one that was red. Every axis
    carries a verdict drawn from a fixed vocabulary and the number that decided
    it, and the overall conclusion is derived from the axes rather than
    written."""
    rep = lh.audit(quick=True)
    assert set(rep["axes"]) == {"tuning-law", "cutoff-offset", "coefficient-rom",
                                "numerical-precision", "drive"}
    for name, ax in rep["axes"].items():
        assert ax["verdict"] in lh.VERDICTS, (name, ax["verdict"])
        assert isinstance(ax["deciding_number"], (int, float)), name
        assert ax["units"]
    worth = [n for n, a in rep["axes"].items() if a["verdict"] == "worth pursuing"]
    small = [n for n, a in rep["axes"].items() if a["verdict"] == "available but small"]
    closed = [n for n, a in rep["axes"].items()
              if a["verdict"] == "no further win available"]
    assert worth == ["cutoff-offset"], rep["axes"]
    assert sorted(small) == ["coefficient-rom", "tuning-law"], rep["axes"]
    assert sorted(closed) == ["drive", "numerical-precision"], rep["axes"]
    assert rep["conclusion"].startswith("Rung 1")
    assert json.dumps(rep)          # the report has to be serialisable to be evidence
