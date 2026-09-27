"""Known answers for tools/cymbal_bands.py: synthetic three-band strikes with
planted energy shares and time constants, independent of any drum model."""
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.signal import butter, lfilter, sosfilt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_bands as cb  # noqa: E402

SR = 48_000


def _band_noise(lo, hi, n, seed):
    rng = np.random.default_rng(seed)
    return sosfilt(butter(6, [lo, hi], btype="bandpass", fs=SR, output="sos"), rng.standard_normal(n))


def _q6_skirt_noise(f0, q, n, seed, fs=SR):
    """The 808's own band-pass shape: a 2-pole/2-zero constant-skirt-gain
    band-pass (RBJ cookbook form), NOT a steep synthetic window. This is the
    same analytic filter `tools/cymbal_bands.py`'s BANDS comment cites for the
    shared 7.1 kHz Q~6 filter's skirt ("~17 dB down [at 4.1 kHz], at 5 kHz only
    ~13 dB") -- both figures reproduce to within 0.2 dB off this exact biquad,
    so it is the right stand-in for "the 808's Q6 skirt", where the steep
    6th-order 7-12 kHz window used by every OTHER test below has essentially
    none (#376)."""
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    b = [q * alpha, 0.0, -q * alpha]
    a = [1 + alpha, -2 * math.cos(w0), 1 - alpha]
    b = [c / a[0] for c in b]
    a = [c / a[0] for c in a]
    x = np.random.default_rng(seed).standard_normal(n)
    return lfilter(b, a, x)


def strike(tau_l=0.10, tau_hs=0.012, tau_hd=0.20, a_l=1.0, a_hs=1.0, a_hd=0.3, dur=3.0, seed=1,
           hi_shape="wide"):
    """Low band 2.6-4.4 kHz (one exponential), high band as the sum of a SHORT
    and a DECAY exponential -- the 808's structure, with every constant known.
    `hi_shape="wide"` (every test below except the crosstalk control) is a
    steep 6th-order 7-12 kHz window with no meaningful skirt below ~6 kHz --
    fine for reading the high band's own decay, wrong for a control that is
    specifically about how much of the high band leaks into the low band's
    2.9-4.1 kHz peak (Ln). `hi_shape="q6"` uses the 808's actual 7.1 kHz Q6
    band-pass shape (`_q6_skirt_noise`) instead."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    low = a_l * _band_noise(2600, 4400, n, seed) * np.exp(-t / tau_l)
    if hi_shape == "q6":
        hn = _q6_skirt_noise(7100.0, 6.0, n, seed + 1)
    else:
        hn = _band_noise(7000, 12000, n, seed + 1)
    high = hn * (a_hs * np.exp(-t / tau_hs) + a_hd * np.exp(-t / tau_hd))
    y = low + high
    y /= np.max(np.abs(y))
    return cb.rc.prepare(np.concatenate([np.zeros(SR // 20), y]), SR, side="synthetic")


def t20(tau):          # an exponential amplitude envelope falls 8.686/tau dB per second
    return 20.0 * tau / (20 * math.log10(math.e))


def test_single_exponential_bands_read_their_planted_t20():
    r = cb.measure(strike(tau_l=0.10, a_hs=0.0, a_hd=1.0, tau_hd=0.20), SR)
    assert r["L"]["t20_late_ms"] == pytest.approx(1e3 * t20(0.10), rel=0.05)
    assert r["H"]["t20_late_ms"] == pytest.approx(1e3 * t20(0.20), rel=0.05)


def test_two_slope_high_band_edt_is_short_and_late_t20_is_the_decay_band():
    r = cb.measure(strike(tau_hs=0.012, a_hs=1.0, tau_hd=0.20, a_hd=0.1), SR)
    assert r["H"]["edt10_ms"] < 1e3 * t20(0.20) / 2 * 0.5
    assert r["H"]["t20_late_ms"] == pytest.approx(1e3 * t20(0.20), rel=0.08)


def test_energy_share_tracks_a_planted_level_change():
    a = cb.measure(strike(a_l=1.0), SR)
    b = cb.measure(strike(a_l=0.5), SR)
    assert b["L"]["energy_share_db"] - a["L"]["energy_share_db"] < -4.0
    assert (b["H_minus_L_db"] - a["H_minus_L_db"]) == pytest.approx(6.02, abs=0.5)


def test_invariances_scale_and_prepended_silence():
    base = strike()
    a = cb.measure(base, SR)
    b = cb.measure(0.25 * base, SR)
    c = cb.measure(cb.rc.prepare(np.concatenate([np.zeros(SR // 3), base]), SR, side="padded"), SR)
    for band in ("L", "H"):
        for k in ("energy_share_db", "t20_late_ms"):
            assert b[band][k] == pytest.approx(a[band][k], rel=1e-6, abs=1e-6)
            assert c[band][k] == pytest.approx(a[band][k], rel=0.01, abs=0.05)


def test_swapped_decays_move_both_bands():
    a = cb.measure(strike(tau_l=0.10, tau_hd=0.30), SR)
    b = cb.measure(strike(tau_l=0.30, tau_hd=0.10), SR)
    assert b["L"]["t20_late_ms"] > 2 * a["L"]["t20_late_ms"]
    assert b["H"]["t20_late_ms"] < 0.5 * a["H"]["t20_late_ms"]


def test_truncated_record_refuses_rather_than_answers():
    y = strike(tau_l=0.10, tau_hd=0.60, dur=3.0)
    cut = y[: cb.rc.required_lead_samples(SR) + int(0.25 * SR)]
    r = cb.measure(cut, SR)
    assert r["H"]["t20_late_ms"] is None and r["H"]["t20_refused"]


def _skirt_growth(tau_l, hi_shape):
    """Hold the low band's own decay FIXED at `tau_l` and move only the high
    DECAY band's decay over the 808's range (late T20 ~250 -> ~1,090 ms, the
    range `cymbal_bands.fischer()` reads off the real recordings). Returns how
    much the narrow low band (Ln)'s EDT10 grows -- i.e. how much of that move
    could be explained by the high band's OWN skirt leaking into Ln, rather
    than the low band's own envelope actually moving."""
    lo = cb.measure(strike(tau_l=tau_l, tau_hd=0.25 / 2.303, a_hd=0.5, hi_shape=hi_shape), SR)
    hi = cb.measure(strike(tau_l=tau_l, tau_hd=1.09 / 2.303, a_hd=0.5, hi_shape=hi_shape), SR)
    return hi["Ln"]["edt10_ms"] / lo["Ln"]["edt10_ms"], lo["Ln"]["edt10_ms"], hi["Ln"]["edt10_ms"]


