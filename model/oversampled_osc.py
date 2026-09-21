"""Reference 2x oscillator/decimator experiment for issue #61.

This is deliberately separate from :class:`voice_fx.VoiceFx` until the RTL
state machine carries the same sub-sample history.  It renders PolyBLEP at
96 kHz, then applies a 31-tap low-pass before retaining every second sample.
The integer taps sum to one in Q15 and are symmetric.
"""
from __future__ import annotations

import numpy as np

from voice_fx import OscFx, _DECIM2_TAPS as SR2_TAPS_Q15, _render_2x


def render_saw(n: int, inc: int) -> np.ndarray:
    """Render *n* base-rate Q1.15 samples through a true 2x path."""
    if n < 0 or inc <= 0:
        raise ValueError("n must be non-negative and inc must be positive")
    osc = OscFx("saw", smooth=False)
    out, _, _ = _render_2x(osc, n, np.full(n, inc, dtype=np.int64),
                           np.zeros(30, dtype=np.int64), 0)
    return out
