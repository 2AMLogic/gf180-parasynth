#!/usr/bin/env python3
"""Ground truth, refusals, locks and injected controls for `model/res_tuning.py`
(issue #257's resonance-keyed cutoff correction).

    python3 -m pytest model/test_res_tuning.py -q        # ~1.5 min

What is ground truth for what:

  * the ring estimator against synthetic signals whose frequency is known by
    construction -- never against the filter it measures
  * the correction's integer read against values computed here by hand
  * the acceptance verdict against the COMMITTED validation artefact, which was
    produced on a grid committed (docs/res-tuning/plan.json) before any
    candidate existed
  * the candidate itself against its own validated points -- a lock, which is
    evidence of SAMENESS, not of quality; every control must break it

And what the acceptance targets are BLIND to, pinned rather than hidden
(docs/verification-rules.md rule 4): four of the six controls pass every
pre-registered target, because the targets are set relative to a 105-cent
baseline. The lock catches all six.
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

import reference_rigs as rr                                         # noqa: E402
import res_tuning as rt                                             # noqa: E402
import voice_fx as vf                                               # noqa: E402

VAL = os.path.join(rt.OUT_DIR, "validation.json")
CTL = os.path.join(rt.OUT_DIR, "controls.json")
SWEEPS = os.path.join(rt.OUT_DIR, "sweeps.json")


def _json(path):
    with open(path) as fh:
        return json.load(fh)


# =============================================================================
# 1. the estimator, on signals whose answer is known independently
# =============================================================================
def test_the_estimator_is_qualified_on_known_frequencies():
    """30 Hz to 16 kHz, sine and tanh-saturated, plus the louder of two lines:
    every one within plan.json's 1-cent accuracy (measured worst: 0.04 c)."""
    q = rt.qualify_estimator()
    assert all(r["ok"] for r in q["rows"]), [r for r in q["rows"] if not r["ok"]]
    assert q["worst_cents"] < 0.1, q["worst_cents"]
    assert q["ok"]


@pytest.mark.parametrize("fixture,reason", [
    ("silence", "silent"),
    ("nan", "non-finite"),
    ("inf", "non-finite"),
    ("too-short-15Hz", "insufficient duration"),
    ("too-short-20Hz", "insufficient duration"),
    ("decaying", "not a sustained ring"),
    ("dead-band", "not a sustained ring"),
    ("two-tone", "ambiguous"),
])
def test_each_refusal_fires_for_its_own_reason(fixture, reason):
    """A refusal for the wrong reason is a control that did not fire: a missing
    import would also 'refuse'. So the reason is part of the assertion."""
    r = rt.qualify_estimator()["refusals"][fixture]
    assert r["refused"], r
    assert reason in r["reason"], r


def test_the_sustain_classifier_has_margin_at_the_plans_below_onset_point():
    """Rule 8, the stated adversary: a decay slow enough to lose under 1 dB in
    the window would be called sustained. At the plan's below-onset point the
    slowest measured decay is 30 Hz (-4.2 dB), and the dead band elsewhere sits
    more than 20 dB under the level floor. Pinned so a change that eats the
    margin is visible."""
    dev = rt.CorrectedLadder()
    y = dev.ring(30, 0.97, seconds=0.8)
    q = len(y) // 4
    drop = 20 * math.log10(np.abs(y[-q:]).max() / np.abs(y[:q]).max())
    assert drop < -3.0, drop
    assert not rt.is_sustained(y)
    y = dev.ring(1131, 0.97, seconds=0.8)
    assert np.abs(y[-q:]).max() < rt.SUSTAIN_FLOOR / 10.0
    assert not rt.is_sustained(y)
    assert rt.is_sustained(dev.ring(1131, 1.03, seconds=0.8))


def test_the_baseline_arm_is_the_shipped_filter():
    rt.assert_baseline_path()


def test_the_estimator_reproduces_issue_237s_travel():
    """Independent of `ladder_headroom._ring_ratio` (which falls back to the
    spectral peak instead of refusing), and it agrees: ~105 cents."""
    dev = rt.CorrectedLadder()
    lo = rt.mean_offset_cents(dev, (100, 800, 6400), 1.02)
    hi = rt.mean_offset_cents(dev, (100, 800, 6400), 2.00)
    assert lo - hi == pytest.approx(105.0, abs=10.0), (lo, hi)


