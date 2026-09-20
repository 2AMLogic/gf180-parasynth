"""Offline reconstructed-input, oversampled ladder, filtered-output chain.

This is an experiment harness, not the production RTL model. `scipy.signal`
performs the specified zero-phase Kaiser rate conversion; the ladder itself
remains the fixed-point `LadderFx` arithmetic. The caller must build its g/k
ROMs at the same `factor` so the commanded cutoff and resonance compensation
are evaluated at the actual internal rate.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly

import fixed
import voice_fx as vf


_KAISER_BETA = 8.6
_Q15_MIN, _Q15_MAX = -32768, 32767


def _factor(value: int) -> int:
    value = int(value)
    if value not in (2, 4):
        raise ValueError("rate-conversion experiment supports factors 2 and 4")
    return value


def reconstruct_q15(x_q15: np.ndarray, factor: int) -> np.ndarray:
    """Bandlimited-interpolate a base-rate Q1.15 signal to SR*factor."""
    factor = _factor(factor)
    x = np.asarray(x_q15)
    if x.ndim != 1 or not np.issubdtype(x.dtype, np.integer):
        raise ValueError("input must be a one-dimensional integer Q1.15 signal")
    if np.any(x < _Q15_MIN) or np.any(x > _Q15_MAX):
        raise ValueError("input contains values outside Q1.15")
    high = resample_poly(x.astype(np.float64) / 32768.0, factor, 1,
                         window=("kaiser", _KAISER_BETA))
    return np.clip(np.rint(high * 32768.0), _Q15_MIN, _Q15_MAX).astype(np.int16)


def decimate_q15(x_q15: np.ndarray, factor: int) -> np.ndarray:
    """Anti-alias and decimate an internal-rate Q1.15 signal to SR."""
    factor = _factor(factor)
    x = np.asarray(x_q15)
    if x.ndim != 1 or not np.issubdtype(x.dtype, np.integer):
        raise ValueError("input must be a one-dimensional integer signal")
    y = resample_poly(x.astype(np.float64) / 32768.0, 1, factor,
                      window=("kaiser", _KAISER_BETA))
    return np.clip(np.rint(y * 32768.0), _Q15_MIN, _Q15_MAX).astype(np.int16)


class RateConvertedLadder:
    """VoiceFx-compatible adapter with input reconstruction and output FIR.

    `g_q16` and `k_q14` are base-frame ROM outputs generated for this adapter's
    internal rate, then held across its reconstructed subframes. Output words
    retain the ladder's configured Q(OB-16).15 range after low-pass decimation.
    """

    def __init__(self, factor: int, ladder_cfg: dict | None = None):
        self.factor = _factor(factor)
        cfg = dict(vf.LADDER_CFG if ladder_cfg is None else ladder_cfg)
        cfg["oversample"] = 1
        self._ladder = fixed.LadderFx(**cfg)
        self.out_bits = self._ladder.OB

    def reset(self):
        self._ladder.reset()

    def process(self, x_q15: np.ndarray, cutoff_hz: np.ndarray, res: float,
                drive: float = 1.0, *, g_q16: np.ndarray = None,
                k: int = None, gain: int = None, ogain: int = None,
                k_q14: np.ndarray = None):
        x = np.asarray(x_q15, dtype=np.int16)
        n = len(x)
        if g_q16 is None or k_q14 is None:
            raise ValueError("rate-converted ladder requires rate-matched g_q16 and k_q14 ROM values")
        g = np.asarray(g_q16, dtype=np.int64)
        kvec = np.asarray(k_q14, dtype=np.int64)
        if g.shape != (n,) or kvec.shape != (n,):
            raise ValueError("g_q16 and k_q14 must have one rate-matched value per base frame")

        x_hi = reconstruct_q15(x, self.factor)
        g_hi = np.repeat(g, self.factor)
        k_hi = np.repeat(kvec, self.factor)
        y_hi = self._ladder.process(x_hi, None, res, drive, g_q16=g_hi,
                                    k=k, gain=gain, ogain=ogain, k_q14=k_hi)
        y_f = resample_poly(y_hi.astype(np.float64) / 32768.0, 1, self.factor,
                            window=("kaiser", _KAISER_BETA))
        lo, hi = -(1 << (self.out_bits - 1)), (1 << (self.out_bits - 1)) - 1
        return np.clip(np.rint(y_f * 32768.0), lo, hi).astype(np.int32)
