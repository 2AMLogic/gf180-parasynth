"""#780: the sweep's own validation cases.

The probe is an instrument, so these check the instrument -- that its
pre-repair copy really is the pre-repair estimator, that its closed form
really matches its panel, that its panels really hold the periods they claim,
and that its injections really go red. A sweep whose panels are not what its
prose says they are produces numbers that look exactly like measurements.
"""
import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT / "model"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import audio_measure as am                                           # noqa: E402
import repeat_period_multiple as rp                                  # noqa: E402

SR = rp.SR


# ---------------------------------------------------------------------------
# the panels are what the prose says they are
# ---------------------------------------------------------------------------
def test_the_exact_loop_repeats_bit_exactly_at_its_stated_period():
    """Not 'nearly'. If the panel's records only approximately repeat then the
    0-of-60 result is a statement about the approximation, not about the
    estimator."""
    for P in (65535, 32768, 4096):
        x = rp.exact_loop(P, 3)
        assert len(x) == int(rp.EXACT_DUR_S * SR)
        head, shifted = x[:len(x) - P], x[P:]
        assert np.array_equal(head, shifted), P


def test_the_alternating_loop_repeats_at_2P_and_not_at_P():
    """The fold panel's whole claim. Its true period must be 2P exactly, and
    the near-repeat at P must be exactly as deep as `depth` says."""
    P, depth = rp.FOLD_HALF_PERIOD, 0.10
    x = rp.alternating_loop(P, depth, 0)
    assert np.allclose(x[:len(x) - 2 * P], x[2 * P:]), "not periodic at 2P"
    a, b = x[:P], x[P:2 * P]
    assert not np.allclose(a, b), "the record repeats at P, so there is no octave to fold"
    assert np.allclose(b, a * (1.0 - depth)), "the alternation is not `depth` deep"


def test_the_fold_shortfall_matches_its_closed_form_on_every_panel_depth():
    """`1 - nccf[P] = d^2 / (2 - 2d + d^2)`, derived before it was measured.
    This is the agreement the dial's whole justification rests on: the margin
    converts into a fold depth only if this law holds."""
    for d in rp.FOLD_DEPTHS:
        r, lo, hi = rp.nccf_of(rp.alternating_loop(rp.FOLD_HALF_PERIOD, d, 0))
        got = 1.0 - float(r[rp.FOLD_HALF_PERIOD])
        want = rp.fold_shortfall(d)
        assert abs(got - want) < 1e-4 * max(want, 1e-4) + 1e-6, (d, got, want)


def test_the_lfsr_matches_the_one_the_voice_tests_use():
    """The probe carries its own copy (a probe must not import a test module).
    If the two drift, the need panel is measuring a different sequence from
    the one `model/test_reference_voice.py` asserts against."""
    sys.path.insert(0, str(ROOT / "model"))
    import test_reference_voice as trv

    assert np.array_equal(rp.lfsr16(200000), trv.lfsr16(200000))
    x = rp.lfsr16(70000)
    assert np.array_equal(x[:70000 - 65535], x[65535:]), "period is not 65535"
    assert set(np.unique(x)) == {-1.0, 1.0}


# ---------------------------------------------------------------------------
# the pre-repair copy is the pre-repair estimator
# ---------------------------------------------------------------------------
def test_the_baseline_reproduces_the_three_rows_the_issue_was_filed_with():
    """#780's table, verbatim: 65535 right, 32768 at FOUR TIMES its period and
    reported at a correlation above 1, 20000 right. If this drifts, the
    before/after column is comparing against something that never shipped."""
    rng = np.random.default_rng(7)      # ONE generator, drawn in this order
    want = {65535: 1.0, 32768: 4.0, 20000: 1.0}
    corr = {}
    for P in (65535, 32768, 20000):
        base = rng.standard_normal(P)
        x = np.tile(base, int(np.ceil(6.0 * SR / P)))[:int(6.0 * SR)]
        v, c = rp._overlap_corrected_baseline(x, SR)
        assert v is not None, P
        assert abs(v * SR / P - want[P]) < 0.01, (P, v * SR / P)
        corr[P] = c
    # the tell the issue was filed on: a "correlation" above 1. The draw order
    # matters -- `default_rng(7)` reseeded per period gives DIFFERENT records,
    # and the first draft of this test did that and read 0.99991.
    assert corr[32768] > 1.0, corr
    assert abs(corr[32768] - 1.000443) < 1e-5, corr[32768]


def test_the_shipped_estimator_returns_the_true_period_on_the_whole_exact_panel():
    """The headline. 60 records, 33 of which the pre-repair estimator got
    wrong."""
    for P in rp.EXACT_PERIODS:
        for s in range(rp.EXACT_SEEDS):
            e = am.repeat_period(rp.exact_loop(P, s), SR)
            assert e.ok, (P, s, e.reason)
            assert abs(e.value * SR / P - 1.0) <= rp.MULT_TOL, (P, s, e.value * SR / P)
            assert e.detail["corr"] <= 1.0 + 1e-12, (P, s, e.detail["corr"])


# ---------------------------------------------------------------------------
# the instrument refuses rather than reporting
# ---------------------------------------------------------------------------
def test_nccf_of_refuses_when_it_disagrees_with_the_shipped_estimator():
    """`nccf_of` exists so the panels can read the statistic directly, which
    is a second implementation of it. It must refuse rather than quietly
    disagree -- so point the shipped name at something else and check it
    does."""
    x = rp.exact_loop(32768, 0)
    real = am.repeat_period
    try:
        am.repeat_period = lambda *a, **k: am.Estimate(
            0.123456, True, "", dict(corr=1.0))
        with pytest.raises(rp.Refused, match="drifted"):
            rp.nccf_of(x)
    finally:
        am.repeat_period = real


