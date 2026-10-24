"""Qualification of the BD pedestal instrument and the deadband mechanism (#220).

Part 1 qualifies `bd_pedestal.tail_pedestal` on signals constructed WITHOUT the
bank (known offsets, zero-offset decaying sinusoids, limit cycles, and a
legitimate long decay that defeats a fixed-window mean), and carries the
historical estimator as a named control that FAILS the same cases.

Part 2 is the mechanism: the analytic fixed-point prediction from the
recurrence, tested on the real `ModalFx` at the BD pole and at predeclared
confirmation poles / state sizes, with floor vs rounding as the intervention.

Run only this file and `test_bd_excitation_probe.py` on a shared host; the
whole-suite / `make verify` runs belong on the build box.
"""
from __future__ import annotations
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bd_pedestal as bp                                            # noqa: E402
import drums_fx as dx                                               # noqa: E402

SR = dx.SR
T = np.arange(int(1.5 * SR)) / SR


def sinus(hz=49.4, tau=0.14, amp=1.0e5, c=0.0, phase=0.3):
    return c + amp * np.exp(-T / tau) * np.sin(2 * math.pi * hz * T + phase)


# ------------------------------------------------ part 1: the instrument -----
@pytest.mark.parametrize("c", [-23893.0, -80.0, -1.0, 0.5, 700.0])
def test_known_constant_offsets_are_recovered(c):
    r = bp.tail_pedestal(sinus(c=c, tau=0.05))   # a finished decay (the 0.14 s one still rings at 19 LSB)
    assert r["status"] == "OK", r["reason"]
    assert r["pedestal"] == pytest.approx(c, rel=1e-3, abs=1e-3)


@pytest.mark.parametrize("c", [-80.0, 5.0])
def test_a_stationary_small_limit_cycle_leaves_the_offset_and_is_reported(c):
    x = c + 0.5 * abs(c) * np.sin(2 * math.pi * 61.3 * T)     # ripple = 50 % of the pedestal
    r = bp.tail_pedestal(x)
    assert r["status"] == "OK", r["reason"]
    assert r["pedestal"] == pytest.approx(c, rel=0.02)
    assert r["limit_cycle"] and r["osc_amp"] == pytest.approx(0.5 * abs(c), rel=0.05)


def test_a_zero_offset_decaying_sinusoid_that_has_finished_is_zero_pedestal():
    r = bp.tail_pedestal(sinus(tau=0.05))                      # e^-24 at 1.2 s
    assert r["status"] == "OK", r["reason"]
    assert r["pedestal"] == 0.0 and r["pedestal_db"] == -math.inf


def test_exact_zero_tail_after_a_hit_is_a_resolved_zero_not_a_refusal():
    x = np.zeros(len(T)); x[10:200] = 1000.0
    assert bp.tail_pedestal(x)["pedestal"] == 0.0


def test_the_long_legitimate_decay_defeats_the_fixed_window_mean_and_is_refused():
    """tau 2 s: zero offset, still ringing at 1.2 s. The historical mean reports
    a 'pedestal'; the truth is that there is none and the tail is unfinished."""
    for hz in (49.4, 50.1, 47.0):
        x = sinus(hz=hz, tau=2.0)
        naive = bp.naive_pedestal_db(x)
        assert naive > -75.0, f"the control must produce a convincing number, got {naive:.1f}"
        r = bp.tail_pedestal(x)
        assert r["status"] == "REFUSED" and "decaying" in r["reason"], r


def test_a_real_pedestal_under_a_long_decay_is_refused_not_misreported():
    r = bp.tail_pedestal(sinus(hz=49.4, tau=1.0, c=-80.0, amp=2.0e4))
    assert r["status"] == "REFUSED" and r["pedestal"] is None


def test_window_leakage_of_the_fixed_mean_is_bounded_by_the_fit_not_by_luck():
    # Stationary sinusoid, zero offset, non-integer cycle count in the window.
    x = 1.0e4 * np.sin(2 * math.pi * 52.37 * T)
    naive_lsb = abs(float(x[int(1.2 * SR):].mean()))
    fit = bp.tail_pedestal(x)
    assert fit["status"] == "OK"
    assert abs(fit["pedestal"]) < 0.05 * naive_lsb + 1e-6


