"""Reproduce the alias table's note-28 reading and check its replacement meter."""
import math

import numpy as np
import pytest

import voice_fx_sweep as sweep
from audio_measure import inharmonic_fraction_db


def test_note_28_compares_the_same_record(monkeypatch):
    estimates = []
    original = sweep.inharmonic_db

    def compare(samples, f0):
        estimates.append(inharmonic_fraction_db(samples, f0, sweep.SR))
        return original(samples, f0)

    monkeypatch.setattr(sweep, "inharmonic_db", compare)
    reported = sweep.saw_alias(28, "fixed")
    (estimate,) = estimates
    assert reported == pytest.approx(-53.9, abs=0.05)
    assert not estimate.ok and estimate.value is None
    assert estimate.reason == "harmonic guards cover the spectrum"
    assert estimate.detail["covered"] > 0.5
    print(f"note 28: {reported:.1f} dB; {estimate.reason} ({estimate.detail['covered']:.2%})")


def test_in_band_measurement_recovers_known_inharmonic_power():
    t = np.arange(sweep.SR // 2) / sweep.SR
    # Both tones land on FFT bins. 1500 Hz is halfway between two harmonics.
    x = np.sin(2 * np.pi * 1000 * t) + 0.1 * np.sin(2 * np.pi * 1500 * t)
    expected = 10 * math.log10(0.1 ** 2 / (1 + 0.1 ** 2))
    estimate = inharmonic_fraction_db(x, 1000, sweep.SR)
    assert estimate.require() == pytest.approx(expected, abs=0.01)
    print(f"planted tone: {estimate.value:.3f} dB; expected {expected:.3f} dB")