# =============================================================================
# 2. the integer contract, by hand
# =============================================================================
def test_the_correction_read_known_answers():
    rom = np.array([32768, 33000, 34000], dtype=np.int64)     # 3 entries, b = 1, fb = 15
    assert int(rt.corr_from_k(0, rom)) == 32768                # below onset: entry 0
    assert int(rt.corr_from_k(65536, rom)) == 32768            # exactly onset
    assert int(rt.corr_from_k(65536 + 32768, rom)) == 33000    # on a knot
    assert int(rt.corr_from_k(65536 + 16384, rom)) == 32768 + (232 * 16384 >> 15)
    assert int(rt.corr_from_k(131071, rom)) == 33000 + ((1000 * 32767) >> 15)   # k clamp
    assert int(rt.corr_from_k(10 ** 7, rom)) == int(rt.corr_from_k(131071, rom))


def test_a_descending_table_floors_like_g_from_cut():
    rom = np.array([33000, 32768], dtype=np.int64)            # 2 entries, b = 0, fb = 16
    assert int(rt.corr_from_k(65536 + 1, rom)) == 33000 + ((-232 * 1) >> 16) == 32999


@pytest.mark.parametrize("n", [0, 1, 4, 6, 10])
def test_a_table_that_is_not_two_to_the_b_plus_one_is_refused(n):
    with pytest.raises(ValueError):
        rt.corr_bits(n)


def test_the_corrected_cutoff_rounds_and_clamps():
    unity = np.full(3, 32768, dtype=np.int64)
    assert int(rt.corrected_cut(1000, 100000, unity)) == 1000
    up = np.full(3, 32768 + 16384, dtype=np.int64)             # x1.5
    assert int(rt.corrected_cut(31, 100000, up)) == 47         # 46.5 rounds up
    assert int(rt.corrected_cut(21000, 100000, up)) == vf.CUT_MAX
    down = np.full(3, 16384, dtype=np.int64)                   # x0.5
    assert int(rt.corrected_cut(40, 100000, down)) == vf.CUT_MIN
    assert int(rt.corrected_cut(1234, 100000, None)) == 1234   # no stage: identity


def test_at_and_below_the_onset_the_correction_is_the_identity():
    """Entry 0 is unity, so for k <= 65536 the candidate renders exactly what
    the same ROMs render with no correction stage at all -- the property that
    lets the below-onset filter be judged by DR 0006's onset check alone."""
    g, k, corr = rt.committed_candidate()
    assert corr[0] == 1 << rt.CORR_Q
    for res in (0.5, 0.9, 1.0):
        a = rt.CorrectedLadder(g, k, corr).ring(800, res, seconds=0.2)
        b = rt.CorrectedLadder(g, k, None).ring(800, res, seconds=0.2)
        assert np.array_equal(a, b), res


def test_the_compensation_is_re_derived_from_the_candidate_coefficients():
    """DR 0006: re-derived, not refit. The candidate's k ROM is `k_onset` over
    the candidate's g ROM by voice_fx.make_k_rom's construction; it differs
    from the shipped table (by up to 0.42 %), which is why using the shipped
    one is a control."""
    g, k, _ = rt.committed_candidate()
    assert np.array_equal(k, rt.k_rom_for(g))
    assert not np.array_equal(k, vf.make_k_rom())
    # the construction is make_k_rom's: on the SHIPPED g ROM it reproduces it
    assert np.array_equal(rt.k_rom_for(vf.make_g_rom()), vf.make_k_rom())


def test_the_k_rom_cache_is_keyed_on_content_not_identity():
    """`reference_rigs._k_rom_for` caches on id(g_rom); a freed array's id can
    be reused by a different ROM. This module's cache keys on the bytes."""
    a = rt.k_rom_for(rt.g_rom_for(0, 1))
    b = rt.k_rom_for(rt.g_rom_for(3, 1))
    assert not np.array_equal(a, b)


# =============================================================================
# 3. start red, then the committed verdict
# =============================================================================
def test_start_red_the_baseline_does_not_pass_the_acceptance():
    """The harness rejects the unchanged filter: baseline-as-candidate fails
    the travel targets (it passes the non-regression margins trivially)."""
    plan = rt.load_plan(require_committed=False)
    val = _json(VAL)
    mb, ob = val["baseline"]["metrics"], val["baseline"]["onset"]
    v = rt.verdict(plan, mb, mb, ob, ob)
    assert not v["ok"]
    assert not v["checks"]["T1_mean_offset_travel"]
    assert not v["checks"]["T2_worst_per_cut_travel"]