@pytest.mark.parametrize("name,x,frag", [
    ("silence", np.zeros(len(T)), "silent"),
    ("nan", np.where(np.arange(len(T)) == 99, np.nan, 1.0), "non-finite"),
    ("inf", np.where(np.arange(len(T)) == 99, np.inf, 1.0), "non-finite"),
    ("short", np.ones(int(1.3 * SR)), "too short"),
    ("2-D", np.ones((10, 10)), "1-D"),
])
def test_degenerate_records_are_refused_never_zero_never_pass(name, x, frag):
    r = bp.tail_pedestal(x)
    assert r["status"] == "REFUSED" and frag in r["reason"]
    assert r["pedestal"] is None and r["pedestal_db"] is None


def test_a_creeping_ramp_and_a_growing_tail_are_refused():
    ramp = -(T * 1.0e4)
    assert bp.tail_pedestal(ramp)["status"] == "REFUSED"
    grow = np.exp(T / 0.3) * np.sin(2 * math.pi * 49.4 * T) + 10.0
    assert bp.tail_pedestal(grow)["status"] == "REFUSED"


def test_an_unresolvably_slow_oscillation_is_refused():
    x = -80.0 + 500.0 * np.sin(2 * math.pi * 4.0 * T)          # 4 Hz: < 3 cycles per half window
    assert bp.tail_pedestal(x)["status"] == "REFUSED"


def test_a_settling_step_is_not_called_stationary():
    x = np.full(len(T), -1000.0); x[int(1.2 * SR):int(1.35 * SR)] = -500.0
    assert bp.tail_pedestal(x)["status"] == "REFUSED"


def test_meta_the_historical_mean_would_fail_this_suite():
    """The suite must be able to fail: run its decisive case against the
    historical estimator wrapped to the same interface. It 'passes' the long
    decay with a number, which is exactly the defect."""
    def historical(x):
        return dict(status="OK", pedestal_db=bp.naive_pedestal_db(x))
    r = historical(sinus(tau=2.0))
    with pytest.raises(AssertionError):
        assert r["status"] == "REFUSED"


# ------------------------------------------------ part 2: the mechanism ------
def deadband_prediction(a1, a2, cf, rounding):
    return bp.deadband_halfwidth(a1, a2, cf, rounding)


def test_prediction_is_the_analytic_fixed_point_set_of_the_recurrence():
    """Independent of the bank: brute-force the integer recurrence on the
    fixed-point condition y = (a*y + RND) >> CF over a window, for a pole with
    a small eps so the set is enumerable."""
    cf, a_sum = 12, (1 << 12) - 4           # eps = 4 / 4096
    eps = 1 - a_sum / (1 << cf)
    for rnd, rng in ((0, False), (1 << (cf - 1), True)):
        fixed = [y for y in range(-5000, 5000) if ((a_sum * y + rnd) >> cf) == y]
        pred = 0.5 / eps if rng else 1.0 / eps
        if rng:
            assert min(fixed) >= -pred - 1 and max(fixed) <= pred + 1 and min(fixed) < 0 < max(fixed)
        else:
            assert max(fixed) == 0 and -pred - 1 <= min(fixed) < -pred / 2


def test_bd_pole_floor_lands_exactly_on_the_edge_of_the_predicted_deadband():
    r = bp.run_pole(dx.BD_HZ, dx.bd_decay_q(5.0), 300000)
    lim = bp.deadband_halfwidth(r["a1"], r["a2"], 24, False)
    assert r["fixed"]["status"] == "OK"
    assert -lim <= r["fixed"]["pedestal"] < 0
    assert abs(r["fixed"]["pedestal"]) == pytest.approx(lim, rel=0.01)   # measured -23893 vs 1/eps 23899
    # the references have NO pedestal: the arithmetic, not the coefficients
    for k in ("float_quantized", "float_ideal"):
        assert r[k]["status"] == "OK" and abs(r[k]["pedestal"]) < 1e-3
    assert r["float_quantized_tail_rms"] < 1e-3 * abs(r["fixed"]["pedestal"])


def test_replay_of_the_recorded_excitation_reproduces_the_shipped_bd_state_bit_for_bit():
    import bd_excitation_probe as bx
    rec = bp.record_bd(bx.bd_kit(), 1.0, 0.4)
    assert np.array_equal(bp.replay_bd_state(rec), rec["state"])