def test_skirt_leakage_alone_cannot_explain_the_low_band_tracking_decay():
    """The Judge's crosstalk control for the §10 contradiction (#371), tightened
    by #376: the first version built its high band with the steep, unrealistic
    `hi_shape="wide"` 6th-order 7-12 kHz window (`strike()`'s default, used by
    every OTHER test in this file), which leaks essentially nothing into Ln no
    matter how the high band's decay or level move -- the control could not
    fail. This uses `hi_shape="q6"`, the 808's actual 7.1 kHz Q6 skirt.

    POSITIVE case: the low band's own decay held at 0.35 s, the real 808's
    figure (`docs/tr808-reference.md` §10, "fixed, medium"; #376). At this
    realistic decay, Ln barely moves (< 1.25x) even though the high band's
    decay moves ~250 -> ~1,090 ms and the recordings' actual Ln move
    (~400 -> ~1,280 ms, ~3.2x) cannot be that leakage.

    NEGATIVE case, paired, and it MUST fail the same 1.25 bound: the same
    high-band sweep with the low band's decay held at an unrealistically
    short 0.10 s instead. Here the high band's tail lives many decay-constants
    past the low band's own -30 dB point, so its skirt genuinely does drag Ln's
    late reading up. Both cases must be shown -- the positive case is only
    evidence if the control can also fail."""
    grow_real, lo_real, hi_real = _skirt_growth(0.35, "q6")
    assert grow_real < 1.25, (lo_real, hi_real, grow_real)

    grow_short, lo_short, hi_short = _skirt_growth(0.10, "q6")
    assert grow_short >= 1.25, (
        "the paired negative case must fail this bound -- if it doesn't, the "
        "control isn't sensitive to skirt leakage at all", lo_short, hi_short, grow_short)


def test_hp3_numerator_is_the_third_difference():
    """The candidate bank's HP3 code (model/cymbal_candidate.py, not the shared decode): a mode with zero poles is its numerator, so an
    impulse must come out as (1 - z^-1)^3 = 1, -3, 3, -1 (times the state scale)."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "model"))
    import cymbal_candidate as cc
    b = cc.ModalFxHP3(modes=1, nums=1, headroom=0, out_bits=28)
    y = [b.step([v], [(0, 0, 65535)], num=[cc.HP3]) for v in (1000, 0, 0, 0, 0)]
    # amp 65535/65536 floors each output by at most one LSB
    assert all(abs(v - w * 1000) <= 1 for v, w in zip(y, (1, -3, 3, -1, 0))), y
