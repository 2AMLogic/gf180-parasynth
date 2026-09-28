"""Known answers and paired must-fail controls for tools/cymbal_mid.py.

Every signal here is planted: a band-limited noise source times an exponential whose time
constant, level and noise floor are chosen by this file, so nothing below is validated against
any drum model of ours. Both estimators (the Schroeder EDT10/T20 reused from `cymbal_bands` and
`cymbal_mid.two_window_t20`, which shares only the band-pass with it) are held to the SAME
planted value, so an agreement between them on the real recordings means something.

Each control states its paired opposite explicitly. #376 is the precedent: the cymbal's first
crosstalk control could not fail, and passed vacuously for weeks.
"""
import functools
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.signal import butter, lfilter, sosfilt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_bands as cb  # noqa: E402
import cymbal_mid as cm  # noqa: E402

SR = 48_000


def t20_of(tau):
    """An exponential amplitude envelope falls 20 dB in tau * ln(10) seconds."""
    return tau * math.log(10.0)


def edt10_of(tau):
    """...and 10 dB in half that: 1.1513 * tau. The Schroeder curve of an exponential falls at
    the same 8.686/tau dB per second the power envelope does, so this is EDT10's known answer
    analytically, with nothing of ours in it."""
    return 0.5 * tau * math.log(10.0)


@functools.lru_cache(maxsize=64)
def _measured(items, bands):
    return cm.measure_mid(strike(**dict(items)), SR, bands=bands)


def measured(_bands=("M",), **kw):
    """`measure_mid` of a planted strike, memoised, and by default only the M band: the same
    planted case is asked for by several tests and a 6 s record's envelopes are not free."""
    return _measured(tuple(sorted(kw.items())), _bands)


def decaying_tone(tau, f=1300.0, dur=4.0, pad_s=0.05):
    """A NOISELESS decaying tone inside the M band: the one planted case with no beating and no
    seed, so both estimators' answers are exact rather than scattered, and the two-window
    estimator's ln(E1/E2) is analytic (2W/tau)."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * math.pi * f * t) * np.exp(-t / tau)
    return cb.rc.prepare(np.concatenate([np.zeros(int(pad_s * SR)), y]), SR, side="tone")


def _noise(lo, hi, n, seed, order=6):
    rng = np.random.default_rng(seed)
    return sosfilt(butter(order, [lo, hi], btype="bandpass", fs=SR, output="sos"),
                   rng.standard_normal(n))


def _q6_low(n, seed, f0=cm.LOW_BP[0], q=cm.LOW_BP[1]):
    """The 808 low band's ACTUAL shape: a constant-skirt-gain 2-pole/2-zero band-pass at
    3.45 kHz, Q 6 (RBJ cookbook), so its skirt really does reach down into M. A steep synthetic
    window here would make the leakage control unfailable -- the #376 mistake."""
    w0 = 2 * math.pi * f0 / SR
    alpha = math.sin(w0) / (2 * q)
    b = [q * alpha, 0.0, -q * alpha]
    a = [1 + alpha, -2 * math.cos(w0), 1 - alpha]
    b, a = [c / a[0] for c in b], [c / a[0] for c in a]
    return lfilter(b, a, np.random.default_rng(seed).standard_normal(n))


