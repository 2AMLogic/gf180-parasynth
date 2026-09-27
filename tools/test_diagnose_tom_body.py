"""Known answers for tools/diagnose_tom_body.decompose: a synthetic strike whose
early transient, harmonic ring and inharmonic noise are planted at chosen
energies, independent of any drum model."""
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import diagnose_tom_body as d  # noqa: E402

SR = 48_000
F0, SPLIT = 135.0, 500.0


def _prep(y):
    """Through the scorer's own prepare(), as every real signal is."""
    return d.rc.prepare(np.concatenate([np.zeros(SR // 20), y]), SR)


def _strike(h3=0.0, click=0.0, noise=0.0, seed=1):
    t = np.arange(int(0.2 * SR)) / SR
    env = np.exp(-t / 0.058)
    y = np.sin(2 * math.pi * F0 * t) * env
    y += h3 * np.sin(2 * math.pi * 5 * F0 * t) * env              # 675 Hz, above split
    rng = np.random.default_rng(seed)
    if click:
        b = np.zeros_like(t)
        b[:240] = rng.standard_normal(240)
        from scipy.signal import butter, sosfilt
        y += click * sosfilt(butter(4, [800, 1800], btype="bandpass", fs=SR, output="sos"), b)
    if noise:
        from scipy.signal import butter, sosfilt
        nz = sosfilt(butter(4, [900, 1100], btype="bandpass", fs=SR, output="sos"),
                     rng.standard_normal(len(t)))
        y += noise * nz * env
    return _prep(y)


def test_pure_ring_has_no_late_above_split_energy():
    # a ring switched on at t=0 has an onset transient (measured -40.9 dB,
    # early); after 10 ms a pure decaying sine puts nothing above the split
    r = d.decompose(_strike(), SR, SPLIT, F0)
    assert r["early_db"] < -38
    assert all(r[k] is None or r[k] < -70 for k in ("harmonic_db", "other_db"))


def test_planted_harmonic_is_harmonic_at_its_level():
    r = d.decompose(_strike(h3=0.1), SR, SPLIT, F0)
    # (0.1)^2 of the ring's energy, times the late part's share of the window
    # (exp(-2 * 10 ms / 58 ms) = 0.71): -20 - 1.5 = -21.5 dB
    assert r["harmonic_db"] == pytest.approx(-21.5, abs=1.0)
    assert r["late_harmonic_share"] > 0.99 and r["other_db"] < -70


def test_planted_click_is_early_and_noise_is_other():
    c = d.decompose(_strike(click=3.0), SR, SPLIT, F0)
    assert c["early_share"] > 0.95 and c["early_db"] > -25
    n = d.decompose(_strike(noise=0.5), SR, SPLIT, F0)
    # noise lands on the masks only in proportion to their coverage
    assert abs(n["late_harmonic_share"] - n["mask_coverage"]) < 0.15 and n["other_db"] > -30


def test_scaling_does_not_move_fractions():
    a = d.decompose(_strike(h3=0.1, noise=0.05), SR, SPLIT, F0)
    b = d.decompose(0.25 * _strike(h3=0.1, noise=0.05), SR, SPLIT, F0)
    for k in ("early_db", "harmonic_db", "other_db"):
        assert a[k] == pytest.approx(b[k], abs=1e-6)


def test_broadband_noise_at_high_k_is_not_called_harmonic():
    # the regression that made conga noise read as "harmonic": with f0 185 Hz
    # the uncapped 6 % masks overlapped from k = 8 up
    t = np.arange(int(0.2 * SR)) / SR
    env = np.exp(-t / 0.077)
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(3)
    y = np.sin(2 * math.pi * 185 * t) * env + 0.5 * env * sosfilt(
        butter(4, [1200, 1900], btype="bandpass", fs=SR, output="sos"), rng.standard_normal(len(t)))
    r = d.decompose(_prep(y), SR, 700.0, 185.0)
    assert r["late_harmonic_share"] < r["mask_coverage"] + 0.15 and r["mask_coverage"] <= 0.41
