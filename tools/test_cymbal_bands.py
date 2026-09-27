"""Known answers for tools/cymbal_bands.py: synthetic three-band strikes with
planted energy shares and time constants, independent of any drum model."""
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_bands as cb  # noqa: E402

SR = 48_000


def _band_noise(lo, hi, n, seed):
    rng = np.random.default_rng(seed)
    return sosfilt(butter(6, [lo, hi], btype="bandpass", fs=SR, output="sos"), rng.standard_normal(n))


def strike(tau_l=0.10, tau_hs=0.012, tau_hd=0.20, a_l=1.0, a_hs=1.0, a_hd=0.3, dur=3.0, seed=1):
    """Low band 2.6-4.4 kHz (one exponential), high band 7-12 kHz as the sum of a
    SHORT and a DECAY exponential -- the 808's structure, with every constant known."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    low = a_l * _band_noise(2600, 4400, n, seed) * np.exp(-t / tau_l)
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


def test_skirt_leakage_alone_cannot_explain_the_low_band_tracking_decay():
    """The Judge's crosstalk control for the §10 contradiction. Hold the low
    band's own decay FIXED and move only the high DECAY band's decay over the
    808's range (late T20 ~250 -> ~1,090 ms): the narrow low band (Ln) must
    barely move. On the recordings Ln's EDT moves ~400 -> ~1,280 ms, so a
    change of that size cannot be the high band's skirt leaking into Ln."""
    lo = cb.measure(strike(tau_l=0.10, tau_hd=0.25 / 2.303, a_hd=0.5), SR)
    hi = cb.measure(strike(tau_l=0.10, tau_hd=1.09 / 2.303, a_hd=0.5), SR)
    grow = hi["Ln"]["edt10_ms"] / lo["Ln"]["edt10_ms"]
    assert grow < 1.25, (lo["Ln"]["edt10_ms"], hi["Ln"]["edt10_ms"])