def test_the_committed_validation_passes_and_recomputes():
    plan = rt.load_plan(require_committed=False)
    val = _json(VAL)
    v = rt.verdict(plan, val["baseline"]["metrics"], val["candidate"]["metrics"],
                   val["baseline"]["onset"], val["candidate"]["onset"])
    assert v == val["verdict"] and v["ok"]
    assert val["baseline"]["metrics"]["mean_offset_travel_cents"] > 100.0
    assert val["candidate"]["metrics"]["mean_offset_travel_cents"] < 6.0
    rt.committed_candidate()                     # one candidate, not three


def test_the_sweeps_select_what_the_module_ships():
    sw = _json(SWEEPS)
    assert sw["chosen"] == dict(LAW_DEGREE=rt.LAW_DEGREE, CORR_ENTRIES=rt.CORR_ENTRIES)
    plan = rt.load_plan(require_committed=False)["sweeps"]
    assert rt._select(sw["law"], "LAW_DEGREE", "worst_abs_cents",
                      plan["res-cut-law-degree"]["flat_within_relative"]) == rt.LAW_DEGREE
    assert rt._select(sw["entries"], "CORR_ENTRIES", "mean_offset_travel_cents",
                      plan["res-cut-correction-entries"]["flat_within_relative"]) == rt.CORR_ENTRIES


def test_the_plan_refuses_when_it_is_not_committed(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(open(rt.PLAN).read())
    with pytest.raises(rt.Refused):
        rt.load_plan(str(p))
    with pytest.raises(rt.Refused):
        rt.load_plan(str(tmp_path / "absent.json"), require_committed=False)


def test_issue_237s_control_moves_against_the_pinned_historical_baseline():
    """#237's resonance-dependent-skew control showed the travel CAN be moved;
    this is the shipped-baseline direction it asked for. Historical baseline
    pinned at 105 cents (`test_ladder_headroom` still asserts the shipped
    filter has it); the candidate takes the same quick probe under 15."""
    g, k, corr = rt.committed_candidate()
    dev = rt.CorrectedLadder(g, k, corr)
    cand = rt.mean_offset_cents(dev, (100, 800, 6400), 1.02) - \
        rt.mean_offset_cents(dev, (100, 800, 6400), 2.00)
    assert abs(cand) < 15.0, cand
    assert 105.0 - abs(cand) > 70.0


# =============================================================================
# 4. the lock, and every control breaking it
# =============================================================================
LOCK_POINTS = ((30, 1.15), (566, 1.6), (2263, 1.03), (9000, 1.99))


def _points(dev):
    return {f"{c}@{float(r)}": rt._cents(rt.ring_frequency(dev.ring(c, r, seconds=0.8)), c)
            for c, r in LOCK_POINTS}


def test_the_candidate_reproduces_its_validated_points():
    g, k, corr = rt.committed_candidate()
    got = _points(rt.CorrectedLadder(g, k, corr))
    want = _json(VAL)["candidate"]["points"]
    for key, x in got.items():
        assert x == pytest.approx(want[key], abs=1e-9), key


@pytest.mark.parametrize("name", sorted(rt.CONTROLS))
def test_every_control_executes_and_breaks_the_lock(name):
    """Rule 5 conditions: the mutant builds, renders finite sustained rings
    (ring_frequency refuses anything else), and moves at least one locked point.
    Run on the lock subset; the full matrix is `res_tuning.py controls`."""
    g, k, corr = rt.committed_candidate()
    got = _points(rt.MutantLadder(name, g, k, corr))
    want = _json(VAL)["candidate"]["points"]
    moved = max(abs(got[key] - want[key]) for key in got)
    assert moved > 1e-6, (name, got)


def test_the_acceptance_matrix_is_pinned_including_what_it_is_blind_to():
    """From the committed `res_tuning.py controls` run. Two controls are
    caught by the pre-registered acceptance targets; four are BLIND to all
    five, which is stated here rather than discovered later."""
    rep = _json(CTL)
    assert set(rep["controls"]) == set(rt.CONTROLS)
    assert all(rep["clean"]["checks"].values())
    caught = {n for n, c in rep["controls"].items() if c["caught"]}
    assert caught == {"disabled", "reversed"}, caught
    for n in set(rt.CONTROLS) - caught:
        assert set(rep["controls"][n]["matrix"].values()) == {"BLIND"}, n
    assert rep["controls"]["reversed"]["matrix"]["N1_worst_abs"] == "MOVED"
    assert rep["controls"]["disabled"]["matrix"]["N1_worst_abs"] == "BLIND"
