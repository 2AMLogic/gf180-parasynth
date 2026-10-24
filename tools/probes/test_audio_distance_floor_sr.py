#!/usr/bin/env python3
"""The sample rate must reach the mel filterbank in `audio_distance_floor` (#533).

    python3 -m pytest tools/probes/test_audio_distance_floor_sr.py -q

`distances()` once called `_mel_dac(a, b)` with no rate, so every Fischer
(44.1 kHz) reading used a 48 kHz filterbank. The injected-bug controls here
reproduce that defect and must be red against it.
"""
from __future__ import annotations

import inspect
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_distance_floor as adf                                    # noqa: E402


def _pair():
    rng = np.random.default_rng(533)
    x = rng.standard_normal(8192) * 0.3
    t = np.arange(8192) / 44100.0
    y = x + 0.2 * np.sin(2 * np.pi * 3000.0 * t)
    return x, y


def test_distances_uses_the_rate_it_is_given():
    x, y = _pair()
    d44 = adf.distances(x, y, 44100)["mel_dac"]
    assert d44 == pytest.approx(adf._mel_dac(x, y, 44100), rel=0, abs=0)
    assert d44 != adf._mel_dac(x, y, 48000)          # the rates are distinguishable here
    assert adf.distances(x, y, 48000)["mel_dac"] == adf._mel_dac(x, y, 48000)


def test_rate_has_no_default():
    for fn in (adf.distances, adf._mel_dac):
        assert inspect.signature(fn).parameters["sr"].default is inspect.Parameter.empty
    x, y = _pair()
    with pytest.raises(TypeError):
        adf.distances(x, y)                           # type: ignore[call-arg]


def test_control_dropped_sr_is_detected(monkeypatch):
    """Injected bug: distances() forgets to pass sr (the original defect)."""
    x, y = _pair()
    real = adf._mel_dac
    monkeypatch.setattr(adf, "_mel_dac", lambda a, b, sr=48000: real(a, b, 48000))
    with pytest.raises(AssertionError):
        test_distances_uses_the_rate_it_is_given()
