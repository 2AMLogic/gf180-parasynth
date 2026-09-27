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
    return y


def test_pure_ring_has_no_above_split_energy():
    r = d.decompose(_strike(), SR, SPLIT, F0)
    assert r["harmonic_db"] < -60 and r["other_db"] < -60


def test_planted_harmonic_is_harmonic_at_its_level():
    r = d.decompose(_strike(h3=0.1), SR, SPLIT, F0)
    assert r["harmonic_db"] == pytest.approx(-20.0 - 0.0, abs=1.5)   # (0.1)^2 of the ring, late part
    assert r["harmonic_share"] > 0.8


def test_planted_click_is_early_and_noise_is_other():
    c = d.decompose(_strike(click=0.3), SR, SPLIT, F0)
    assert c["early_share"] > 0.8
    n = d.decompose(_strike(noise=0.05), SR, SPLIT, F0)
    assert n["other_share"] > 0.7 and n["other_db"] > -50


def test_scaling_does_not_move_fractions():
    a = d.decompose(_strike(h3=0.1, noise=0.05), SR, SPLIT, F0)
    b = d.decompose(0.25 * _strike(h3=0.1, noise=0.05), SR, SPLIT, F0)
    for k in ("early_db", "harmonic_db", "other_db"):
        assert a[k] == pytest.approx(b[k], abs=1e-6)
