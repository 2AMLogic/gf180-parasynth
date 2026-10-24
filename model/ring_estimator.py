"""The sustained-ring frequency estimator, shared by `ladder_headroom` and
`res_tuning` so there is ONE policy for what counts as a measurement of a free
ring (issue #548).

It lives here, importing neither investigation, because `res_tuning` imports
`ladder_headroom` and the reverse import would be a cycle. The definitions are
moved from `res_tuning` unchanged: thresholds, the sustain classifier and the
refusal-based frequency policy keep the validated domain they were qualified on
(`res_tuning.qualify_estimator`, `model/test_res_tuning.py`).
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "audition"))

import audio_measure as am                                          # noqa: E402
from dsp import SR                                                  # noqa: E402

# ---- the estimator's thresholds (plan.json "estimator") ---------------------
MIN_CYCLES = 10
AGREE_REL = 0.005
SUSTAIN_FLOOR = 0.01                # last-quarter peak, full scale: a ring, not a dead band
SUSTAIN_DROP_DB = -1.0              # last vs first quarter peak


class Refused(RuntimeError):
    """A precondition of the measurement is unmet, so nothing was measured."""


def _finite(y) -> np.ndarray:
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0 or not np.all(np.isfinite(y)):
        raise Refused("the record is empty or holds non-finite samples")
    return y


def is_sustained(y) -> bool:
    """Whether a free-ring record holds a SUSTAINED oscillation: the last
    quarter's peak stands above `SUSTAIN_FLOOR` and has not fallen more than
    `SUSTAIN_DROP_DB` below the first quarter's. A growing ring is sustained.

    Two below-onset shapes it has to reject, both measured on the shipped
    filter: an ordinary decay (30 Hz, res 0.97: -4.2 dB across the window), and
    the fixed-point DEAD-BAND limit cycle the decay ends in -- 1131 Hz at res
    0.97 holds a perfectly steady 6e-4 full scale (20 LSB), which a ratio test
    alone would call sustained. The level floor is what rejects it.

    Defeating input, stated (verification-rules rule 8): a decay slow enough to
    lose under 1 dB across the window while staying above 0.01 -- a resonance a
    hair below the onset, at a low cutoff. Not constructed on the plan's grids:
    its below-onset point is res 0.97, where the slowest measured decay is
    -4.2 dB. The test suite pins that number so the margin is visible."""
    y = _finite(y)
    q = len(y) // 4
    if q < 1:
        raise Refused("record too short to compare its quarters")
    first, last = float(np.abs(y[:q]).max()), float(np.abs(y[-q:]).max())
    if last < SUSTAIN_FLOOR:
        return False
    return 20.0 * math.log10(last / max(first, 1e-300)) >= SUSTAIN_DROP_DB


def ring_frequency(y, sr: int = SR, *, min_cycles: int = MIN_CYCLES,
                   agree_rel: float = AGREE_REL) -> float:
    """The frequency of a sustained free ring, in Hz, or `Refused`.

    The value is the interpolated zero-crossing frequency, which is immune to
    the envelope smearing a growing ring puts on a spectrum; it is accepted only
    when the spectral peak agrees within `agree_rel`. A disagreement is
    AMBIGUOUS and refused, never resolved by picking one: two estimators of one
    quantity that disagree are not a measurement of it."""
    y = _finite(y)
    if am.is_silent(y, 1e-6):
        raise Refused("silent record")
    if not is_sustained(y):
        raise Refused("not a sustained ring (decaying, or a dead-band limit cycle)")
    z = am.zero_crossing_frequency(y, sr, min_crossings=min_cycles + 1)
    if not z.ok:
        raise Refused(f"insufficient duration: {z.reason} ({z.detail})")
    cycles = z.value * len(y) / sr
    if cycles < min_cycles:
        raise Refused(f"insufficient duration: {cycles:.1f} cycles, need {min_cycles}")
    e = am.dominant_frequency(y, 0.5 * z.value, min(1.5 * z.value, 0.49 * sr), sr)
    if not e.ok:
        raise Refused(f"no spectral line to confirm {z.value:.2f} Hz: {e.reason}")
    if abs(z.value - e.value) / z.value > agree_rel:
        raise Refused(f"ambiguous: zero crossings say {z.value:.3f} Hz, "
                      f"the spectral peak {e.value:.3f} Hz")
    return float(z.value)
