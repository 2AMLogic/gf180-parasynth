"""Ground-truth tests for the M5A waveform-level envelope measurement."""
from __future__ import annotations

import numpy as np

from tools.measure_mono_m5a_reference import envelope_timing


SR = 48_000


def test_envelope_timing_matches_a_piecewise_linear_attack_and_exponential_release():
    on_s, off_s, attack_s, tau_s = 0.1, 0.7, 0.05, 0.1
    t = np.arange(int(2.0 * SR)) / SR
    env = np.zeros_like(t)
    attack = (t >= on_s) & (t < on_s + attack_s)
    env[attack] = (t[attack] - on_s) / attack_s
    sustain = (t >= on_s + attack_s) & (t < off_s)
    env[sustain] = 1.0
    release = t >= off_s
    env[release] = np.exp(-(t[release] - off_s) / tau_s)

    got = envelope_timing(env, SR, on_s, off_s)
    assert got["valid"]
    assert abs(got["attack_10_90_ms"] - 40.0) <= 1.0
    assert abs(got["release_t20_ms"] - tau_s * np.log(10) * 1000) <= 1.0
    assert got["release_complete_40db"]


def test_envelope_timing_refuses_silence_and_incomplete_release():
    silence = envelope_timing(np.zeros(1000), SR, 0.002, 0.010)
    assert silence == {"valid": False, "why": "silent during gate"}

    # A held signal with no post-gate decay cannot be reported as a release.
    held = np.ones(SR)
    got = envelope_timing(held, SR, 0.1, 0.5)
    assert not got["valid"]
    assert got["why"] == "20 dB release threshold not reached"