# Predeclared: DEVELOPMENT picked the prediction, CONFIRMATION was run once.
@pytest.mark.parametrize("pole", list(bp.CONFIRMATION["poles"]))
@pytest.mark.parametrize("sb,sq", bp.CONFIRMATION["state"])
def test_confirmation_poles_and_state_sizes_floor_pedestal_is_inside_the_predicted_band(pole, sb, sq):
    f0, q = bp.CONFIRMATION["poles"][pole]
    for lev in (bp.CONFIRMATION["levels"][1],):
        r = bp.run_pole(f0, q, lev, sb=sb, sq=sq)
        lim = bp.deadband_halfwidth(r["a1"], r["a2"], 24, False)
        tail = r["tail_fixed"]
        assert np.all(np.isfinite(tail))
        if pole == "HI":          # eps large: a limit cycle about -0.5/eps, not a DC fixed point
            assert r["fixed"]["pedestal"] == pytest.approx(-0.5 * lim, rel=0.05)
        else:
            assert tail.min() == tail.max() and -lim <= tail.max() <= 0


def test_rounding_does_not_remove_the_deadband_it_changes_its_sign_and_size():
    r_floor = bp.run_pole(dx.BD_HZ, dx.bd_decay_q(5.0), 3000)
    r_round = bp.run_pole(dx.BD_HZ, dx.bd_decay_q(5.0), 3000, rounding=True)
    lim = bp.deadband_halfwidth(r_round["a1"], r_round["a2"], 24, True)
    assert abs(r_round["fixed"]["pedestal"]) == pytest.approx(lim, rel=0.01)
    # at this level rounding is WORSE than the shipped floor -- 3 orders of magnitude
    assert abs(r_round["fixed"]["pedestal"]) > 100 * abs(r_floor["fixed"]["pedestal"])
    pos = bp.run_pole(dx.BD_HZ, dx.bd_decay_q(5.0), 30000, rounding=True)
    assert pos["fixed"]["pedestal"] > 0 > r_round["fixed"]["pedestal"]


def test_pedestal_in_state_lsb_is_independent_of_state_width_so_relative_size_scales():
    f0, q = bp.CONFIRMATION["poles"]["MT"]
    base = bp.run_pole(f0, q, 20000)
    wide = bp.run_pole(f0, q, 20000, sb=32, sq=19)
    assert base["fixed"]["pedestal"] == wide["fixed"]["pedestal"]
    assert wide["peak"] / base["peak"] == pytest.approx(16.0, rel=0.02)


def test_run_pole_refuses_a_saturated_state_instead_of_reporting_it():
    with pytest.raises(bp.Refused):
        bp.run_pole(dx.BD_HZ, dx.bd_decay_q(5.0), 300000, sb=22, sq=15, n=int(0.2 * SR))


def test_property_defect_matrix_moves_where_it_should_and_is_blind_where_it_must_be():
    m = bp.property_matrix()
    p = bp.PROPERTIES
    print()
    for d, row in m.items():
        for name in p:
            print(f"  {d:<28} {name:<70} {row[name]['value']!s:<6} {row[name]['verdict']}")
    rd, wd, fl = (m["rounding"], m["state +4 bits (SB32 SQ19)"],
                  m["float recurrence (cause removed)"])
    # the positive control: remove the integer arithmetic and the tail properties MUST move
    assert fl[p[1]]["verdict"] == fl[p[2]]["verdict"] == fl[p[3]]["verdict"] == "MOVED"
    assert rd[p[0]]["verdict"] == "MOVED"        # floor => one-sided; rounding => two-sided
    assert rd[p[2]]["verdict"] == "BLIND"        # rounding does not shrink the relative pedestal
    assert rd[p[3]]["verdict"] == "BLIND"        # and the tail still never decays
    assert wd[p[2]]["verdict"] == "MOVED"        # more state bits do shrink it relative to signal
    assert wd[p[0]]["verdict"] == "BLIND"
    # every property is MOVED by at least one defect, so none is a rubber stamp
    for name in p:
        assert any(m[d][name]["verdict"] == "MOVED" for d in m if not d.startswith("none")), name