def test_the_inharmonic_panel_derives_its_truth_without_the_estimator():
    """The constraint column must not be circular. Its true lag comes from
    `1.5 * f0` and the sample rate; break the estimator and the panel's
    answer must not move."""
    panel = rp.inharmonic_panel()
    assert len(panel) >= 3
    for label, _sig, sr, lag in panel:
        assert lag == 4800, (label, lag)          # 22 carrier periods at 220 Hz
        assert lag >= int(0.05 * sr)
    real = am.repeat_period
    try:
        am.repeat_period = lambda *a, **k: am.Estimate(
            0.05, True, "", dict(corr=1.0))
        again = rp.inharmonic_panel()
    finally:
        am.repeat_period = real
    assert [p[3] for p in again] == [p[3] for p in panel]


def test_the_alternating_loop_refuses_an_impossible_depth():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(rp.Refused):
            rp.alternating_loop(1000, bad, 0)


def test_an_unknown_injection_refuses_and_names_the_ones_there_are():
    with pytest.raises(rp.Refused, match="NO_SUBMULTIPLE_RULE"):
        rp.injected("NOT_AN_INJECTION")


# ---------------------------------------------------------------------------
# the controls go red, each for its own reason
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,least", [("NO_SUBMULTIPLE_RULE", 30),
                                        ("OVERLAP_NORMALISER", 10)])
def test_each_injection_turns_its_own_panel_red(name, least):
    """Rule 5: a repair with no injection is not closed. `least` is a floor
    well under the measured 43 and 28, so the test asserts the control has
    REAL power without pinning a number that a numpy build can move."""
    res = rp.injected(name)
    assert res["wrong"] >= least, res
    assert res["total"] == len(rp.EXACT_PERIODS) * rp.EXACT_SEEDS


def test_the_two_injections_are_red_for_DIFFERENT_reasons():
    """#780's test plan: reverting either half must go red, and the halves do
    not do the same job. The rule half breaks the PERIOD; the normaliser half
    breaks the UNITS, and leaves the period alone on this panel. A single
    control covering both would have hidden that."""
    assert rp.INJECTIONS["NO_SUBMULTIPLE_RULE"][1] == "period"
    assert rp.INJECTIONS["OVERLAP_NORMALISER"][1] == "corr_bound"
    # the normaliser half really does leave the period right: this is the
    # measurement that refuted the issue's "both halves are load-bearing"
    wrong = 0
    for P in rp.EXACT_PERIODS:
        for s in range(rp.EXACT_SEEDS):
            v = rp._estimate(rp.exact_loop(P, s), SR, "overlap",
                             am.REPEAT_SUBMULTIPLE_MARGIN_PERMILLE)
            wrong += 0 if rp.is_right(v, P) else 1
    assert wrong == 0, (f"the overlap statistic WITH the submultiple rule got "
                        f"{wrong}/60 periods wrong; the split recorded in "
                        f"repeat_period's docstring no longer holds")


def test_an_injection_refuses_when_its_own_baseline_is_already_red():
    """A control measured against a broken baseline reports CAUGHT for
    nothing. Break the shipped estimator and the injection must REFUSE, not
    report a red panel."""
    real = am.repeat_period
    try:
        am.repeat_period = lambda *a, **k: am.Estimate(
            0.111, True, "", dict(corr=1.0))
        with pytest.raises(rp.Refused, match="baseline that is already red"):
            rp.injected("NO_SUBMULTIPLE_RULE")
    finally:
        am.repeat_period = real


# ---------------------------------------------------------------------------
# the grid and the shipped dial agree with the record
# ---------------------------------------------------------------------------
def test_the_grid_carries_the_shipped_margin_and_a_zero():
    assert am.REPEAT_SUBMULTIPLE_MARGIN_PERMILLE in rp.GRID_PERMILLE
    assert 0 in rp.GRID_PERMILLE, "without 0 the sweep prices the dial but not the repair"
    assert rp.GRID_PERMILLE == tuple(sorted(rp.GRID_PERMILLE))


def test_the_margin_the_other_call_site_ships_is_on_the_grid():
    """`discrimination_features.JIT_OCTAVE_MARGIN_PERMILLE` is the same rule's
    dial elsewhere (#774). It is on this grid so that 'do not propagate that
    value here' is a measured row and not an assertion. Skips rather than
    fails if that module is not importable, since it is not this estimator's
    dependency."""
    try:
        import discrimination_features as df
    except Exception:                                       # pragma: no cover
        pytest.skip("discrimination_features is not importable here")
    other = getattr(df, "JIT_OCTAVE_MARGIN_PERMILLE", None)
    if other is None:
        # #774's dial lands with PR #781, which was still open when #780 was
        # built. Skipping is correct and the assertions below are what make
        # this test start working the moment it merges, rather than silently
        # staying green on a module that no longer has the constant.
        pytest.skip("JIT_OCTAVE_MARGIN_PERMILLE is not in discrimination_features "
                    "yet; it lands with #774/PR #781")
    assert other in rp.GRID_PERMILLE, (
        f"the other call site ships {other} thousandths, which is not a row of "
        f"this sweep -- so 'do not propagate it here' is an assertion again")
    assert other != am.REPEAT_SUBMULTIPLE_MARGIN_PERMILLE, (
        "the two dials now agree, so the deliberate difference recorded in "
        "REPEAT_SUBMULTIPLE_MARGIN_PERMILLE's comment is stale")
