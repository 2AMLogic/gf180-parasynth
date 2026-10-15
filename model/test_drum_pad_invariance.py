"""Padding invariance of the drum-facing whole-clip-mean sites (#617).

A statistic that moves with the silence a clip carries can report a sound
difference that is only a padding difference. Each site is padded with 0.5 s
of leading and 5 s of trailing digital silence and must not move on the
original-support samples. Each check also runs against an injected
whole-clip-mean defect (the form `MeanSubtract` in tools/probes/residual_dc.py
keeps as a permanent control) and must turn RED there, so a green real site
means the check can fail.

Sites: `drum_verify.read_wav` and `discrimination_features.dominant_period`.
Run only this file on a shared host.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discrimination_features as df                                # noqa: E402
import drum_verify as dv                                            # noqa: E402

SR = 48000
LEAD, TAIL = int(0.5 * SR), int(5.0 * SR)
TOL = 1e-9


def _clip(offset: float) -> np.ndarray:
    """Decaying partials at the 808 oscillator frequencies plus a standing offset."""
    t = np.arange(int(0.6 * SR)) / SR
    parts = ((0.5, 205.3, 0.10), (0.3, 369.6, 0.08), (0.2, 522.7, 0.05))
    return sum(a * np.exp(-t / tau) * np.sin(2 * np.pi * f * t) for a, f, tau in parts) + offset


def _padded(x, lead=LEAD, tail=TAIL):
    return np.concatenate([np.zeros(lead), x, np.zeros(tail)])


def _read_whole_mean(path):
    """The injected defect: the pre-#617 `read_wav` conditioning."""
    sr, x = wavfile.read(path)
    x = np.asarray(x, np.float64)
    x = x - x.mean()
    pk = np.abs(x).max()
    return sr, x / pk


def _read_movement(reader, tmp_path, x, lead, tail):
    pa, pb = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    wavfile.write(pa, SR, x.astype(np.float32))
    wavfile.write(pb, SR, _padded(x, lead, tail).astype(np.float32))
    a = reader(pa)[1]
    b = reader(pb)[1][lead:lead + len(x)]
    return float(np.abs(a - b).max())


CASES = [("both", LEAD, TAIL), ("lead_only", LEAD, 0), ("tail_only", 0, TAIL)]


@pytest.mark.parametrize("offset", [0.0, 0.05])
@pytest.mark.parametrize("name,lead,tail", CASES)
def test_read_wav_is_pad_invariant(tmp_path, offset, name, lead, tail):
    assert _read_movement(dv.read_wav, tmp_path, _clip(offset), lead, tail) < TOL


@pytest.mark.parametrize("name,lead,tail", CASES)
def test_read_wav_control_whole_mean_is_red(tmp_path, name, lead, tail):
    """Control: the whole-file mean defect moves the output when padded.
    Standing-offset clip, where the movement is large (~0.09 measured)."""
    assert _read_movement(_read_whole_mean, tmp_path, _clip(0.05), lead, tail) > 1e-3


def test_read_wav_still_removes_standing_offset(tmp_path):
    p = str(tmp_path / "o.wav")
    wavfile.write(p, SR, _clip(0.05).astype(np.float32))
    y = dv.read_wav(p)[1]
    assert abs(y.mean()) < 1e-6 and abs(y).max() == pytest.approx(1.0)


def _dp_whole_buffer(x, sr, band=df.JIT_BAND_HZ):
    """Injected defect: the pre-#617 `dominant_period`, whose cycle gate and mean
    are taken over the whole buffer including any padding."""
    x = np.asarray(x, float)
    if len(x) < 16 or not np.any(x):
        return 0
    lo, hi = int(sr / band[1]), int(sr / band[0])
    hi = min(hi, len(x) // df.JIT_MIN_CYCLES)
    if hi <= lo + 1:
        return 0
    n = 1 << int(np.ceil(np.log2(2 * len(x))))
    X = np.fft.rfft(x - x.mean(), n)
    r = np.fft.irfft(X * np.conj(X), n)[:hi + 1]
    return int(lo + np.argmax(r[lo:hi + 1])) if r[0] > 0 else 0


@pytest.mark.parametrize("offset", [0.0, 0.05])
@pytest.mark.parametrize("name,lead,tail", CASES)
def test_dominant_period_is_pad_invariant(offset, name, lead, tail):
    x = _clip(offset)
    base = df.dominant_period(x, SR)
    assert base > 0
    assert df.dominant_period(_padded(x, lead, tail), SR) == base


def _short():
    """10 ms: under JIT_MIN_CYCLES cycles of the 205 Hz partial, so the
    refusal gate is what is under test."""
    return _clip(0.0)[:int(0.01 * SR)]


@pytest.mark.parametrize("name,lead,tail", CASES)
def test_dominant_period_refusal_gate_is_pad_invariant(name, lead, tail):
    x = _short()
    assert df.dominant_period(_padded(x, lead, tail), SR) == df.dominant_period(x, SR)


def test_dominant_period_control_is_red():
    """Control: the whole-buffer form lets padding satisfy the cycle gate, so
    the short window's lag moves (measured 40 -> 234)."""
    x = _short()
    assert _dp_whole_buffer(_padded(x), SR) != _dp_whole_buffer(x, SR)
    # and the control agrees with the real site on unpadded input
    assert _dp_whole_buffer(x, SR) == df.dominant_period(x, SR)
