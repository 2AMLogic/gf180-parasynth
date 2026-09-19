"""Reference 2x oscillator/decimator experiment for issue #61.

This is deliberately separate from :class:`voice_fx.VoiceFx` until the RTL
state machine carries the same sub-sample history.  It renders PolyBLEP at
96 kHz, then applies a 31-tap low-pass before retaining every second sample.
The integer taps sum to one in Q15 and are symmetric.
"""
from __future__ import annotations

import numpy as np

from voice_fx import OscFx

SR2_TAPS_Q15 = np.array((
    39, 54, -44, -138, 34, 323, 72, -609, -397, 957, 1133,
    -1296, -2819, 1544, 10175, 14712, 10175, 1544, -2819,
    -1296, 1133, 957, -397, -609, 72, 323, 34, -138, -44, 54, 39,
), dtype=np.int64)
assert SR2_TAPS_Q15.sum() == 1 << 15


def render_saw(n: int, inc: int) -> np.ndarray:
    """Render *n* base-rate Q1.15 samples through a true 2x path."""
    if n < 0 or inc < 0:
        raise ValueError("n and inc must be non-negative")
    # The current phase register has integer increments.  Odd values round
    # down at the internal rate; the resulting pitch error is under 0.005
    # cents at the measured note and is reported by the measurement.
    hi = OscFx("saw", smooth=False).render(2 * n, inc // 2)
    filt = np.convolve(np.asarray(hi, dtype=np.int64), SR2_TAPS_Q15, mode="full")
    filt = (filt[: 2 * n] >> 15).astype(np.int64)
    return filt[1::2]