@functools.lru_cache(maxsize=64)
def strike(tau_m=0.30, a_m=4.0, tau_l=0.35, a_l=1.0, tau_h=0.20, a_h=0.6,
           tau_m2=None, a_m2=0.0, dur=4.0, seed=3, floor_db=None, pad_s=0.05):
    """A planted cymbal-shaped strike: an M-band component (1.0-1.6 kHz, inside cymbal_mid.MID's
    M window), the 808's Q6 low band at 3.45 kHz whose skirt leaks into M, and a high band.
    `a_m=0.0` removes M's own content and leaves only that leakage -- the negative case.
    `a_l=0.0` removes the low band, which is how the ESTIMATORS are tested apart from the
    preconditions. `floor_db` adds a wide-band noise floor at that many dB below the peak.

    The default `a_m=4.0` is the amplitude at which M's own content clears the leak margin with
    room to spare (~16 dB at the strike); at a_m=1.0 the planted signal is itself refused, which
    `test_control_leakage_only_band_refuses_and_its_pair_with_real_content_answers` uses."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    if a_m:
        env = a_m * np.exp(-t / tau_m)
        if tau_m2 is not None:
            env = env + a_m2 * np.exp(-t / tau_m2)
        y += _noise(1000.0, 1600.0, n, seed) * env
    if a_l:
        y += a_l * _q6_low(n, seed + 1) * np.exp(-t / tau_l)
    if a_h:
        y += a_h * _noise(7000.0, 12000.0, n, seed + 2) * np.exp(-t / tau_h)
    y /= np.max(np.abs(y))
    if floor_db is not None:
        y = y + 10.0 ** (floor_db / 20.0) * np.random.default_rng(seed + 3).standard_normal(n)
        y /= np.max(np.abs(y))
    return cb.rc.prepare(np.concatenate([np.zeros(int(pad_s * SR)), y]), SR, side="synthetic-mid")


# --------------------------------------------------------------------------- the band's geometry

def test_skirt_at_the_band_edges_is_what_the_docstring_claims():
    """M is only a separable band because the 3.45 kHz Q6 band-pass is far down at its edges --
    the same argument `cymbal_bands.BANDS` makes for Ln. Pin the numbers so the prose cannot
    drift from the filter (#383: a docstring once claimed 4x the precision the filter had)."""
    lo, hi = cm.MID["M"]
    assert float(cm.skirt_db(lo)) == pytest.approx(-26.73, abs=0.05)
    assert float(cm.skirt_db(hi)) == pytest.approx(-18.67, abs=0.05)
    assert float(cm.skirt_db(cm.MID["M25"][1])) == pytest.approx(-8.44, abs=0.05)
    # M25's top edge is ~10 dB less separated than M's: which is why M25 is reported, not qualified
    assert float(cm.skirt_db(hi)) - float(cm.skirt_db(cm.MID["M25"][1])) < -5.0


def test_predicted_leak_is_larger_for_the_wider_band_and_both_are_well_below_L():
    lm, lm25 = cm.skirt_leak_db(cm.MID["M"]), cm.skirt_leak_db(cm.MID["M25"])
    assert lm < lm25 < 0.0
    assert -30.0 < lm < -15.0, lm


def test_bp_mag_is_unity_at_the_centre_and_half_power_at_the_q_edges():
    f0, q = cm.LOW_BP
    assert float(cm.bp_mag(f0)) == pytest.approx(1.0, abs=1e-12)
    bw = f0 / q
    f_hi = math.sqrt((bw / 2) ** 2 + f0 ** 2) + bw / 2          # the analytic -3 dB edge
    assert 20 * math.log10(float(cm.bp_mag(f_hi))) == pytest.approx(-3.0103, abs=0.01)


# --------------------------------------------------------------------------- known answers

@pytest.mark.parametrize("tau", (0.10, 0.15, 0.30, 0.60))
@pytest.mark.parametrize("seed", (3, 7, 11))
def test_the_estimators_read_a_planted_single_exponential(tau, seed):
    """No low band here (`a_l=0.0`): this is the estimators' own accuracy, measured apart from
    the preconditions, against the ANALYTIC answer for an exponential rather than against each
    other. The bounds are the ones MEASURED over these 12 cases, not aspirational -- EDT10 and
    the Schroeder T20 within 10 % and 6 %, the two-window T20 within 15 %, its resolution being
    set by how well a 891 Hz-wide noise band's power can be estimated in one window (worst case
    here +13.4 %). Quoting a tighter bound than the instrument has is the #383 mistake."""
    r = measured(tau_m=tau, a_l=0.0, seed=seed, dur=max(4.0, 10 * tau))["M"]
    assert "refused" not in r, r.get("refused")
    assert r["edt10_qualified_ms"] == pytest.approx(1e3 * edt10_of(tau), rel=0.10), r
    tgt = 1e3 * t20_of(tau)
    assert r["t20_late_ms"] == pytest.approx(tgt, rel=0.06), r
    assert r["xcheck"]["t20_ms"] == pytest.approx(tgt, rel=0.15), r
    assert r["t20_qualified_ms"] == r["t20_late_ms"], r          # the two agree, so it is quotable


@pytest.mark.parametrize("tau,seed", ((0.15, 7), (0.30, 11), (0.60, 7)))
def test_the_envelope_edt_estimator_is_the_one_that_could_not_be_a_gate(tau, seed):
    """The wrong-then-right record for `envelope_edt10_ms`: it was built to be EDT10's second
    estimator and gate, and on these planted cases it is 26-36 % low against the analytic answer
    while the Schroeder EDT10 on the SAME records is within 7 %. Gating EDT10 on a 25 % agreement
    with it would have refused correct answers, which is why it is reported and not gated. Pinned
    here so nobody re-promotes it without re-measuring."""
    r = measured(tau_m=tau, a_l=0.0, seed=seed, dur=max(4.0, 10 * tau))["M"]
    true_ms = 1e3 * edt10_of(tau)
    assert r["edt10_ms"] == pytest.approx(true_ms, rel=0.07), r["edt10_ms"]
    assert r["edt10_env_ms"] < 0.80 * true_ms, (r["edt10_env_ms"], true_ms)
    assert r["edt10_qualified_ms"] is not None, "EDT10 must NOT be gated on that estimator"


def test_a_planted_two_slope_m_band_reads_short_at_edt_and_the_long_part_late():
    """The structure the 808's bands actually have, planted in M: a fast component plus a slow
    one. EDT10 must read shorter than the slow component's T20, and the late T20 must find the
    slow component."""
    r = measured(tau_m=0.05, tau_m2=0.50, a_m2=0.6, a_l=0.0, dur=6.0)["M"]
    assert "refused" not in r, r.get("refused")
    assert r["edt10_ms"] < 0.5 * 1e3 * t20_of(0.50)
    assert r["t20_late_ms"] == pytest.approx(1e3 * t20_of(0.50), rel=0.15), r


def test_a_planted_level_change_reads_as_that_many_db():
    a = measured(a_m=4.0)["M"]["energy_share_db"]
    b = measured(a_m=2.0)["M"]["energy_share_db"]
    assert (b - a) == pytest.approx(-6.02, abs=1.0), (a, b)


def test_invariance_to_scale_and_to_prepended_silence():
    base = strike()
    a = cm.measure_mid(base, SR, bands=("M",))["M"]
    b = cm.measure_mid(0.25 * base, SR, bands=("M",))["M"]
    c = cm.measure_mid(cb.rc.prepare(np.concatenate([np.zeros(SR // 3), base]), SR, side="pad"),
                       SR, bands=("M",))["M"]
    for k in ("energy_share_db", "t20_late_ms", "edt10_ms"):
        assert b[k] == pytest.approx(a[k], rel=1e-6, abs=1e-6), k
        assert c[k] == pytest.approx(a[k], rel=0.02, abs=0.1), k
    assert b["xcheck"]["t20_ms"] == pytest.approx(a["xcheck"]["t20_ms"], rel=1e-6)


def test_m_follows_its_own_planted_decay_over_a_four_fold_range():
    """M must report ITS OWN decay. Both cases must be QUALIFIED, so this is not satisfied by a
    refusal. No low band (`a_l=0.0`) -- with one present, the slower of the two dominates M's
    tail and the instrument correctly refuses instead, which is
    `test_control_a_tail_that_passes_the_leak_margin_can_still_be_biased`'s job."""
    a = measured(tau_m=0.15, a_l=0.0, dur=6.0)["M"]
    b = measured(tau_m=0.60, a_l=0.0, dur=6.0)["M"]
    assert a["t20_qualified_ms"] and b["t20_qualified_ms"], (a.get("t20_refused"), b.get("t20_refused"))
    assert b["t20_qualified_ms"] > 2.5 * a["t20_qualified_ms"], (a["t20_qualified_ms"], b["t20_qualified_ms"])


def test_a_truncated_record_refuses_rather_than_answering():
    y = strike(tau_m=0.60)
    cut = y[: cb.rc.required_lead_samples(SR) + int(0.30 * SR)]
    r = cm.measure_mid(cut, SR)              # the default: both bands, as every report runs it
    assert set(r) >= set(cm.MID), r
    assert r["M"]["t20_late_ms"] is None
    assert r["M"].get("t20_refused") or "refused" in r["M"], r


def test_floor_margin_at_minus30_matches_band_decays_own_end_margin():
    """`floor_margins` re-derives what `cymbal_bands.band_decay` calls `end_margin_db`; if the
    two ever disagree, this module's added -10 dB guard is measuring something else than the
    frozen instrument's own -30 dB guard, and the two numbers must not be read side by side."""
    x = cb._bp(strike(tau_m=0.30), SR, *cm.MID["M"])
    assert cm.floor_margins(x, SR)["margin30_db"] == pytest.approx(cb.band_decay(x, SR)["end_margin_db"], abs=0.02)


# --------------------------------------------------------------------------- controls that must fail

def test_control_leakage_only_band_refuses_and_its_pair_with_real_content_answers():
    """CONTROL 1, paired. NEGATIVE: remove M's own content (`a_m=0.0`) and leave only the
    3.45 kHz Q6 low band's skirt reaching into M. Nothing in M is then its own, so the
    measurement MUST refuse -- if it answered, every M number in the scorecard could be the low
    band's envelope wearing M's name. POSITIVE, the same record with M content restored: it must
    answer, and read M's planted decay, which is what makes the refusal above discriminating
    rather than a blanket."""
    neg = measured(a_m=0.0, tau_l=0.35)["M"]
    assert neg["t20_late_ms"] is None and "refused" in neg, neg
    assert "skirt leakage" in neg["refused"], neg["refused"]
    assert neg["over_leak_edt_db"] < cm.LEAK_MARGIN_DB, neg["over_leak_edt_db"]

    weak = measured(a_m=1.0, tau_m=0.30, tau_l=0.35)["M"]
    assert "refused" in weak, weak            # 4.0 dB of margin: still not separable

    pos = measured(a_m=4.0, tau_m=0.30, tau_l=0.35)["M"]
    assert "refused" not in pos, pos.get("refused")
    assert pos["over_leak_edt_db"] >= cm.LEAK_MARGIN_DB
    assert pos["t20_qualified_ms"] == pytest.approx(1e3 * t20_of(0.30), rel=0.08), pos


def test_control_a_leaky_band_reads_the_low_bands_decay_which_is_what_the_refusal_prevents():
    """CONTROL 1b: show the failure the leakage refusal exists to prevent is REAL, not
    hypothetical. With M's own content removed, whatever EDT10 the Schroeder estimator computes
    over M tracks the LOW band's planted decay -- so an unguarded M would have reported the low
    band's envelope. Computed with the guard bypassed (`cymbal_bands.band_decay` directly on the
    M-filtered signal), which is exactly what this module refuses to do."""
    short = cb.band_decay(cb._bp(strike(a_m=0.0, tau_l=0.10), SR, *cm.MID["M"]), SR)["edt10_ms"]
    long_ = cb.band_decay(cb._bp(strike(a_m=0.0, tau_l=0.60), SR, *cm.MID["M"]), SR)["edt10_ms"]
    assert long_ > 2.0 * short, (short, long_)


def test_control_a_tail_that_passes_the_leak_margin_can_still_be_biased():
    """CONTROL 1c, and the wrong-then-right record for this module: a planted 345 ms M tail whose
    late window clears the 6 dB leak margin (7.9 dB) is read by the Schroeder estimator as ~497 ms
    -- +44 %, the slower low band's decay pulling M's. The preconditions PASS here. The only thing
    that catches it is the second estimator, which reads ~344 ms, and the 25 % disagreement bound.
    So `t20_qualified_ms` must be None while `t20_late_ms` is not: the number exists, is wrong,
    and is not quotable. If this test ever passes with a qualified value, the cross-estimator gate
    has stopped working and every M tail in the scorecard needs re-reading."""
    r = measured(tau_m=0.15, a_m=4.0, tau_l=0.35)["M"]
    assert "refused" not in r, r.get("refused")
    assert r["over_leak_late_db"] >= cm.LEAK_MARGIN_DB, r["over_leak_late_db"]
    assert r["t20_late_ms"] > 1.25 * 1e3 * t20_of(0.15), r["t20_late_ms"]
    assert r["t20_qualified_ms"] is None, r
    assert "disagree" in r["t20_refused"], r["t20_refused"]


def test_control_a_raised_noise_floor_refuses_and_a_low_floor_does_not():
    """CONTROL 2, paired. NEGATIVE: a wide-band floor 12 dB below peak, so M's envelope is within
    1.2 dB of its floor by the -10 dB point -- MUST refuse, and specifically on the floor, which
    is the guard `cymbal_bands` does not have. POSITIVE: the same strike with the floor at -90 dB,
    which must answer and still read the planted decay. Without the pair, "it refuses" could just
    mean the tool refuses everything.

    MIDDLE, and the reason the guard is worth having: at a -40 dB floor the frozen instrument's
    own truncation guard refuses the late T20 (10.7 dB of end margin against its 15 dB bound) but
    still reports an EDT10 -- correctly, because at that floor EDT10 has 34 dB of margin. The two
    guards cover different quantities and neither subsumes the other."""
    neg = measured(tau_m=0.30, a_l=0.0, floor_db=-12.0)["M"]
    assert neg["t20_late_ms"] is None and "refused" in neg, neg
    assert "noise floor" in neg["refused"], neg["refused"]

    mid = measured(tau_m=0.30, a_l=0.0, floor_db=-40.0)["M"]
    assert mid["t20_late_ms"] is None and "refused" not in mid, mid
    assert mid["edt10_qualified_ms"] is not None and mid["margin10_db"] > cm.FLOOR_MARGIN_DB, mid

    pos = measured(tau_m=0.30, a_l=0.0, floor_db=-90.0)["M"]
    assert "refused" not in pos, pos.get("refused")
    assert pos["t20_qualified_ms"] == pytest.approx(1e3 * t20_of(0.30), rel=0.08), pos


def test_two_window_estimator_refuses_a_record_that_is_too_short():
    x = cb._bp(strike(tau_m=0.60), SR, *cm.MID["M"])
    r = cm.two_window_t20(x[: int(0.20 * SR)], SR)
    assert r["t20_ms"] is None and "refused" in r, r


def test_on_a_noiseless_tone_both_estimators_are_near_exact():
    """The one case with no beating in it: a decaying 1.3 kHz tone. Here the estimators have no
    scatter to hide behind, so the bound is 3 %, not the 6-15 % the noise-band cases need. If a
    future change breaks the ARITHMETIC rather than the robustness, this is the test that moves."""
    x = cb._bp(decaying_tone(0.30), SR, *cm.MID["M"])
    assert cb.band_decay(x, SR)["t20_late_ms"] == pytest.approx(1e3 * t20_of(0.30), rel=0.03)
    assert cm.two_window_t20(x, SR)["t20_ms"] == pytest.approx(1e3 * t20_of(0.30), rel=0.03)


def test_two_window_estimator_refuses_when_its_own_resolution_runs_out():
    """The estimator stating its own limit: a window pair across which the power barely falls
    gives tau = 2W / ln(ratio) with ln(ratio) -> 0, where its scatter swamps the answer. A fixed
    150 ms window reported 5,399 ms for a planted 1,382 ms on one seed this way. Forced here on
    the noiseless tone, where ln(ratio) = 2W/tau is analytic: asking for a 1.5 dB span sets
    W = 0.173 tau and so ln(ratio) = 0.35, below XCHECK_MIN_LN = 0.8."""
    x = cb._bp(decaying_tone(0.30), SR, *cm.MID["M"])
    r = cm.two_window_t20(x, SR, span_db=1.5)
    assert r["t20_ms"] is None and "scatter" in r["refused"], r
    assert 0.2 < r["ln_ratio"] < cm.XCHECK_MIN_LN, r


def test_boxcar_same_matches_numpy_convolve():
    """`_boxcar_same` exists only to be the O(n) twin of what `cymbal_bands.band_decay` computes
    with np.convolve. If the alignment differs, this module's floor margins are taken at different
    samples than the frozen instrument's and the two must not be compared."""
    rng = np.random.default_rng(5)
    for k in (3, 4, 2400):
        p = rng.standard_normal(9001) ** 2
        assert np.allclose(cm._boxcar_same(p, k), np.convolve(p, np.ones(k) / k, mode="same"),
                           rtol=0, atol=1e-12), k
