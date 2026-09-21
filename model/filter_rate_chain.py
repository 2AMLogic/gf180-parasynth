"""Offline reconstructed-input, oversampled ladder, filtered-output chain.

This is an experiment harness, not the production RTL model. `scipy.signal`
performs the specified zero-phase Kaiser rate conversion; the ladder itself
remains the fixed-point `LadderFx` arithmetic. The caller must build its g/k
ROMs at the same `factor` so the commanded cutoff and resonance compensation
are evaluated at the actual internal rate.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import firwin, resample_poly

import fixed
import voice_fx as vf


_KAISER_BETA = 8.6
_Q15_MIN, _Q15_MAX = -32768, 32767


def _factor(value: int) -> int:
    value = int(value)
    if value not in (2, 4):
        raise ValueError("rate-conversion experiment supports factors 2 and 4")
    return value


def reconstruct_q15(x_q15: np.ndarray, factor: int, *,
                    preserve_headroom: bool = False) -> np.ndarray:
    """Bandlimited-interpolate base-rate Q1.15 samples to SR*factor.

    By default retain the historical int16 saturation for audit reproduction.
    Candidate filter experiments can preserve the FIR overshoot in int32 while
    retaining Q1.15 scaling; it is internal headroom, not a wider normalized
    audio format.
    """
    factor = _factor(factor)
    x = np.asarray(x_q15)
    if x.ndim != 1 or not np.issubdtype(x.dtype, np.integer):
        raise ValueError("input must be a one-dimensional integer Q1.15 signal")
    if np.any(x < _Q15_MIN) or np.any(x > _Q15_MAX):
        raise ValueError("input contains values outside Q1.15")
    high = resample_poly(x.astype(np.float64) / 32768.0, factor, 1,
                         window=("kaiser", _KAISER_BETA))
    if not np.isfinite(high).all():
        raise ValueError("input reconstruction produced non-finite samples")
    quantized = np.rint(high * 32768.0)
    if preserve_headroom:
        if np.any(np.abs(quantized) > np.iinfo(np.int32).max):
            raise ValueError("input reconstruction exceeds safe int32 Q1.15 headroom")
        return quantized.astype(np.int32)
    return np.clip(quantized, _Q15_MIN, _Q15_MAX).astype(np.int16)


def decimate_q15(x_q15: np.ndarray, factor: int) -> np.ndarray:
    """Anti-alias and decimate an internal-rate Q1.15 signal to SR."""
    factor = _factor(factor)
    x = np.asarray(x_q15)
    if x.ndim != 1 or not np.issubdtype(x.dtype, np.integer):
        raise ValueError("input must be a one-dimensional integer signal")
    y = resample_poly(x.astype(np.float64) / 32768.0, 1, factor,
                      window=("kaiser", _KAISER_BETA))
    return np.clip(np.rint(y * 32768.0), _Q15_MIN, _Q15_MAX).astype(np.int16)


class CausalRateConverter:
    """Stateful, causal fixed-coefficient 2x/4x Q1.15 rate converter.

    FIR coefficients use the same Kaiser beta/cutoff/length as SciPy's default
    ``resample_poly`` filter. The converter retains FIR history across calls;
    its interpolation and decimation group delays add to 20 base-rate frames.
    Coefficients use Q2.30, and FIR accumulators use signed int64.
    """

    COEF_Q = 30

    def __init__(self, factor: int):
        self.factor = _factor(factor)
        taps_n = 20 * self.factor + 1
        prototype = firwin(taps_n, 1.0 / self.factor,
                           window=("kaiser", _KAISER_BETA))
        self._interp_taps = np.rint(prototype * self.factor * (1 << self.COEF_Q)).astype(np.int64)
        self._decim_taps = np.rint(prototype * (1 << self.COEF_Q)).astype(np.int64)
        self.latency_frames = ((taps_n - 1) // 2) * 2 // self.factor
        self.reset()

    def reset(self):
        self._interp_history = np.zeros(len(self._interp_taps) - 1, dtype=np.int64)
        self._decim_history = np.zeros(len(self._decim_taps) - 1, dtype=np.int64)
        self._decim_phase = 0
        self.last_decimation = None

    @staticmethod
    def _filter_chunk(samples: np.ndarray, taps: np.ndarray,
                      history: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if not len(samples):
            return np.empty(0, dtype=np.int64), history.copy()
        joined = np.concatenate((history, samples.astype(np.int64, copy=False)))
        full = np.convolve(joined, taps, mode="full")
        start = len(history)
        accum = full[start:start + len(samples)]
        filtered = np.right_shift(accum + (1 << (CausalRateConverter.COEF_Q - 1)),
                                  CausalRateConverter.COEF_Q)
        return filtered.astype(np.int64), joined[-len(history):].copy()

    def reconstruct(self, x_q15: np.ndarray) -> np.ndarray:
        x = np.asarray(x_q15)
        if x.ndim != 1 or not np.issubdtype(x.dtype, np.integer):
            raise ValueError("input must be a one-dimensional integer Q1.15 signal")
        if np.any(x < _Q15_MIN) or np.any(x > _Q15_MAX):
            raise ValueError("input contains values outside Q1.15")
        up = np.zeros(len(x) * self.factor, dtype=np.int64)
        up[::self.factor] = x
        high, self._interp_history = self._filter_chunk(
            up, self._interp_taps, self._interp_history)
        if np.any(np.abs(high) > np.iinfo(np.int32).max):
            raise ValueError("causal reconstruction exceeds safe int32 Q1.15 headroom")
        return high.astype(np.int32)

    def decimate(self, x_high_q15: np.ndarray, *, output_bits: int = 16) -> np.ndarray:
        x = np.asarray(x_high_q15)
        if x.ndim != 1 or not np.issubdtype(x.dtype, np.integer):
            raise ValueError("high-rate input must be a one-dimensional integer signal")
        output_bits = int(output_bits)
        if not 2 <= output_bits <= 31:
            raise ValueError("decimator output width must be in 2..31 bits")
        filtered, self._decim_history = self._filter_chunk(
            x.astype(np.int64, copy=False), self._decim_taps, self._decim_history)
        keep = (self._decim_phase + np.arange(len(filtered))) % self.factor == 0
        self._decim_phase = (self._decim_phase + len(filtered)) % self.factor
        selected = filtered[keep]
        lo, hi = -(1 << (output_bits - 1)), (1 << (output_bits - 1)) - 1
        self.last_decimation = {
            "output_bits": output_bits,
            "would_clip_count": int(np.count_nonzero((selected < lo) | (selected > hi))),
            "would_clip_fraction": float(np.mean((selected < lo) | (selected > hi))) if len(selected) else 0.0,
            "max_abs_output": int(np.max(np.abs(selected))) if len(selected) else 0,
            "samples": int(len(selected)),
        }
        dtype = np.int16 if output_bits <= 16 else np.int32
        return np.clip(selected, lo, hi).astype(dtype)


class RateConvertedLadder:
    """VoiceFx-compatible adapter with input reconstruction and output FIR.

    `g_q16` and `k_q14` are base-frame ROM outputs generated for this adapter's
    internal rate, then held across its reconstructed subframes. Output words
    retain the ladder's configured Q(OB-16).15 range after low-pass decimation.
    """

    def __init__(self, factor: int, ladder_cfg: dict | None = None, *,
                 preserve_headroom: bool = False, causal: bool = False):
        self.factor = _factor(factor)
        self.preserve_headroom = bool(preserve_headroom)
        self.causal = bool(causal)
        self.converter = CausalRateConverter(self.factor) if self.causal else None
        self.latency_frames = self.converter.latency_frames if self.converter else 0
        self.last_reconstruction = None
        cfg = dict(vf.LADDER_CFG if ladder_cfg is None else ladder_cfg)
        cfg["oversample"] = 1
        self._ladder = fixed.LadderFx(**cfg)
        self.out_bits = self._ladder.OB

    def reset(self):
        self._ladder.reset()
        if self.converter is not None:
            self.converter.reset()

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

        if self.causal:
            raw_x_hi = self.converter.reconstruct(x)
        else:
            raw_x_hi = reconstruct_q15(x, self.factor, preserve_headroom=True)
        x_hi = (raw_x_hi if self.preserve_headroom else
                np.clip(raw_x_hi, _Q15_MIN, _Q15_MAX).astype(np.int16))
        self.last_reconstruction = {
            "preserved": self.preserve_headroom,
            "would_clip_count": int(np.count_nonzero(
                (raw_x_hi < _Q15_MIN) | (raw_x_hi > _Q15_MAX))),
            "would_clip_fraction": float(np.mean(
                (raw_x_hi < _Q15_MIN) | (raw_x_hi > _Q15_MAX))) if len(raw_x_hi) else 0.0,
            "max_abs_q15": int(np.max(np.abs(raw_x_hi.astype(np.int64)))) if len(raw_x_hi) else 0,
            "samples": int(len(raw_x_hi)),
        }
        g_hi = np.repeat(g, self.factor)
        k_hi = np.repeat(kvec, self.factor)
        y_hi = self._ladder.process(x_hi, None, res, drive, g_q16=g_hi,
                                    k=k, gain=gain, ogain=ogain, k_q14=k_hi)
        lo, hi = -(1 << (self.out_bits - 1)), (1 << (self.out_bits - 1)) - 1
        if self.causal:
            y = self.converter.decimate(y_hi, output_bits=self.out_bits)
            return np.clip(y, lo, hi).astype(np.int32)
        y_f = resample_poly(y_hi.astype(np.float64) / 32768.0, 1, self.factor,
                            window=("kaiser", _KAISER_BETA))
        return np.clip(np.rint(y_f * 32768.0), lo, hi).astype(np.int32)
